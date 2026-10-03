"""
Admin WeChat Bridge — connects Admin's main Agent to iLink protocol.

Unlike the consumer bridge (which routes through consumer_agent),
this bridge uses the Admin's own agent with full capabilities.
"""

import base64
import asyncio
import json
import os
import uuid
import logging
import hashlib
import re

from app.channels.wechat.client import (
    ILinkClient, ILinkAPIError, ITEM_TEXT, ITEM_IMAGE, ITEM_VOICE, ensure_ilink_success,
)
from app.channels.wechat.media import b64_to_key
from app.services.conversations import (
    save_message,
    save_attachment, get_attachment_dir,
)
from app.core.security import get_user_filesystem_dir

log = logging.getLogger("wechat.admin_bridge")


async def handle_admin_wechat_message(session: dict, raw_msg: dict):
    """
    Entry point for admin WeChat messages.
    session is a dict with: user_id, conversation_id, client (ILinkClient instance).
    """
    client: ILinkClient = session["client"]
    user_id = session["user_id"]
    conv_id = session["conversation_id"]

    from_user = raw_msg.get("from_user_id", "")
    ctx_token = raw_msg.get("context_token", "")
    items = raw_msg.get("item_list", [])

    if ctx_token:
        session["context_token"] = ctx_token
        from app.channels.wechat.admin_router import _save_admin_session
        _save_admin_session(session["user_id"])

    from app.channels.wechat.rate_limiter import check_message_rate
    allowed, reason = check_message_rate(f"admin_{user_id}")
    if not allowed:
        log.warning("Rate limited admin %s: %s", user_id, reason)
        try:
            await client.send_text(from_user, reason, ctx_token)
        except Exception:
            pass
        return

    # Explicit case commands are business operations with a frozen recipient,
    # not free-form instructions to the administrator's full Agent.
    if await _handle_case_reply(session, raw_msg):
        return

    # The engine belongs to this conversation, not the user's current default.
    # QR-confirm binds new conversations; connections created before that change
    # remain on their existing DeepAgents path.
    from app.runtime.chat import conversation_binding
    try:
        conversation = conversation_binding(user_id, conv_id)
    except Exception:
        log.exception("Admin WeChat conversation unavailable (user=%s, conv=%s)", user_id, conv_id)
        await client.send_text(from_user, "微信会话不可用，请重新扫码建立会话。", ctx_token)
        return
    raw_binding = conversation.get("runtime_binding")
    bound = raw_binding or {}
    engine = bound.get("runtime") if isinstance(bound, dict) else None
    if raw_binding is not None and (not isinstance(raw_binding, dict) or engine not in ("deepagents", "codex", "cursor")):
        log.error("Unsupported admin WeChat runtime binding (user=%s, conv=%s)", user_id, conv_id)
        await client.send_text(from_user, "微信会话的引擎绑定无效，请重新扫码建立会话。", ctx_token)
        return
    runtime_bound = engine in ("codex", "cursor")
    if runtime_bound != bool(conversation.get("runtime_session_id")):
        log.error("Incomplete admin WeChat runtime binding (user=%s, conv=%s)", user_id, conv_id)
        await client.send_text(from_user, "微信会话的引擎连接不完整，请重新扫码建立会话。", ctx_token)
        return
    request_id = None
    if runtime_bound:
        request_id = _admin_runtime_request_id(session, raw_msg)
        if request_id is None:
            await client.send_text(from_user, "这条微信缺少消息编号，无法安全提交任务，请重新发送。", ctx_token)
            return
        from app.runtime.manager import get_runtime
        try:
            prior = get_runtime().store.find("run", actor_id=user_id, request_id=request_id)
        except Exception:
            log.exception("Admin WeChat runtime unavailable (user=%s)", user_id)
            await client.send_text(from_user, "当前引擎连接不可用，请稍后重试。", ctx_token)
            return
        if prior:
            # Resume only parts known to be pending. An in-flight send may have
            # succeeded remotely, so ambiguous parts are never blindly retried.
            try:
                await _run_admin_runtime_and_reply(
                    session, from_user, ctx_token, "", [], request_id, existing_run=prior[0],
                )
            except Exception:
                log.exception("Admin WeChat runtime replay failed (user=%s, run=%s)", user_id, prior[0]["id"])
                await client.send_text(from_user, "恢复任务结果时出错，请到网页会话查看。", ctx_token)
            return

    image_attachments = await _download_images(user_id, conv_id, client, items)
    voice_texts = await _transcribe_voices(user_id, client, items)

    user_text = _extract_user_text(items, voice_texts)
    if not user_text and not image_attachments:
        user_text = "[语音/非文字消息]"

    log.info("Admin WeChat msg from %s: %s", user_id, (user_text or "")[:60])

    try:
        await client.send_typing(ctx_token)
    except Exception:
        pass

    save_text = user_text
    if image_attachments:
        save_text += "\n" + "、".join(f"[图片:{a['filename']}]" for a in image_attachments)
    att_list = [{"type": a["type"], "filename": a["filename"], "path": a["path"]}
                for a in image_attachments] or None
    try:
        if runtime_bound:
            await _run_admin_runtime_and_reply(
                session, from_user, ctx_token, save_text, image_attachments, request_id,
            )
        else:
            image_full_paths = [a["full_path"] for a in image_attachments]
            user_content = _build_multimodal_content(user_text, image_full_paths)
            save_message(user_id, conv_id, "user", save_text, attachments=att_list)
            await _run_admin_agent_and_reply(session, from_user, ctx_token, user_content)
    except Exception:
        log.exception("Admin agent processing failed (user=%s)", user_id)
        try:
            await client.send_text(from_user, "处理消息时出错了，请稍后再试。", ctx_token)
        except Exception:
            pass


