"""
Shared WeChat message delivery helpers.

Used by both the real-time Bridge and the Scheduler to forward
send_message tool results (text + media) to WeChat via iLink.
"""

import json
import os
import logging

from app.channels.wechat.client import ILinkClient
from app.channels.wechat.session_manager import WeChatSession

log = logging.getLogger("wechat.delivery")

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
_TTS_CONVERTIBLE = {".mp3", ".wav", ".m4a", ".ogg", ".aac", ".flac"}


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

    if media:
        try:
            await send_media_to_wechat(session, client, media)
            sent = True
        except Exception:
            log.exception("Failed to deliver media %s", media)

    if text:
        try:
            await client.send_text(to_user, text, ctx_token)
            sent = True
            log.info("Delivered text via send_message: %s", text[:50])
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
