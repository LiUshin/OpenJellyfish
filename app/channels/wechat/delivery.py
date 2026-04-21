"""
Shared WeChat message delivery helpers.

Used by both the real-time Bridge and the Scheduler to forward
send_message tool results (text + media) to WeChat via iLink.
"""

import json
import os
import re
import logging
from typing import List, Tuple

from app.channels.wechat.client import ILinkClient
from app.channels.wechat.session_manager import WeChatSession

log = logging.getLogger("wechat.delivery")

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
_TTS_CONVERTIBLE = {".mp3", ".wav", ".m4a", ".ogg", ".aac", ".flac"}

# 匹配 <<FILE:path>> 媒体标签 — 与 system prompt / generate_* 工具返回值约定一致。
# Agent 在微信对话里常把生成的媒体路径以 <<FILE:...>> 形式塞进 send_message 的 text，
# 投递层主动解析后转为媒体消息发送，避免用户看到字面字符串。
_MEDIA_TAG_RE = re.compile(r"<<FILE:([^>]+?)>>")


def extract_media_tags(text: str) -> Tuple[str, List[str]]:
    """Extract <<FILE:path>> tags from text.

    Returns (cleaned_text, [media_paths]).
    - cleaned_text 移除所有 <<FILE:...>> 标签，并折叠多余空行
    - media_paths 按出现顺序去重 + 去空白
    """
    if not text:
        return text or "", []
    paths_raw = _MEDIA_TAG_RE.findall(text)
    cleaned = _MEDIA_TAG_RE.sub("", text)
    cleaned = re.sub(r"\n[ \t]*\n[ \t]*\n+", "\n\n", cleaned).strip()
    seen: set = set()
    paths: List[str] = []
    for p in paths_raw:
        p = p.strip()
        if p and p not in seen:
            seen.add(p)
            paths.append(p)
    return cleaned, paths


async def deliver_tool_message(
    content_json: str,
    session: WeChatSession,
    client: ILinkClient,
) -> bool:
    """Parse a send_message tool result and deliver to WeChat.

    Returns True if at least one message was sent successfully.
    """
    try:
        payload = json.loads(content_json)
    except (json.JSONDecodeError, TypeError):
        log.warning("Failed to parse send_message result: %s", content_json[:200])
        return False

    text = payload.get("text", "")
    media = payload.get("media")
    to_user = session.from_user_id
    ctx_token = session.context_token
    sent = False

    if not to_user:
        log.warning("Cannot deliver: session %s has no from_user_id", session.session_id)
        return False

    cleaned_text, extra_media = extract_media_tags(text)
    media_paths: List[str] = []
    if media:
        media_paths.append(media)
    media_paths.extend(extra_media)

    for media_path in media_paths:
        try:
            await send_media_to_wechat(session, client, media_path)
            sent = True
        except Exception:
            log.exception("Failed to deliver media %s", media_path)

    if cleaned_text:
        try:
            await client.send_text(to_user, cleaned_text, ctx_token)
            sent = True
            log.info("Delivered text via send_message: %s", cleaned_text[:50])
        except Exception:
            log.exception("Failed to deliver text via iLink")

    return sent


async def send_media_to_wechat(
    session: WeChatSession,
    client: ILinkClient,
    media_path: str,
):
    """Send a generated media file (image/video/audio/file) to WeChat via iLink."""
    from app.storage import get_storage_service
    storage = get_storage_service()

    clean = media_path.lstrip("/").replace("\\", "/")
    if clean.startswith("generated/"):
        clean = clean[len("generated/"):]

    admin_id = session.admin_id
    service_id = session.service_id
    conv_id = session.conversation_id

    try:
        file_bytes = storage.read_consumer_bytes(admin_id, service_id, conv_id, clean)
    except FileNotFoundError:
        log.warning("Media file not found: %s", media_path)
        return

    to_user = session.from_user_id
    ctx_token = session.context_token
    filename = os.path.basename(clean)
    ext = os.path.splitext(filename)[1].lower()
    in_audio_dir = "/audio/" in clean

    if ext in _IMAGE_EXTS:
        await client.send_image(to_user, file_bytes, ctx_token, filename)
        log.info("Sent image to WeChat: %s (%d bytes)", filename, len(file_bytes))
    elif ext in _VIDEO_EXTS:
        await client.send_video(to_user, file_bytes, ctx_token)
        log.info("Sent video to WeChat: %s (%d bytes)", filename, len(file_bytes))
    elif ext == ".silk":
        await client.send_voice(to_user, file_bytes, ctx_token)
        log.info("Sent voice to WeChat: %s (%d bytes)", filename, len(file_bytes))
    elif in_audio_dir and ext in _TTS_CONVERTIBLE:
        await client.send_file(to_user, file_bytes, filename, ctx_token)
        log.info("Sent audio as file to WeChat: %s (%d bytes)", filename, len(file_bytes))
    else:
        await client.send_file(to_user, file_bytes, filename, ctx_token)
        log.info("Sent file to WeChat: %s (%d bytes)", filename, len(file_bytes))