def _admin_runtime_request_id(session: dict, raw_msg: dict) -> str | None:
    msg_id = raw_msg.get("message_id")
    if not isinstance(msg_id, (str, int)) or not str(msg_id):
        return None
    identity = json.dumps(
        [session["user_id"], session["conversation_id"], str(msg_id)],
        separators=(",", ":"),
    )
    return "wechat-admin:" + hashlib.sha256(identity.encode()).hexdigest()


def _runtime_image_inputs(images: list[dict]) -> list[dict]:
    """Use stable names so a transport retry has the same input fingerprint."""
    mime_by_ext = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    }
    inputs = []
    for index, image in enumerate(images, 1):
        ext = os.path.splitext(image["filename"])[1].lower()
        mime = mime_by_ext.get(ext, "image/jpeg")
        with open(image["full_path"], "rb") as stream:
            encoded = base64.b64encode(stream.read()).decode()
        inputs.append({"name": f"image-{index}{ext or '.jpg'}", "data_url": f"data:{mime};base64,{encoded}"})
    return inputs


async def _run_admin_runtime_and_reply(
    session: dict, to_user: str, ctx_token: str, message: str,
    images: list[dict], request_id: str, *, existing_run: dict | None = None,
):
    """Run one bound Codex/Cursor turn and deliver its archived outputs to iLink."""
    from app.runtime.chat import enqueue_chat
    from app.runtime.manager import get_runtime
    from app.runtime.store import TERMINAL

    user_id, conv_id = session["user_id"], session["conversation_id"]
    runtime = get_runtime()
    if existing_run is None:
        attachments = _runtime_image_inputs(images)
        run = enqueue_chat(user_id, conv_id, request_id, message, attachments=attachments,
                           yolo=True, channel='wechat')
    else:
        run = existing_run
    rid = run["id"]
    delivery = runtime.store.get("wechat_admin_delivery", request_id)
    if delivery is None:
        delivery = {"id": request_id, "actor_id": user_id, "run_id": rid,
                    "status": "pending", "parts": []}
        runtime.store.put("wechat_admin_delivery", delivery)
    elif delivery["actor_id"] != user_id or delivery["run_id"] != rid:
        raise ValueError("微信投递记录与运行任务不匹配")
    while True:
        run = runtime.runs.own("run", rid, user_id)
        if run["status"] in TERMINAL:
            break
        await asyncio.sleep(.05)

    client: ILinkClient = session["client"]
    if not delivery["parts"]:
        from app.channels.wechat.delivery import extract_media_tags
        if run["status"] == "completed":
            cleaned_text, tagged_paths = extract_media_tags(run.get("output", ""))
            artifacts = run.get("artifacts", [])
            allowed = {item["path"] for item in artifacts}
            media_paths = list(dict.fromkeys(
                [path for path in tagged_paths if path in allowed]
                + [item["path"] for item in artifacts if item.get("native_image")]
            ))
        else:
            cleaned_text = run.get("error") or "任务未完成，请稍后重试。"
            media_paths = []
        if not cleaned_text and not media_paths:
            cleaned_text = "任务已完成。"
        delivery["parts"] = ([{"kind": "media", "value": path, "status": "pending"} for path in media_paths]
                             + ([{"kind": "text", "value": cleaned_text, "status": "pending"}] if cleaned_text else []))
        runtime.store.put("wechat_admin_delivery", delivery)

    for part in delivery["parts"]:
        if part["status"] == "sent":
            continue
        if part["status"] in ("sending", "unknown"):
            part["status"] = "unknown"
            delivery["status"] = "unknown"
            runtime.store.put("wechat_admin_delivery", delivery)
            log.warning("Admin WeChat delivery outcome unknown (run=%s)", rid)
            return
        part["status"] = "sending"
        delivery["status"] = "pending"
        runtime.store.put("wechat_admin_delivery", delivery)
        try:
            if part["kind"] == "media":
                await _send_media(user_id, client, to_user, ctx_token, part["value"], require_ack=True)
            else:
                ensure_ilink_success(await client.send_text(to_user, part["value"], ctx_token), require_ack=True)
        except ILinkAPIError as exc:
            part["status"] = "pending" if exc.explicit_rejection else "unknown"
            delivery["status"] = part["status"]
            runtime.store.put("wechat_admin_delivery", delivery)
            log.warning("Admin WeChat delivery rejected or unconfirmed (run=%s)", rid)
            return
        except FileNotFoundError:
            part["status"] = "pending"
            delivery["status"] = "pending"
            runtime.store.put("wechat_admin_delivery", delivery)
            log.exception("Admin WeChat archived media unavailable (run=%s)", rid)
            return
        except Exception:
            part["status"] = "unknown"
            delivery["status"] = "unknown"
            runtime.store.put("wechat_admin_delivery", delivery)
            log.exception("Admin WeChat delivery outcome unknown (run=%s)", rid)
            return
        part["status"] = "sent"
        runtime.store.put("wechat_admin_delivery", delivery)
    delivery["status"] = "sent"
    runtime.store.put("wechat_admin_delivery", delivery)


