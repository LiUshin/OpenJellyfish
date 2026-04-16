"""Standalone chat page for published services — served at /s/{service_id}."""

import html
import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from app.core.settings import ROOT_DIR
from app.services.published import get_service
from app.core.security import USERS_DIR

router = APIRouter(tags=["consumer-ui"])

_CHAT_PAGE = os.path.join(ROOT_DIR, "frontend", "public", "service-chat.html")


def _find_service_admin(service_id: str):
    """Locate admin_id that owns service_id by scanning users/*/services/."""
    if not os.path.isdir(USERS_DIR):
        return None
    for uid in os.listdir(USERS_DIR):
        svc_cfg = os.path.join(USERS_DIR, uid, "services", service_id, "config.json")
        if os.path.isfile(svc_cfg):
            return uid
    return None


@router.get("/s/{service_id}")
async def serve_chat_page(service_id: str):
    admin_id = _find_service_admin(service_id)
    if not admin_id:
        raise HTTPException(status_code=404, detail="Service not found")
    svc = get_service(admin_id, service_id)
    if not svc or not svc.get("published", True):
        raise HTTPException(status_code=404, detail="Service not found or unpublished")

    if not os.path.isfile(_CHAT_PAGE):
        raise HTTPException(status_code=500, detail="Chat page template not found")

    with open(_CHAT_PAGE, "r", encoding="utf-8") as f:
        page = f.read()

    page = page.replace("{{SERVICE_ID}}", html.escape(service_id))
    page = page.replace("{{SERVICE_NAME}}", html.escape(svc.get("name", "Chat")))
    page = page.replace("{{SERVICE_DESC}}", html.escape(svc.get("description", "")))

    return HTMLResponse(page)
