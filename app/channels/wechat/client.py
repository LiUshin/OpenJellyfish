"""
iLink Bot protocol client — multi-instance version.

Based on @tencent-weixin/openclaw-weixin@1.0.2 source code.
Each WeChat session holds its own ILinkClient instance.
"""

import io
import os
import json
import time
import random
import base64
import asyncio
import logging
from typing import Optional, Tuple

import httpx
import qrcode

from app.channels.wechat.media import (
    encrypt_aes_ecb, decrypt_aes_ecb,
    generate_aes_key, key_to_b64, b64_to_key,
)

log = logging.getLogger("wechat.ilink")

BASE_URL = os.environ.get("ILINK_BASE_URL", "https://ilinkai.weixin.qq.com")
CDN_BASE = os.environ.get("ILINK_CDN_URL", "https://novac2c.cdn.weixin.qq.com/c2c")
CHANNEL_VERSION = "1.0.2"

ITEM_TEXT = 1
ITEM_IMAGE = 2
ITEM_VOICE = 3
ITEM_FILE = 4
ITEM_VIDEO = 5

MTYPE_USER = 1
MTYPE_BOT = 2
STATE_FINISH = 2


def _base_info() -> dict:
    return {"channel_version": CHANNEL_VERSION}


def _random_uin() -> str:
    return base64.b64encode(str(random.randint(0, 0xFFFFFFFF)).encode()).decode()


def _headers(token: Optional[str] = None) -> dict:
    h = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "X-WECHAT-UIN": _random_uin(),
    }
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _generate_client_id() -> str:
    return f"openclaw-weixin:{int(time.time() * 1000)}-{os.urandom(4).hex()}"


def _create_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(45.0, connect=15.0),
        follow_redirects=True,
        proxy=None,
        verify=True,
    )


# ── Static helpers for QR code generation (no instance needed) ──────