async def _handle_case_reply(session: dict, raw_msg: dict) -> bool:
    """Handle a text-only, explicitly addressed reply without invoking a model."""
    items = raw_msg.get('item_list', [])
    text = '\n'.join(item.get('text_item', {}).get('text', '') for item in items if item.get('type') == ITEM_TEXT).strip()
    if not re.match(r'^(?:回复|reply)\s+inbox_', text, re.IGNORECASE):
        return False
    to_user, ctx = raw_msg.get('from_user_id', ''), raw_msg.get('context_token', '')
    # A QR session is bound to the admin's sender. Do not acknowledge or act on
    # a different sender, even when the supplied case ID belongs to this admin.
    if not to_user or session.get('from_user_id') != to_user:
        log.warning('Rejected case reply from an unbound admin WeChat sender')
        return True
    match = re.fullmatch(r'(?:回复|reply)\s+(inbox_[A-Za-z0-9_-]+)\s*[:：]\s*(.+)', text, re.IGNORECASE | re.DOTALL)
    reply = '格式：回复 inbox_反馈编号：回复正文。只支持文字；也可到网页收件箱回复。'
    if match and all(item.get('type') == ITEM_TEXT for item in items):
        msg_id = raw_msg.get('message_id')
        if isinstance(msg_id, (str, int)) and str(msg_id):
            from app.services.inbox import reply_to_inbox
            from fastapi import HTTPException
            identity = json.dumps([session['user_id'], session['conversation_id'], str(msg_id)], separators=(',', ':'))
            request_key = 'wechat-case-reply:' + hashlib.sha256(identity.encode()).hexdigest()
            try:
                result = reply_to_inbox(session['user_id'], match.group(1), match.group(2).strip(), idempotency_key=request_key)
                reply = f"对反馈 {result['case']['id']} 的回复已记录并排队投递。请在收件箱查看投递状态；尚不代表对方已收到。"
            except (KeyError, PermissionError):
                reply = '无法回复：反馈不存在、无权限，或原收件对象已不可用。请在网页收件箱检查。'
            except (ValueError, HTTPException):
                reply = '无法回复：内容无效、消息已用于其他回复，或 Service/渠道已停用。请在网页收件箱检查。'
            except Exception:
                log.exception('Unable to queue admin WeChat case reply')
                reply = '回复暂未提交成功，请稍后重试或到网页收件箱检查。'
        else:
            # No fabricated idempotency key: protocol retries must not multiply
            # consumer replies when the transport omits its message identity.
            reply = '这条微信缺少消息编号，无法安全提交回复。请到网页收件箱回复。'
    try:
        await session['client'].send_text(to_user, reply, ctx)
    except Exception:
        log.exception('Failed to acknowledge admin WeChat case command')
    return True


