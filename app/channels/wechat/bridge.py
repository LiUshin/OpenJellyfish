"""
WeChat Bridge — connects Consumer Agent to iLink protocol.

Receives iLink messages, routes them through the consumer agent,
captures send_message tool results, and forwards them back via iLink.
"""

import base64
import json
import os
import uuid
import logging

from app.channels.wechat.client import ILinkClient, ITEM_TEXT, ITEM_IMAGE, ITEM_VOICE
from app.channels.wechat.media import b64_to_key
from app.channels.wechat.session_manager import WeChatSession, get_session_manager
from app.services.published import (
    save_consumer_message, get_consumer_generated_dir,
    save_consumer_attachment, get_consumer_attachment_dir,
)
from app.services.consumer_agent import create_consumer_agent
from app.services.usage_log import record_request

log = logging.getLogger("wechat.bridge")


async def handle_wechat_message(session: WeChatSession, raw_msg: dict):
    """
    Entry point called by SessionManager when a message arrives.
    Routes to the consumer agent and sends replies via iLink.
    """
    manager = get_session_manager()
    client = manager.get_client(session.session_id)
    if not client:
        log.error("No iLink client for session %s", session.session_id)
        return

    from_user = raw_msg.get("from_user_id", "")
    ctx_token = raw_msg.get("context_token", "")
    items = raw_msg.get("item_list", [])

    if ctx_token:
        session.context_token = ctx_token

    log.info("Raw msg keys=%s, item_list len=%d, full=%s",
             list(raw_msg.keys()), len(items),
             json.dumps(raw_msg, ensure_ascii=False, default=str)[:800])

    image_attachments = await _download_images(session, client, items)
    voice_texts = await _transcribe_voices(session, client, items)

    user_text = _extract_user_text(items, voice_texts)
    if not user_text and not image_attachments:
        user_text = "[用户发送了一条非文字消息]"

    image_full_paths = [a["full_path"] for a in image_attachments]
    user_content = _build_multimodal_content(user_text, image_full_paths)

    log.info("Processing message from %s: %s", from_user[:20], (user_text or "")[:50])

    from app.channels.wechat.rate_limiter import check_message_rate
    allowed, reason = check_message_rate(session.session_id)
    if not allowed:
        log.warning("Rate limited session %s: %s", session.session_id, reason)
        try:
            await client.send_text(from_user, reason, ctx_token)
        except Exception:
            pass
        return

    try:
        await client.send_typing(ctx_token)
    except Exception:
        pass

    save_text = user_text
    if image_attachments:
        save_text += "\n" + "、".join(f"[图片:{a['filename']}]" for a in image_attachments)
    att_list = [{"type": a["type"], "filename": a["filename"], "path": a["path"]}
                for a in image_attachments] or None
    save_consumer_message(
        session.admin_id, session.service_id,
        session.conversation_id, "user", save_text,
        attachments=att_list,
    )

    import time as _time
    _start = _time.time()
    _ok = True
    try:
        await _run_agent_and_reply(session, client, from_user, ctx_token, user_content)
    except Exception:
        _ok = False
        log.exception("Agent processing failed (session=%s)", session.session_id)
        try:
            await client.send_text(from_user, "抱歉，处理消息时出错了，请稍后再试。", ctx_token)
        except Exception:
            pass
    finally:
        record_request(
            session.admin_id, session.service_id,
            channel="wechat", key_id="",
            conv_id=session.conversation_id,
            endpoint="wechat:on_message",
            status_code=200 if _ok else 500,
            latency_ms=int((_time.time() - _start) * 1000), ok=_ok,
        )


def _detect_image_format(data: bytes) -> str:
    """Detect image format from magic bytes, return file extension."""
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
    log.warning("Unknown image format, magic=%s, defaulting to .jpg", data[:8].hex())
    return ".jpg"


def _b64_decode_flexible(s: str) -> bytes:
    """Decode base64 with support for standard and URL-safe alphabets, with or without padding."""
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


