"""Standalone chat page for published services — served at /s/{service_id}.

页面来自 vite multi-entry 构建产物 frontend/dist/service-chat.html。该 HTML 里
有一行 `<!-- SVC_INJECT -->` 占位，本路由读到模板后替换为
`<script>window.__SVC__ = {...}</script>`，把运行时配置（service_id /
welcome_message / quick_questions 等）传给前端 React app。

dev 模式（`frontend/dist/service-chat.html` 不存在）下走 vite dev server fallback：
返回一个最小 HTML，引用 `http://localhost:3000/...` 的 vite dev module，
浏览器 ESM 跨域加载即可热更新。
"""

import html
import json
import os
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from app.core.settings import ROOT_DIR
from app.services.published import get_service
from app.core.security import USERS_DIR

router = APIRouter(tags=["consumer-ui"])

_DIST_PAGE = os.path.join(ROOT_DIR, "frontend", "dist", "service-chat.html")
_SVC_INJECT_MARKER = "<!-- SVC_INJECT -->"

# dev 模式下 vite dev server 的位置（默认 3000）
_DEV_VITE_ORIGIN = os.environ.get("JELLYFISH_VITE_DEV_ORIGIN", "http://localhost:3000")


def _find_service_admin(service_id: str):
    """Locate admin_id that owns service_id by scanning users/*/services/."""
    if not os.path.isdir(USERS_DIR):
        return None
    for uid in os.listdir(USERS_DIR):
        svc_cfg = os.path.join(USERS_DIR, uid, "services", service_id, "config.json")
        if os.path.isfile(svc_cfg):
            return uid
    return None


def _safe_json_for_inline_script(value: Any) -> str:
    """JSON-encode for embedding inside <script> — escape '</' to prevent script breakout."""
    return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")


def _build_inject_script(svc_config: dict, service_id: str) -> str:
    """生成要注入到 dist HTML 的 <script>window.__SVC__={...}</script> 块。"""
    payload = {
        "service_id": service_id,
        "service_name": svc_config.get("name", "Chat"),
        "service_desc": svc_config.get("description", ""),
        "welcome_message": svc_config.get("welcome_message") or "",
        "quick_questions": svc_config.get("quick_questions") or [],
    }
    return (
        "<script>window.__SVC__ = "
        + _safe_json_for_inline_script(payload)
        + ";</script>"
        # 用 server-side 替换 <title>，这样首次加载就有正确标题（前端 React 之后还会覆盖一次）
        + f"<title>{html.escape(payload['service_name'])}</title>"
    )


def _dev_fallback_html(svc_config: dict, service_id: str) -> str:
    """dist 不存在时返回引用 vite dev server 的最小 HTML。"""
    inject = _build_inject_script(svc_config, service_id)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
{inject}
<script type="module" src="{_DEV_VITE_ORIGIN}/@vite/client"></script>
<script type="module" src="{_DEV_VITE_ORIGIN}/src/service-chat/main.tsx"></script>
</head>
<body>
<div id="service-root"></div>
</body>
</html>"""


@router.get("/s/{service_id}")
async def serve_chat_page(service_id: str):
    admin_id = _find_service_admin(service_id)
    if not admin_id:
        raise HTTPException(status_code=404, detail="Service not found")
    svc = get_service(admin_id, service_id)
    if not svc or not svc.get("published", True):
        raise HTTPException(status_code=404, detail="Service not found or unpublished")

    if not os.path.isfile(_DIST_PAGE):
        # dev 模式：vite 还没 build；返回引用 vite dev server 的 HTML
        return HTMLResponse(_dev_fallback_html(svc, service_id))

    with open(_DIST_PAGE, "r", encoding="utf-8") as f:
        page = f.read()

    inject = _build_inject_script(svc, service_id)
    if _SVC_INJECT_MARKER in page:
        page = page.replace(_SVC_INJECT_MARKER, inject, 1)
    else:
        # 兜底：把注入脚本插到 </head> 前；防 vite build 把注释剥掉的极端情况
        page = page.replace("</head>", inject + "</head>", 1)

    return HTMLResponse(page)
