"""
JellyfishBot — FastAPI application entry point.

New structure:
    app/core/       — settings, security, observability
    app/schemas/    — Pydantic request/response models
    app/services/   — business logic (agent, tools, conversations, prompt, subagents)
    app/routes/     — FastAPI routers
    app/voice/      — WebSocket S2S voice proxy
"""

import logging
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.settings import ROOT_DIR  # noqa: F401  triggers dotenv load
from app.core.observability import shutdown_langfuse
from app.services.agent import init_checkpointer

from app.routes.auth import router as auth_router
from app.routes.conversations import router as conversations_router
from app.routes.chat import router as chat_router
from app.routes.files import router as files_router
from app.routes.scripts import router as scripts_router
from app.routes.models import router as models_router
from app.routes.settings_routes import router as settings_router
from app.routes.batch import router as batch_router
from app.routes.services import router as services_router
from app.routes.consumer import router as consumer_router
from app.routes.scheduler import router as scheduler_router
from app.routes.consumer_ui import router as consumer_ui_router
from app.routes.inbox import router as inbox_router
from app.voice.router import router as voice_router
try:
    from app.channels.wechat.router import router as wechat_api_router
    from app.channels.wechat.admin_router import router as wechat_admin_router
    from app.routes.wechat_ui import router as wechat_ui_router
    _WECHAT_AVAILABLE = True
except ImportError:
    _WECHAT_AVAILABLE = False
    print("[JellyfishBot] WeChat channel unavailable (missing qrcode/pycryptodome)")


app = FastAPI(
    title="JellyfishBot API",
    version="2.0.0",
    description="AI agent with filesystem, voice, batch execution and tool-calling capabilities.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(conversations_router)
app.include_router(chat_router)
app.include_router(files_router)
app.include_router(scripts_router)
app.include_router(models_router)
app.include_router(settings_router)
app.include_router(batch_router)
app.include_router(services_router)
app.include_router(consumer_router)
app.include_router(consumer_ui_router)
app.include_router(scheduler_router)
app.include_router(inbox_router)
app.include_router(voice_router)
if _WECHAT_AVAILABLE:
    app.include_router(wechat_api_router)
    app.include_router(wechat_admin_router)
    app.include_router(wechat_ui_router)


@app.on_event("startup")
async def startup():
    import asyncio
    from app.services.inbox import set_main_loop
    set_main_loop(asyncio.get_running_loop())

    await init_checkpointer()
    print("[JellyfishBot] Checkpointer initialized")

    from app.services.scheduler import get_scheduler
    get_scheduler().start()
    print("[JellyfishBot] Task scheduler started")

    from app.services.venv_manager import restore_all_venvs
    await restore_all_venvs()
    print("[JellyfishBot] User venvs verified")

    if _WECHAT_AVAILABLE:
        from app.channels.wechat.session_manager import get_session_manager
        from app.channels.wechat.bridge import handle_wechat_message
        from app.channels.wechat.admin_router import restore_admin_sessions
        manager = get_session_manager()
        manager.set_message_handler(handle_wechat_message)
        await manager.restore_sessions()
        await manager.start_all_polling()
        manager.start_cleanup_task(inactive_minutes=60 * 24)
        print("[JellyfishBot] WeChat session manager initialized")

        await restore_admin_sessions()
        print("[JellyfishBot] Admin WeChat sessions restored")


@app.on_event("shutdown")
async def shutdown():
    from app.services.scheduler import get_scheduler
    await get_scheduler().stop()

    if _WECHAT_AVAILABLE:
        from app.channels.wechat.session_manager import get_session_manager
        from app.channels.wechat.admin_router import shutdown_admin_sessions
        await get_session_manager().shutdown()
        await shutdown_admin_sessions()
    shutdown_langfuse()
    print("[JellyfishBot] Shutdown complete")


@app.get("/api/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
