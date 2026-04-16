"""
iLink Mock Server — simulates ilinkai.weixin.qq.com for local testing.

Usage:
    python tests/ilink_mock_server.py          # port 9999
    python tests/ilink_mock_server.py --port 9998

Mock endpoints mirror the real iLink API under /ilink/bot/*.
Control endpoints under /mock/* let test scripts inject messages,
set QR status, and inspect sent messages.
"""

import os
import time
import uuid
import json
import asyncio
import logging
from collections import defaultdict
from typing import Optional

from fastapi import FastAPI, Request, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

log = logging.getLogger("ilink_mock")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

app = FastAPI(title="iLink Mock Server")

# ── Internal state ───────────────────────────────────────────────────

_qr_counter = 0
_qr_sessions: dict[str, dict] = {}
# qr_id -> {status, bot_token, ilink_user_id, ilink_bot_id, baseurl}

_message_queue: list[dict] = []
# Messages waiting to be returned by getupdates

_sent_messages: list[dict] = []
# Messages received from sendmessage

_upload_counter = 0
_uploads: dict[str, dict] = {}

_getupdates_buf_counter = 0

_lock = asyncio.Lock()


def _reset_state():
    global _qr_counter, _message_queue, _sent_messages
    global _upload_counter, _uploads, _getupdates_buf_counter, _qr_sessions
    _qr_counter = 0
    _qr_sessions = {}
    _message_queue = []
    _sent_messages = []
    _upload_counter = 0
    _uploads = {}
    _getupdates_buf_counter = 0


# ── iLink API endpoints ─────────────────────────────────────────────


@app.get("/ilink/bot/get_bot_qrcode")
async def get_bot_qrcode(bot_type: str = "3"):
    global _qr_counter
    async with _lock:
        _qr_counter += 1
        qr_id = f"mock_qr_{_qr_counter:04d}"
        _qr_sessions[qr_id] = {
            "status": "waiting",
            "bot_token": "",
            "ilink_user_id": "",
            "ilink_bot_id": "",
            "baseurl": "",
        }
    log.info("QR generated: %s", qr_id)
    return {
        "qrcode": qr_id,
        "qrcode_img_content": f"https://mock-ilink/scan/{qr_id}",
    }


@app.get("/ilink/bot/get_qrcode_status")
async def get_qrcode_status(qrcode: str = Query(...)):
    async with _lock:
        session = _qr_sessions.get(qrcode)
    if not session:
        return {"status": "expired"}

    result = {"status": session["status"]}
    if session["status"] == "confirmed":
        result.update({
            "bot_token": session["bot_token"],
            "ilink_user_id": session["ilink_user_id"],
            "ilink_bot_id": session["ilink_bot_id"],
        })
        if session.get("baseurl"):
            result["baseurl"] = session["baseurl"]
    log.info("QR status for %s: %s", qrcode, result["status"])
    return result


@app.post("/ilink/bot/getupdates")
async def getupdates(request: Request):
    global _getupdates_buf_counter
    async with _lock:
        msgs = list(_message_queue)
        _message_queue.clear()
        _getupdates_buf_counter += 1
        buf = f"mock_buf_{_getupdates_buf_counter}"
    if msgs:
        log.info("getupdates returning %d messages", len(msgs))
    return {
        "msgs": msgs,
        "get_updates_buf": buf,
    }


@app.post("/ilink/bot/sendmessage")
async def sendmessage(request: Request):
    body = await request.json()
    msg = body.get("msg", {})
    async with _lock:
        _sent_messages.append({
            "timestamp": time.time(),
            "to_user_id": msg.get("to_user_id", ""),
            "from_user_id": msg.get("from_user_id", ""),
            "context_token": msg.get("context_token", ""),
            "item_list": msg.get("item_list", []),
            "raw": body,
        })
    text_items = [
        it.get("text_item", {}).get("text", "")
        for it in msg.get("item_list", [])
        if it.get("type") == 1
    ]
    log.info("sendmessage to=%s, texts=%s", msg.get("to_user_id"), text_items)
    return {}


@app.post("/ilink/bot/getuploadurl")
async def getuploadurl(request: Request):
    global _upload_counter
    body = await request.json()
    async with _lock:
        _upload_counter += 1
        upload_id = f"upload_{_upload_counter}"
        mock_host = os.environ.get("ILINK_CDN_URL", "http://localhost:9999/cdn")
        upload_url = f"{mock_host}/upload?id={upload_id}"
        _uploads[upload_id] = {
            "filekey": body.get("filekey", ""),
            "media_type": body.get("media_type", 0),
            "data": None,
        }
    log.info("getuploadurl: %s", upload_id)
    return {
        "upload_full_url": upload_url,
        "upload_param": f"mock_param_{upload_id}",
    }