async def generate_qrcode(http: Optional[httpx.AsyncClient] = None) -> dict:
    """
    Call iLink API to generate a login QR code.
    Returns {"qr_id": str, "qr_url": str, "qr_image_png": bytes}.
    """
    own_http = http is None
    if own_http:
        http = _create_http_client()
    try:
        resp = await http.get(
            f"{BASE_URL}/ilink/bot/get_bot_qrcode",
            params={"bot_type": "3"},
            headers=_headers(),
        )
        resp.raise_for_status()
        data = resp.json()

        qr_id = data["qrcode"]
        qr_url = (
            data.get("qrcode_img_content")
            or data.get("url")
            or data.get("qrcode_url")
            or ""
        )
        if not qr_url:
            raise RuntimeError("API did not return a scan URL")

        qr = qrcode.QRCode(version=1, box_size=10, border=2)
        qr.add_data(qr_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png_bytes = buf.getvalue()

        return {"qr_id": qr_id, "qr_url": qr_url, "qr_image_png": png_bytes}
    finally:
        if own_http:
            await http.aclose()


async def poll_qrcode_status(
    qr_id: str, http: Optional[httpx.AsyncClient] = None
) -> dict:
    """
    Check QR scan status once.
    Returns {"status": "waiting"|"scanned"|"confirmed"|"expired", ...}.
    On confirmed, also includes bot_token, ilink_user_id, ilink_bot_id.
    """
    own_http = http is None
    if own_http:
        http = _create_http_client()
    try:
        resp = await http.get(
            f"{BASE_URL}/ilink/bot/get_qrcode_status",
            params={"qrcode": qr_id},
            headers=_headers(),
        )
        resp.raise_for_status()
        return resp.json()
    finally:
        if own_http:
            await http.aclose()


# ── Per-session client ──────────────────────────────────────────────


class ILinkClient:
    """Manages a single iLink session for one WeChat user."""

    def __init__(
        self,
        bot_token: str,
        ilink_user_id: str,
        ilink_bot_id: str,
        base_url: str = BASE_URL,
    ):
        self.token = bot_token
        self.base_url = base_url
        self.ilink_user_id = ilink_user_id
        self.ilink_bot_id = ilink_bot_id
        self.updates_buf: str = ""
        self.typing_ticket: str = ""
        self._http = _create_http_client()

    async def _post(self, endpoint: str, payload: dict) -> dict:
        url = f"{self.base_url}/ilink/bot/{endpoint}"
        resp = await self._http.post(url, json=payload, headers=_headers(self.token))
        resp.raise_for_status()
        text = resp.text
        if not text or text.strip() == "":
            return {}
        data = json.loads(text)
        ret = data.get("ret")
        if ret is not None and ret != 0:
            log.warning("[%s] ret=%s: %s", endpoint, ret,
                        json.dumps(data, ensure_ascii=False)[:300])
        return data

    # ── receive messages ────────────────────────────────────────────

    async def get_updates(self) -> list[dict]:
        data = await self._post("getupdates", {
            "get_updates_buf": self.updates_buf,
            "base_info": _base_info(),
        })
        new_buf = data.get("get_updates_buf")
        if new_buf:
            self.updates_buf = new_buf
        msgs = data.get("msgs") or []
        if msgs:
            log.info("get_updates: %d msgs, keys=%s, first=%s",
                     len(msgs), list(data.keys()),
                     json.dumps(msgs[0], ensure_ascii=False, default=str)[:600])
        return msgs

    # ── send messages ───────────────────────────────────────────────

    async def send_text(self, to: str, text: str, ctx_token: str) -> dict:
        payload = {
            "msg": {
                "from_user_id": "",
                "to_user_id": to,
                "client_id": _generate_client_id(),
                "message_type": MTYPE_BOT,
                "message_state": STATE_FINISH,
                "context_token": ctx_token,
                "item_list": [
                    {"type": ITEM_TEXT, "text_item": {"text": text}}
                ],
            },
            "base_info": _base_info(),
        }
        return await self._post("sendmessage", payload)

    # ── CDN upload (shared by send_image/send_video/send_file/send_voice) ──

    async def _upload_to_cdn(
        self, to: str, plaintext: bytes, media_type: int
    ) -> dict:
        """
        Upload encrypted media to WeChat CDN. Returns dict with keys:
        download_param, aeskey_b64, raw_size, cipher_size.
        Matches openclaw-weixin@2.1.1 upload pipeline.
        """
        import hashlib
        from urllib.parse import quote

        aes_key = generate_aes_key()
        encrypted = encrypt_aes_ecb(plaintext, aes_key)
        filekey = os.urandom(16).hex()
        rawfilemd5 = hashlib.md5(plaintext).hexdigest()
        aeskey_hex = aes_key.hex()

        upload_resp = await self._post("getuploadurl", {
            "filekey": filekey,
            "media_type": media_type,
            "to_user_id": to,
            "rawsize": len(plaintext),
            "rawfilemd5": rawfilemd5,
            "filesize": len(encrypted),
            "no_need_thumb": True,
            "aeskey": aeskey_hex,
            "base_info": _base_info(),
        })
        log.info("getuploadurl resp (media_type=%d): keys=%s", media_type, list(upload_resp.keys()))

        upload_full_url = (upload_resp.get("upload_full_url") or "").strip()
        upload_param = upload_resp.get("upload_param") or ""

        if upload_full_url:
            cdn_upload_url = upload_full_url
        elif upload_param:
            cdn_upload_url = (
                CDN_BASE + "/upload"
                "?encrypted_query_param=" + quote(upload_param, safe="")
                + "&filekey=" + quote(filekey, safe="")
            )
        else:
            log.error("getuploadurl returned no upload URL: %s", upload_resp)
            raise RuntimeError("getuploadurl returned no upload URL")

        log.info("CDN upload POST: %s (%d bytes)", cdn_upload_url[:120], len(encrypted))
        cdn_resp = await self._http.post(
            cdn_upload_url,
            content=encrypted,
            headers={"Content-Type": "application/octet-stream"},
        )
        cdn_resp.raise_for_status()

        download_param = cdn_resp.headers.get("x-encrypted-param", "")
        if not download_param:
            log.error("CDN response missing x-encrypted-param header, headers=%s",
                      dict(cdn_resp.headers))
            raise RuntimeError("CDN upload response missing x-encrypted-param header")
        log.info("CDN upload success, download_param=%s", download_param[:60])

        aeskey_b64 = base64.b64encode(aeskey_hex.encode()).decode()

        return {
            "download_param": download_param,
            "aeskey_b64": aeskey_b64,
            "raw_size": len(plaintext),
            "cipher_size": len(encrypted),
        }

    def _build_cdn_media(self, upload_info: dict) -> dict:
        return {
            "encrypt_query_param": upload_info["download_param"],
            "aes_key": upload_info["aeskey_b64"],
            "encrypt_type": 1,
        }

    async def _send_media_message(
        self, to: str, ctx_token: str, item: dict, text: str = ""
    ) -> dict:
        """Send one or more items (optional text + media) as separate messages."""
        items_to_send = []
        if text:
            items_to_send.append({"type": ITEM_TEXT, "text_item": {"text": text}})
        items_to_send.append(item)

        last_result = {}
        for send_item in items_to_send:
            last_result = await self._post("sendmessage", {
                "msg": {
                    "from_user_id": "",
                    "to_user_id": to,
                    "client_id": _generate_client_id(),
                    "message_type": MTYPE_BOT,
                    "message_state": STATE_FINISH,
                    "context_token": ctx_token,
                    "item_list": [send_item],
                },
                "base_info": _base_info(),
            })
        return last_result

    # ── send media methods ─────────────────────────────────────────

    async def send_image(
        self, to: str, image_bytes: bytes, ctx_token: str, filename: str = "image.png"
    ) -> dict:
        info = await self._upload_to_cdn(to, image_bytes, media_type=1)
        item = {
            "type": ITEM_IMAGE,
            "image_item": {
                "media": self._build_cdn_media(info),
                "mid_size": info["cipher_size"],
            },
        }
        return await self._send_media_message(to, ctx_token, item)

    async def send_video(
        self, to: str, video_bytes: bytes, ctx_token: str, text: str = ""
    ) -> dict:
        info = await self._upload_to_cdn(to, video_bytes, media_type=2)
        item = {
            "type": ITEM_VIDEO,
            "video_item": {
                "media": self._build_cdn_media(info),
                "video_size": info["cipher_size"],
            },
        }
        return await self._send_media_message(to, ctx_token, item, text=text)

    async def send_file(
        self, to: str, file_bytes: bytes, filename: str, ctx_token: str, text: str = ""
    ) -> dict:
        info = await self._upload_to_cdn(to, file_bytes, media_type=3)
        item = {
            "type": ITEM_FILE,
            "file_item": {
                "media": self._build_cdn_media(info),
                "file_name": filename,
                "len": str(info["raw_size"]),
            },
        }
        return await self._send_media_message(to, ctx_token, item, text=text)

    async def send_voice(
        self, to: str, voice_bytes: bytes, ctx_token: str,
        duration_ms: int = 0,
    ) -> dict:
        info = await self._upload_to_cdn(to, voice_bytes, media_type=4)
        voice_item: dict = {
            "media": self._build_cdn_media(info),
        }
        if duration_ms > 0:
            voice_item["playtime"] = duration_ms
        item = {
            "type": ITEM_VOICE,
            "voice_item": voice_item,
        }
        return await self._send_media_message(to, ctx_token, item)

    # ── media download ──────────────────────────────────────────────

    async def download_media(self, cdn_url: str, aes_key_b64: str) -> bytes:
        resp = await self._http.get(cdn_url)
        resp.raise_for_status()
        return decrypt_aes_ecb(resp.content, b64_to_key(aes_key_b64))

    async def download_media_raw(self, cdn_url: str, aes_key_bytes: bytes) -> bytes:
        """Download and decrypt media with raw AES key bytes."""
        resp = await self._http.get(cdn_url)
        resp.raise_for_status()
        return decrypt_aes_ecb(resp.content, aes_key_bytes)

    async def download_received_media(
        self, encrypt_query_param: str, aes_key_bytes: bytes
    ) -> bytes:
        """
        Download media from a received message using encrypt_query_param.
        CDN endpoint: GET https://novac2c.cdn.weixin.qq.com/c2c/download
        Query param name: encrypted_query_param (with 'd')
        Value must be URL-encoded.
        """
        from urllib.parse import quote
        cdn_url = (
            CDN_BASE + "/download"
            "?encrypted_query_param=" + quote(encrypt_query_param, safe="")
        )
        log.info("CDN download: %s", cdn_url[:140])
        resp = await self._http.get(cdn_url)
        log.info("CDN response: status=%d, content-type=%s, len=%d",
                 resp.status_code,
                 resp.headers.get("content-type", "unknown"),
                 len(resp.content))
        resp.raise_for_status()
        return decrypt_aes_ecb(resp.content, aes_key_bytes)

    # ── typing indicator ────────────────────────────────────────────

    async def fetch_typing_ticket(self, ctx_token: str = "") -> dict:
        data = await self._post("getconfig", {
            "ilink_user_id": self.ilink_user_id,
            "context_token": ctx_token,
            "base_info": _base_info(),
        })
        self.typing_ticket = data.get("typing_ticket", "")
        return data

    async def send_typing(self, ctx_token: str):
        if not self.typing_ticket:
            await self.fetch_typing_ticket(ctx_token)
        try:
            await self._post("sendtyping", {
                "ilink_user_id": self.ilink_user_id,
                "typing_ticket": self.typing_ticket,
                "status": 1,
                "base_info": _base_info(),
            })
        except Exception:
            pass

    # ── lifecycle ───────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "bot_token": self.token,
            "base_url": self.base_url,
            "ilink_user_id": self.ilink_user_id,
            "ilink_bot_id": self.ilink_bot_id,
            "updates_buf": self.updates_buf,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ILinkClient":
        client = cls(
            bot_token=data["bot_token"],
            ilink_user_id=data["ilink_user_id"],
            ilink_bot_id=data["ilink_bot_id"],
            base_url=data.get("base_url", BASE_URL),
        )
        client.updates_buf = data.get("updates_buf", "")
        return client

    async def close(self):
        await self._http.aclose()