def _detect_image_format(data: bytes) -> str:
    """Detect image format from magic bytes."""
    if data[:3] == b'\xff\xd8\xff':
        return ".jpg"
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return ".png"
    if data[:4] == b'GIF8':
        return ".gif"
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return ".webp"
    if data[:2] == b'BM':
        return ".bmp"
    log.warning("Unknown image format, magic=%s", data[:8].hex())
    return ".jpg"


def _b64_decode_flexible(s: str) -> bytes:
    """Decode base64 with support for standard and URL-safe alphabets."""
    for decoder in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            return decoder(s)
        except Exception:
            continue
    padded = s + "=" * (-len(s) % 4)
    for decoder in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            return decoder(padded)
        except Exception:
            continue
    raise ValueError(f"Cannot decode base64: {s[:40]}")


def _decode_aes_key(raw: str) -> bytes:
    """
    Decode AES key from one of three iLink formats:
      Format 1: direct hex (32 chars) → bytes.fromhex
      Format 2: base64(raw 16 bytes) → base64 decode → 16 bytes
      Format 3: base64(hex string)  → base64 decode → hex string → bytes.fromhex
    """
    if not raw:
        return b""
    if all(c in "0123456789abcdefABCDEF" for c in raw) and len(raw) == 32:
        return bytes.fromhex(raw)
    decoded = _b64_decode_flexible(raw)
    if len(decoded) == 16:
        return decoded
    try:
        hex_str = decoded.decode("ascii")
        if all(c in "0123456789abcdefABCDEF" for c in hex_str) and len(hex_str) == 32:
            return bytes.fromhex(hex_str)
    except (UnicodeDecodeError, ValueError):
        pass
    return decoded


def _resolve_aes_key(media_item: dict) -> bytes:
    """Resolve AES key from image_item or voice_item, checking media sub-object first."""
    media_obj = media_item.get("media", {})
    media_key = ""
    if isinstance(media_obj, dict):
        media_key = media_obj.get("aes_key") or media_obj.get("aeskey") or ""
    top_key = media_item.get("aeskey") or media_item.get("aes_key") or ""
    raw = media_key or top_key
    return _decode_aes_key(raw)


async def _download_images(user_id: str, conv_id: str,
                           client: ILinkClient, items: list) -> list[dict]:
    """Download and decrypt image items, save to query_appendix/.

    Returns list of attachment info dicts:
    {"type": "image", "filename": "...", "path": "images/...", "full_path": "..."}
    """
    saved = []
    for item in items:
        if item.get("type") != ITEM_IMAGE:
            continue
        image_item = item.get("image_item", {})
        aes_key_bytes = _resolve_aes_key(image_item)

        media_obj = image_item.get("media", {})
        encrypt_qp = media_obj.get("encrypt_query_param", "") if isinstance(media_obj, dict) else ""

        log.info("Image download: encrypt_qp=%s, aes_key=%d bytes",
                 "present" if encrypt_qp else "absent", len(aes_key_bytes))

        try:
            if encrypt_qp and aes_key_bytes:
                raw_bytes = await client.download_received_media(encrypt_qp, aes_key_bytes)
            else:
                log.warning("No encrypt_query_param, cannot download received image")
                continue

            ext = _detect_image_format(raw_bytes)
            filename = f"wx_{uuid.uuid4().hex[:8]}{ext}"
            rel = f"images/{filename}"
            save_attachment(user_id, conv_id, rel, raw_bytes)
            att_dir = get_attachment_dir(user_id, conv_id)
            full_path = os.path.join(att_dir, rel)
            saved.append({
                "type": "image",
                "filename": filename,
                "path": rel,
                "full_path": full_path,
            })
            log.info("Downloaded image to query_appendix: %s (%d bytes, magic=%s)",
                     filename, len(raw_bytes), raw_bytes[:4].hex())
        except Exception:
            log.exception("Failed to download image")
    return saved