@app.post("/cdn/upload")
async def cdn_upload(request: Request, id: str = Query("")):
    data = await request.body()
    async with _lock:
        if id in _uploads:
            _uploads[id]["data"] = len(data)
    log.info("CDN upload id=%s, size=%d bytes", id, len(data))
    download_param = f"mock_download_param_{id}"
    return Response(
        content=b"",
        headers={"x-encrypted-param": download_param},
    )


@app.get("/cdn/download")
async def cdn_download(encrypted_query_param: str = Query("")):
    log.info("CDN download param=%s", encrypted_query_param[:60])
    return Response(content=b"\x00" * 64, media_type="application/octet-stream")


@app.post("/ilink/bot/getconfig")
async def getconfig(request: Request):
    return {"typing_ticket": f"mock_ticket_{int(time.time())}"}


@app.post("/ilink/bot/sendtyping")
async def sendtyping(request: Request):
    return {}


# ── Mock control endpoints ───────────────────────────────────────────


class InjectMessageRequest(BaseModel):
    text: Optional[str] = None
    from_user_id: str = "mock_user_001"
    context_token: str = "mock_ctx_token"
    image_encrypt_query_param: Optional[str] = None
    image_aeskey: Optional[str] = None


@app.post("/mock/inject-message")
async def mock_inject_message(req: InjectMessageRequest):
    """Inject a message into the getupdates queue as if a user sent it."""
    item_list = []
    if req.text:
        item_list.append({
            "type": 1,
            "text_item": {"text": req.text},
        })
    if req.image_encrypt_query_param:
        item_list.append({
            "type": 2,
            "image_item": {
                "aeskey": req.image_aeskey or "00" * 16,
                "media": {
                    "encrypt_query_param": req.image_encrypt_query_param,
                    "aes_key": req.image_aeskey or "",
                },
            },
        })

    msg = {
        "from_user_id": req.from_user_id,
        "to_user_id": "mock_bot@im.wechat",
        "context_token": req.context_token,
        "message_type": 1,
        "item_list": item_list,
    }
    async with _lock:
        _message_queue.append(msg)
    log.info("Injected message from=%s, items=%d", req.from_user_id, len(item_list))
    return {"ok": True, "queued": len(_message_queue)}


class SetQRStatusRequest(BaseModel):
    qr_id: Optional[str] = None
    status: str = "confirmed"
    bot_token: str = "mock_bot_token_001"
    ilink_user_id: str = "mock_ilink_user_001"
    ilink_bot_id: str = "mock_ilink_bot_001"
    baseurl: Optional[str] = None


@app.post("/mock/set-qr-status")
async def mock_set_qr_status(req: SetQRStatusRequest):
    """Set QR scan status. If qr_id is None, sets the latest QR."""
    async with _lock:
        if req.qr_id:
            target = req.qr_id
        elif _qr_sessions:
            target = max(_qr_sessions.keys())
        else:
            return JSONResponse({"error": "no QR sessions"}, status_code=404)

        if target not in _qr_sessions:
            return JSONResponse({"error": f"QR {target} not found"}, status_code=404)

        _qr_sessions[target]["status"] = req.status
        if req.status == "confirmed":
            _qr_sessions[target].update({
                "bot_token": req.bot_token,
                "ilink_user_id": req.ilink_user_id,
                "ilink_bot_id": req.ilink_bot_id,
                "baseurl": req.baseurl or "",
            })
    log.info("QR %s -> status=%s", target, req.status)
    return {"ok": True, "qr_id": target, "status": req.status}


@app.get("/mock/sent-messages")
async def mock_sent_messages(since: float = 0.0, limit: int = 100):
    """Return messages that the app sent via sendmessage."""
    async with _lock:
        msgs = [m for m in _sent_messages if m["timestamp"] >= since]
    return {"messages": msgs[-limit:], "total": len(msgs)}


@app.get("/mock/status")
async def mock_status():
    async with _lock:
        return {
            "qr_sessions": {k: v["status"] for k, v in _qr_sessions.items()},
            "message_queue_size": len(_message_queue),
            "sent_messages_count": len(_sent_messages),
            "uploads_count": len(_uploads),
        }


@app.post("/mock/reset")
async def mock_reset():
    async with _lock:
        _reset_state()
    log.info("State reset")
    return {"ok": True}


@app.get("/mock/queue")
async def mock_queue():
    """Peek at pending messages in the getupdates queue."""
    async with _lock:
        return {"queue": list(_message_queue)}


# ── Main ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="iLink Mock Server")
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