async def _download_images(
    session: WeChatSession, client: ILinkClient, items: list
) -> list[dict]:
    """Download and decrypt image items, return list of attachment info dicts.

    Each dict: {"type": "image", "filename": "...", "path": "images/...", "full_path": "..."}
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
            save_consumer_attachment(
                session.admin_id, session.service_id, session.conversation_id,
                rel, raw_bytes,
            )
            att_dir = get_consumer_attachment_dir(
                session.admin_id, session.service_id, session.conversation_id,
            )
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


async def _transcribe_voices(
    session: WeChatSession, client: ILinkClient, items: list
) -> list[str]:
    """Download voice items, decrypt, transcribe via Whisper. Returns list of transcribed texts."""
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
            rel_path = f"/audio/{filename}"
            storage.write_consumer_bytes(
                session.admin_id, session.service_id, session.conversation_id,
                rel_path, raw_bytes,
            )
            log.info("Downloaded voice: %s (%d bytes)", filename, len(raw_bytes))

            text = await _whisper_transcribe(rel_path, raw_bytes)
            if text:
                transcribed.append(text)
                log.info("Transcribed voice: %s", text[:60])
        except Exception:
            log.exception("Failed to process voice message")
    return transcribed


def _silk_to_wav(silk_bytes: bytes) -> bytes:
    """Convert SILK audio bytes to WAV format for Whisper."""
    import io
    import struct
    try:
        import pysilk
    except ImportError:
        log.warning("pysilk not installed, trying raw SILK data")
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


async def _whisper_transcribe(filepath: str, audio_bytes: bytes) -> str:
    """Transcribe audio bytes using OpenAI Whisper API. Handles SILK format."""
    try:
        import openai
        client = openai.AsyncOpenAI()
        import tempfile

        ext = os.path.splitext(filepath)[1].lower()
        if ext == ".silk" or audio_bytes[:9] == b'#!SILK_V3' or audio_bytes[:1] == b'\x02':
            log.info("Converting SILK to WAV for Whisper")
            wav_data = _silk_to_wav(audio_bytes)
            suffix = ".wav"
        elif ext in (".mp3", ".wav", ".m4a", ".ogg", ".webm"):
            wav_data = audio_bytes
            suffix = ext
        else:
            wav_data = audio_bytes
            suffix = ".wav"

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(wav_data)
            tmp_path = tmp.name

        try:
            with open(tmp_path, "rb") as audio_file:
                resp = await client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    language="zh",
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
    """Extract text content from iLink message item_list."""
    parts = []
    voice_idx = 0
    for item in items:
        item_type = item.get("type")
        if item_type == ITEM_TEXT:
            text_item = item.get("text_item", {})
            text = text_item.get("text", "")
            if text:
                parts.append(text)
        elif item_type == ITEM_IMAGE:
            pass  # handled by _download_images
        elif item_type == ITEM_VOICE:
            if voice_texts and voice_idx < len(voice_texts) and voice_texts[voice_idx]:
                parts.append(f"[语音转文字] {voice_texts[voice_idx]}")
            else:
                parts.append("[语音消息，无法转录]")
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


async def _run_agent_and_reply(
    session: WeChatSession,
    client: ILinkClient,
    to_user: str,
    ctx_token: str,
    user_content,
):
    """
    Run the consumer agent in streaming mode.
    Capture send_message tool results and forward via iLink.
    If agent doesn't use send_message, forward its text output directly.

    Handles HITL interrupts by auto-approving, since WeChat has no approval UI.
    """
    if isinstance(user_content, list):
        types = [b.get("type") for b in user_content]
        sizes = [len(str(b.get("image_url", {}).get("url", "")))
                 if b.get("type") == "image_url" else 0 for b in user_content]
        log.info("Multimodal content: blocks=%s, image_data_sizes=%s", types, sizes)
    else:
        log.info("Text-only content: %d chars", len(str(user_content)))

    from app.services.prompt import stamp_message
    from langgraph.types import Command

    agent = create_consumer_agent(
        session.admin_id, session.service_id, session.conversation_id,
        wechat_session_id=session.session_id,
        channel="wechat",
    )
    thread_id = f"svc-{session.service_id}-{session.conversation_id}"
    config = {"configurable": {"thread_id": thread_id}}

    # Bracket the entire streaming + post-stream send/save section so any L2
    # scheduled-task injections queued for this thread wait until we finish.
    from app.services import scheduled_inject

    full_response = ""
    sent_via_tool = False
    tool_records = []
    cur_tool_name = None

    stamped_content = stamp_message(user_content, session.admin_id)
    input_payload = {"messages": [{"role": "user", "content": stamped_content}]}

    _MAX_HITL_LOOPS = 10

    async with scheduled_inject.thread_active(thread_id):
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
                    tool_call_chunks = getattr(msg, "tool_call_chunks", None) or []
                    tool_calls = getattr(msg, "tool_calls", None) or []
                    for tc in (tool_call_chunks or tool_calls):
                        name = tc.get("name", "")
                        if name:
                            cur_tool_name = name

                    content = msg.content
                    if isinstance(content, str) and content:
                        full_response += content
                    elif isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                text = block.get("text", "")
                                if text:
                                    full_response += text

                elif msg_type == "ToolMessage":
                    tool_name = getattr(msg, "name", "tool")
                    content = msg.content if isinstance(msg.content, str) else str(msg.content)
                    tool_records.append({"name": tool_name, "result": content[:500]})

                    if tool_name == "send_message":
                        from app.channels.wechat.delivery import deliver_tool_message
                        try:
                            if await deliver_tool_message(content, session, client):
                                sent_via_tool = True
                        except Exception:
                            log.exception("Failed to send tool message via iLink")

                    cur_tool_name = None

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

            log.info("WeChat consumer bridge: auto-approving %d HITL actions (loop %d)",
                     len(decisions), _loop_i + 1)
            input_payload = Command(resume={"decisions": decisions})

        if not sent_via_tool and full_response.strip():
            await client.send_text(to_user, full_response.strip(), ctx_token)
            log.info("Sent direct response: %s", full_response[:50])

        save_consumer_message(
            session.admin_id, session.service_id,
            session.conversation_id, "assistant", full_response,
            tool_calls=tool_records if tool_records else None,
        )


