"""
WeChat session manager — tracks active iLink sessions,
maps WeChat users to conversations, handles polling lifecycle.
"""

import os
import json
import asyncio
import logging
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional, Dict

from app.channels.wechat.client import ILinkClient
from app.services.published import (
    get_service, create_consumer_conversation,
    _service_dir,
)

log = logging.getLogger("wechat.session")


@dataclass
class WeChatSession:
    session_id: str
    service_id: str
    admin_id: str
    conversation_id: str
    bot_token: str
    ilink_user_id: str
    ilink_bot_id: str
    base_url: str
    from_user_id: str = ""
    context_token: str = ""
    updates_buf: str = ""
    created_at: str = ""
    last_active_at: str = ""

    def __post_init__(self):
        now = datetime.now().isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.last_active_at:
            self.last_active_at = now


def _sessions_path(admin_id: str, service_id: str) -> str:
    return os.path.join(_service_dir(admin_id, service_id), "wechat_sessions.json")


class WeChatSessionManager:
    """Manages all active WeChat sessions across services."""

    def __init__(self):
        self._sessions: Dict[str, WeChatSession] = {}
        self._clients: Dict[str, ILinkClient] = {}
        self._poll_tasks: Dict[str, asyncio.Task] = {}
        self._message_handler = None
        self._lock = asyncio.Lock()

    def set_message_handler(self, handler):
        """Set callback: async handler(session, message_dict)"""
        self._message_handler = handler

    # ── session CRUD ────────────────────────────────────────────────

    async def create_session(
        self,
        admin_id: str,
        service_id: str,
        bot_token: str,
        ilink_user_id: str,
        ilink_bot_id: str,
        base_url: str = "https://ilinkai.weixin.qq.com",
    ) -> WeChatSession:
        async with self._lock:
            for existing in self._sessions.values():
                if existing.bot_token == bot_token:
                    log.info("Session already exists for bot_token, returning existing: %s",
                             existing.session_id)
                    return existing

        stale = [
            sid for sid, s in self._sessions.items()
            if s.ilink_user_id == ilink_user_id and s.service_id == service_id
        ]
        for sid in stale:
            log.info("Removing stale session %s (same user re-scanned)", sid)
            await self.remove_session(sid)

        conv = create_consumer_conversation(admin_id, service_id, title="微信用户", source="wechat")
        conv_id = conv["id"]

        session = WeChatSession(
            session_id="ws_" + uuid.uuid4().hex[:8],
            service_id=service_id,
            admin_id=admin_id,
            conversation_id=conv_id,
            bot_token=bot_token,
            ilink_user_id=ilink_user_id,
            ilink_bot_id=ilink_bot_id,
            base_url=base_url,
        )

        client = ILinkClient(
            bot_token=bot_token,
            ilink_user_id=ilink_user_id,
            ilink_bot_id=ilink_bot_id,
            base_url=base_url,
        )

        async with self._lock:
            self._sessions[session.session_id] = session
            self._clients[session.session_id] = client

        self._save_sessions(admin_id, service_id)
        log.info("Session created: %s (conv=%s, service=%s)",
                 session.session_id, conv_id, service_id)
        return session

    def get_session(self, session_id: str) -> Optional[WeChatSession]:
        return self._sessions.get(session_id)

    def get_client(self, session_id: str) -> Optional[ILinkClient]:
        return self._clients.get(session_id)

    def list_sessions(self, service_id: str = None, admin_id: str = None) -> list[WeChatSession]:
        sessions = list(self._sessions.values())
        if service_id:
            sessions = [s for s in sessions if s.service_id == service_id]
        if admin_id:
            sessions = [s for s in sessions if s.admin_id == admin_id]
        return sessions

    def find_session_by_user(self, from_user_id: str) -> Optional[WeChatSession]:
        for s in self._sessions.values():
            if s.from_user_id == from_user_id:
                return s
        return None

    def _find_duplicate_user_session(
        self, from_user_id: str, service_id: str, exclude_session_id: str
    ) -> Optional[str]:
        """Find another session with the same from_user_id and service_id."""
        for sid, s in self._sessions.items():
            if (sid != exclude_session_id
                    and s.from_user_id == from_user_id
                    and s.service_id == service_id):
                return sid
        return None

    async def remove_session(self, session_id: str):
        async with self._lock:
            task = self._poll_tasks.pop(session_id, None)
            if task and not task.done():
                task.cancel()

            client = self._clients.pop(session_id, None)
            if client:
                await client.close()

            session = self._sessions.pop(session_id, None)
            if session:
                self._save_sessions(session.admin_id, session.service_id)
                log.info("Session removed: %s", session_id)

    # ── polling ─────────────────────────────────────────────────────

    def start_polling(self, session_id: str):
        if session_id in self._poll_tasks:
            task = self._poll_tasks[session_id]
            if not task.done():
                return
        self._poll_tasks[session_id] = asyncio.create_task(
            self._poll_loop(session_id)
        )
        log.info("Polling started for session %s", session_id)

    async def _poll_loop(self, session_id: str):
        consecutive_errors = 0
        consecutive_empty = 0
        MIN_POLL_INTERVAL = 2.0

        while session_id in self._sessions:
            poll_start = asyncio.get_event_loop().time()
            try:
                client = self._clients.get(session_id)
                session = self._sessions.get(session_id)
                if not client or not session:
                    break

                msgs = await client.get_updates()
                consecutive_errors = 0
                session.updates_buf = client.updates_buf

                if not msgs:
                    consecutive_empty += 1
                    if not session.from_user_id and consecutive_empty >= 50:
                        log.info("Session %s: %d consecutive empty polls with no user, removing as abandoned",
                                 session_id, consecutive_empty)
                        await self.remove_session(session_id)
                        break
                else:
                    consecutive_empty = 0

                for msg in msgs:
                    from_user = msg.get("from_user_id", "")
                    if from_user and not session.from_user_id:
                        session.from_user_id = from_user
                        self._save_sessions(session.admin_id, session.service_id)

                        dup = self._find_duplicate_user_session(
                            from_user, session.service_id, session.session_id
                        )
                        if dup:
                            log.info("Removing duplicate session %s for user %s (keeping %s)",
                                     dup, from_user[:20], session.session_id)
                            asyncio.create_task(self.remove_session(dup))

                    session.last_active_at = datetime.now().isoformat()

                    if self._message_handler:
                        try:
                            await self._message_handler(session, msg)
                        except Exception:
                            log.exception("Message handler error (session=%s)", session_id)

                if msgs:
                    self._save_sessions(session.admin_id, session.service_id)

            except asyncio.CancelledError:
                break
            except Exception:
                consecutive_errors += 1
                backoff = min(5 * (2 ** min(consecutive_errors - 1, 5)), 300)
                log.warning("Poll error (session=%s, attempt=%d), retrying in %ds",
                            session_id, consecutive_errors, backoff)
                if consecutive_errors >= 20:
                    log.error("Too many poll errors, removing session %s", session_id)
                    await self.remove_session(session_id)
                    break
                await asyncio.sleep(backoff)
                continue

            elapsed = asyncio.get_event_loop().time() - poll_start
            if elapsed < MIN_POLL_INTERVAL:
                await asyncio.sleep(MIN_POLL_INTERVAL - elapsed)

    async def stop_all_polling(self):
        for sid, task in list(self._poll_tasks.items()):
            task.cancel()
        self._poll_tasks.clear()

    # ── auto-cleanup ────────────────────────────────────────────────

    def start_cleanup_task(self, inactive_minutes: int = 60 * 24):
        """Periodically remove sessions inactive for longer than threshold."""
        self._cleanup_task = asyncio.create_task(
            self._cleanup_loop(inactive_minutes)
        )

    async def _cleanup_loop(self, inactive_minutes: int):
        while True:
            try:
                await asyncio.sleep(300)  # check every 5 minutes
                now = datetime.now()
                to_remove = []
                for sid, session in self._sessions.items():
                    if session.from_user_id:
                        continue
                    try:
                        last = datetime.fromisoformat(session.last_active_at)
                        if (now - last).total_seconds() > inactive_minutes * 60:
                            to_remove.append(sid)
                    except (ValueError, TypeError):
                        pass
                for sid in to_remove:
                    log.info("Auto-removing inactive session: %s", sid)
                    await self.remove_session(sid)
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("Cleanup loop error")

    # ── persistence ─────────────────────────────────────────────────

    def _save_sessions(self, admin_id: str, service_id: str):
        sessions = [
            s for s in self._sessions.values()
            if s.admin_id == admin_id and s.service_id == service_id
        ]
        path = _sessions_path(admin_id, service_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = {"sessions": [asdict(s) for s in sessions]}
        from app.core.fileutil import atomic_json_save
        atomic_json_save(path, data, ensure_ascii=False, indent=2)

    async def restore_sessions(self):
        """Scan all services and restore persisted sessions."""
        from app.core.security import USERS_DIR
        if not os.path.isdir(USERS_DIR):
            return

        count = 0
        for admin_id in os.listdir(USERS_DIR):
            svc_root = os.path.join(USERS_DIR, admin_id, "services")
            if not os.path.isdir(svc_root):
                continue
            for svc_name in os.listdir(svc_root):
                sessions_file = os.path.join(svc_root, svc_name, "wechat_sessions.json")
                if not os.path.isfile(sessions_file):
                    continue

                svc_config = get_service(admin_id, svc_name)
                wc = (svc_config or {}).get("wechat_channel", {})
                if not wc.get("enabled"):
                    continue

                expires_at = wc.get("expires_at")
                if expires_at:
                    try:
                        if datetime.fromisoformat(expires_at).replace(tzinfo=None) < datetime.now():
                            continue
                    except ValueError:
                        pass

                try:
                    with open(sessions_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except (json.JSONDecodeError, OSError):
                    continue

                raw_sessions = []
                for s_data in data.get("sessions", []):
                    try:
                        raw_sessions.append(WeChatSession(**s_data))
                    except Exception:
                        log.exception("Failed to parse session from %s", sessions_file)

                total_in_file = len(raw_sessions)

                best: dict[str, WeChatSession] = {}
                for s in raw_sessions:
                    if not s.from_user_id:
                        continue
                    key = (s.from_user_id, s.service_id)
                    existing = best.get(key)
                    if not existing or s.created_at > existing.created_at:
                        best[key] = s

                kept = list(best.values())
                skipped = total_in_file - len(kept)
                if skipped or total_in_file > 0:
                    log.info("Service %s: %d sessions in file, keeping %d (discarded %d dead/duplicate)",
                             svc_name, total_in_file, len(kept), skipped)

                for session in kept:
                    if session.bot_token in {s.bot_token for s in self._sessions.values()}:
                        continue

                    client = ILinkClient(
                        bot_token=session.bot_token,
                        ilink_user_id=session.ilink_user_id,
                        ilink_bot_id=session.ilink_bot_id,
                        base_url=session.base_url,
                    )
                    client.updates_buf = session.updates_buf

                    self._sessions[session.session_id] = session
                    self._clients[session.session_id] = client
                    count += 1

                if skipped:
                    self._save_sessions(admin_id, svc_name)

        log.info("Restored %d WeChat sessions", count)

    async def start_all_polling(self):
        for sid in list(self._sessions.keys()):
            self.start_polling(sid)

    # ── service config check ────────────────────────────────────────

    @staticmethod
    def check_service_wechat(admin_id: str, service_id: str) -> tuple[bool, str]:
        """Check if service has WeChat channel enabled and not expired."""
        svc = get_service(admin_id, service_id)
        if not svc:
            return False, "Service not found"
        if not svc.get("published", True):
            return False, "Service not published"
        wc = svc.get("wechat_channel", {})
        if not wc.get("enabled"):
            return False, "WeChat channel not enabled"
        expires_at = wc.get("expires_at")
        if expires_at:
            try:
                if datetime.fromisoformat(expires_at).replace(tzinfo=None) < datetime.now():
                    return False, "WeChat channel expired"
            except ValueError:
                pass
        max_sessions = wc.get("max_sessions", 100)
        mgr = get_session_manager()
        current = len(mgr.list_sessions(service_id=service_id))
        if current >= max_sessions:
            return False, f"已达最大会话数 ({max_sessions})"
        return True, "ok"

    # ── lifecycle ───────────────────────────────────────────────────

    async def shutdown(self):
        if hasattr(self, '_cleanup_task') and not self._cleanup_task.done():
            self._cleanup_task.cancel()
        await self.stop_all_polling()
        for client in self._clients.values():
            await client.close()
        self._clients.clear()
        log.info("WeChat session manager shut down")


# Singleton
_manager: Optional[WeChatSessionManager] = None


def get_session_manager() -> WeChatSessionManager:
    global _manager
    if _manager is None:
        _manager = WeChatSessionManager()
    return _manager