async def _transcribe_voices(user_id: str, client: ILinkClient, items: list) -> list[str]:
    from app.storage import get_storage_service
    storage = get_storage_service()
    transcribed = []

    for item in items:
        if item.get("type") != ITEM_VOICE:
            continue
        voice_item = item.get("voice_item", {})
        aes_key_bytes = _resolve_aes_key(voice_item)
        media_obj = voice_item.get("media", {})
        encrypt_qp = media_obj.get("encrypt_query_param", "") if isinstance(media_obj, dict) else ""
        if not encrypt_qp or not aes_key_bytes:
            log.warning("No encrypt_query_param for voice, skipping")
            continue
        try:
            raw_bytes = await client.download_received_media(encrypt_qp, aes_key_bytes)
            filename = f"wx_voice_{uuid.uuid4().hex[:8]}.silk"
            rel_path = f"/generated/audio/{filename}"
            storage.write_bytes(user_id, rel_path, raw_bytes)

            text = await _whisper_transcribe(raw_bytes)
            if text:
                transcribed.append(text)
        except Exception:
            log.exception("Failed to process voice")
    return transcribed


def _silk_to_wav(silk_bytes: bytes) -> bytes:
    """Convert SILK audio bytes to WAV format for Whisper."""
    import io
    import struct
    try:
        import pysilk
    except ImportError:
        log.warning("pysilk not installed, returning raw data")
        return silk_bytes

    silk_input = io.BytesIO(silk_bytes)
    pcm_output = io.BytesIO()

    if silk_bytes[:1] == b'\x02':
        silk_input = io.BytesIO(silk_bytes[1:])

    pysilk.decode(silk_input, pcm_output, 24000)
    pcm_data = pcm_output.getvalue()

    wav_buf = io.BytesIO()
    sample_rate = 24000
    num_channels = 1
    bits_per_sample = 16
    data_size = len(pcm_data)
    byte_rate = sample_rate * num_channels * bits_per_sample // 8
    block_align = num_channels * bits_per_sample // 8

    wav_buf.write(b'RIFF')
    wav_buf.write(struct.pack('<I', 36 + data_size))
    wav_buf.write(b'WAVE')
    wav_buf.write(b'fmt ')
    wav_buf.write(struct.pack('<IHHIIHH', 16, 1, num_channels, sample_rate,
                              byte_rate, block_align, bits_per_sample))
    wav_buf.write(b'data')
    wav_buf.write(struct.pack('<I', data_size))
    wav_buf.write(pcm_data)

    return wav_buf.getvalue()


async def _whisper_transcribe(audio_bytes: bytes) -> str:
    """Transcribe audio bytes using Whisper. Handles SILK format."""
    try:
        import openai
        import tempfile
        client = openai.AsyncOpenAI()

        if audio_bytes[:9] == b'#!SILK_V3' or audio_bytes[:1] == b'\x02':
            log.info("Converting SILK to WAV for Whisper")
            wav_data = _silk_to_wav(audio_bytes)
            suffix = ".wav"
        else:
            wav_data = audio_bytes
            suffix = ".wav"

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(wav_data)
            tmp_path = tmp.name
        try:
            with open(tmp_path, "rb") as f:
                resp = await client.audio.transcriptions.create(
                    model="whisper-1", file=f, language="zh",
                )
            return resp.text
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    except ImportError as e:
        log.warning("Missing dependency for transcription: %s", e)
        return ""
    except Exception:
        log.exception("Whisper transcription failed")
        return ""


def _extract_user_text(items: list, voice_texts: list[str] = None) -> str:
    parts = []
    voice_idx = 0
    for item in items:
        t = item.get("type")
        if t == ITEM_TEXT:
            text = item.get("text_item", {}).get("text", "")
            if text:
                parts.append(text)
        elif t == ITEM_VOICE:
            if voice_texts and voice_idx < len(voice_texts) and voice_texts[voice_idx]:
                parts.append(f"[语音转文字] {voice_texts[voice_idx]}")
            else:
                parts.append("[语音消息]")
            voice_idx += 1
    return "\n".join(parts) if parts else ""


def _build_multimodal_content(text: str, image_paths: list[str]):
    """Build LangChain multimodal content list with text + base64 images."""
    if not image_paths:
        return text or "[空消息]"

    content = []
    content.append({
        "type": "text",
        "text": text if text else "用户发送了图片，请查看并描述或回应图片内容。",
    })

    for img_path in image_paths:
        try:
            with open(img_path, "rb") as f:
                raw = f.read()
            img_data = base64.b64encode(raw).decode()
            ext = os.path.splitext(img_path)[1].lower()
            mime = {
                ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".png": "image/png", ".gif": "image/gif",
                ".webp": "image/webp", ".bmp": "image/bmp",
            }.get(ext, "image/jpeg")
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{img_data}"},
            })
            log.info("Encoded image for LLM: %s, %s, %d raw bytes, %d b64 chars",
                     os.path.basename(img_path), mime, len(raw), len(img_data))
        except Exception:
            log.warning("Failed to encode image: %s", img_path)

    if len(content) == 1 and content[0]["type"] == "text":
        return content[0]["text"]
    return content


async def _run_admin_agent_and_reply(
    session: dict,
    to_user: str,
    ctx_token: str,
    user_content,
):
    """Run the admin's main agent and forward replies via iLink.

    Handles HITL interrupts by auto-approving file write/edit operations,
    since the WeChat interface has no approval UI.
    """
    from app.services.agent import create_user_agent
    from app.services.prompt import stamp_message
    from app.services.token_usage import build_usage_callbacks
    from app.services.project_context import (
        admin_conversation_scope, context_for_conversation,
    )
    from langgraph.types import Command

    user_id = session["user_id"]
    conv_id = session["conversation_id"]
    client: ILinkClient = session["client"]

    agent = create_user_agent(
        user_id,
        capabilities=["humanchat", "image", "speech"],
        project_brief=context_for_conversation(user_id, conv_id),
    )
    thread_id = f"{user_id}-{conv_id}"
    config = {
        "configurable": {"thread_id": thread_id},
        "callbacks": build_usage_callbacks(user_id, channel="wechat", conv_id=conv_id),
    }

    # Bracket the entire streaming + post-stream send/save section inside a
    # scheduled_inject.thread_active context so any L2 injections queued for
    # this thread wait until we're fully done (exception-safe via try/finally
    # inside the context manager).
    from app.services import scheduled_inject

    full_response = ""
    sent_via_tool = False
    tool_records = []

    stamped_content = stamp_message(user_content, user_id)
    input_payload = {"messages": [{"role": "user", "content": stamped_content}]}

    _MAX_HITL_LOOPS = 10

    async with (scheduled_inject.thread_active(thread_id, agent=agent),
                admin_conversation_scope(user_id, conv_id, channel="wechat")):
        for _loop_i in range(_MAX_HITL_LOOPS):
            async for event in agent.astream(
                input_payload,
                config=config,
                stream_mode="messages",
                subgraphs=True,
            ):
                if not isinstance(event, tuple) or len(event) != 2:
                    continue
                ns, chunk = event
                if isinstance(chunk, tuple) and len(chunk) == 2:
                    msg, metadata = chunk
                elif hasattr(ns, '__class__') and 'Message' in ns.__class__.__name__:
                    msg, metadata = ns, chunk
                    ns = ()
                else:
                    continue

                msg_type = msg.__class__.__name__

                if msg_type == "AIMessageChunk":
                    content = msg.content
                    if isinstance(content, str) and content:
                        full_response += content
                    elif isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                full_response += block.get("text", "")

                elif msg_type == "ToolMessage":
                    tool_name = getattr(msg, "name", "tool")
                    content = msg.content if isinstance(msg.content, str) else str(msg.content)
                    tool_records.append({"name": tool_name, "result": content[:500]})

                    if tool_name == "send_message":
                        sent_via_tool = True
                        try:
                            from app.channels.wechat.delivery import extract_media_tags
                            payload = json.loads(content)
                            text = payload.get("text", "")
                            media = payload.get("media")

                            # Agent 经常把生成的图片/音频以 <<FILE:...>> 标签嵌在 text 里
                            # （system prompt 引导的 web 端渲染格式），投递层主动解析转为媒体消息
                            cleaned_text, extra_media = extract_media_tags(text)
                            media_paths = ([media] if media else []) + extra_media

                            for mp in media_paths:
                                try:
                                    await _send_media(user_id, client, to_user, ctx_token, mp)
                                except Exception:
                                    log.exception("Failed to send admin media %s", mp)

                            if cleaned_text:
                                await client.send_text(to_user, cleaned_text, ctx_token)
                                log.info("Admin send_message: %s", cleaned_text[:50])
                        except Exception:
                            log.exception("Failed to send via iLink")

            # Check for HITL interrupts and auto-approve
            state = await agent.aget_state(config)
            has_interrupt = False
            if state and hasattr(state, "tasks") and state.tasks:
                for task in state.tasks:
                    if hasattr(task, "interrupts") and task.interrupts:
                        has_interrupt = True
                        break

            if not has_interrupt:
                break

            decisions = []
            for task in state.tasks:
                if hasattr(task, "interrupts") and task.interrupts:
                    for intr in task.interrupts:
                        val = intr.value if hasattr(intr, "value") else {}
                        if isinstance(val, dict) and "action_requests" in val:
                            for ar in val["action_requests"]:
                                # langchain HITL middleware expects {"type": "approve"} after upgrade
                                decisions.append({"type": "approve"})

            if not decisions:
                break

            log.info("WeChat admin bridge: auto-approving %d HITL actions (loop %d)",
                     len(decisions), _loop_i + 1)
            input_payload = Command(resume={"decisions": decisions})

        if not sent_via_tool and full_response.strip():
            await client.send_text(to_user, full_response.strip(), ctx_token)
            log.info("Admin direct response: %s", full_response[:50])

        save_message(
            user_id, conv_id, "assistant", full_response,
            tool_calls=tool_records if tool_records else None,
        )


_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
_TTS_CONVERTIBLE = {".mp3", ".wav", ".m4a", ".ogg", ".aac", ".flac"}


async def _send_media(
    user_id: str,
    client: ILinkClient,
    to_user: str,
    ctx_token: str,
    media_path: str,
    *, require_ack: bool = False,
):
    from app.storage import get_storage_service
    storage = get_storage_service()

    clean = media_path.lstrip("/").replace("\\", "/")
    if not clean.startswith("generated/"):
        clean = f"generated/{clean}"
    rel_path = f"/{clean}"

    if not storage.is_file(user_id, rel_path):
        log.warning("Media file not found: %s", rel_path)
        if require_ack:
            raise FileNotFoundError(rel_path)
        return

    file_bytes = storage.read_bytes(user_id, rel_path)
    filename = os.path.basename(clean)
    ext = os.path.splitext(filename)[1].lower()

    in_audio_dir = "/audio/" in clean

    if ext in _IMAGE_EXTS:
        result = await client.send_image(to_user, file_bytes, ctx_token, filename)
        log.info("Sent image to WeChat: %s (%d bytes)", filename, len(file_bytes))
    elif ext in _VIDEO_EXTS:
        result = await client.send_video(to_user, file_bytes, ctx_token)
        log.info("Sent video to WeChat: %s (%d bytes)", filename, len(file_bytes))
    elif ext == ".silk":
        result = await client.send_voice(to_user, file_bytes, ctx_token)
        log.info("Sent voice to WeChat: %s (%d bytes)", filename, len(file_bytes))
    elif in_audio_dir and ext in _TTS_CONVERTIBLE:
        result = await client.send_file(to_user, file_bytes, filename, ctx_token)
        log.info("Sent audio as file to WeChat: %s (%d bytes)", filename, len(file_bytes))
    else:
        result = await client.send_file(to_user, file_bytes, filename, ctx_token)
        log.info("Sent file to WeChat: %s (%d bytes)", filename, len(file_bytes))
    if require_ack:
        ensure_ilink_success(result, require_ack=True)
