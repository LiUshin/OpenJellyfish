"""Admin personal WeChat routes bound CLI chats through the shared run service."""
import asyncio
import base64
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.channels.wechat import admin_bridge, admin_router
from app.channels.wechat.client import ILinkAPIError
import app.runtime.chat  # Load the manager alias before tests patch manager.get_runtime.
from app.runtime.store import RuntimeStore
from app.services.conversations import get_conversation


class AdminWeChatRuntimeTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def runtime_store():
        records = {}
        store = SimpleNamespace(
            find=Mock(return_value=[]),
            get=Mock(side_effect=lambda kind, key: records.get((kind, key))),
            put=Mock(side_effect=lambda kind, row: records.__setitem__((kind, row["id"]), dict(row))),
        )
        return store, records

    async def test_runtime_message_runs_once_and_delivers_archived_image(self):
        image_path = "/generated/runtime/session/run/image.png"
        result = {"id": "run", "status": "completed", "output": f"已完成 <<FILE:{image_path}>>",
                  "artifacts": [{"path": image_path, "native_image": True}]}
        store, records = self.runtime_store()
        runtime = SimpleNamespace(store=store, runs=SimpleNamespace(own=Mock(return_value=result)))
        client = SimpleNamespace(send_text=AsyncMock(return_value={"ret": 0}),
                                 send_typing=AsyncMock())
        session = {"user_id": "alice", "conversation_id": "conv", "client": client}
        incoming = {"message_id": 123, "from_user_id": "alice-wx", "context_token": "ctx",
                    "item_list": [{"type": 1, "text_item": {"text": "画一只猫"}}]}
        with patch("app.runtime.chat.conversation_binding", return_value={"runtime_binding": {"runtime": "codex"}, "runtime_session_id": "session"}), \
             patch("app.channels.wechat.admin_router._save_admin_session"), \
             patch("app.channels.wechat.rate_limiter.check_message_rate", return_value=(True, "")), \
             patch("app.runtime.manager.get_runtime", return_value=runtime), \
             patch("app.runtime.chat.enqueue_chat", return_value={"id": "run"}) as enqueue, \
             patch.object(admin_bridge, "_run_admin_agent_and_reply", new_callable=AsyncMock) as legacy, \
             patch.object(admin_bridge, "save_message") as save, \
             patch.object(admin_bridge, "_send_media", new_callable=AsyncMock) as media:
            await admin_bridge.handle_admin_wechat_message(session, incoming)
            store.find.return_value = [{"id": "run"}]
            await admin_bridge.handle_admin_wechat_message(session, incoming)
        enqueue.assert_called_once()
        self.assertEqual(enqueue.call_args.args[:2], ("alice", "conv"))
        self.assertEqual(enqueue.call_args.args[3], "画一只猫")
        self.assertTrue(enqueue.call_args.kwargs["yolo"])
        self.assertEqual(client.send_text.await_count, 1)
        client.send_text.assert_awaited_with("alice-wx", "已完成", "ctx")
        media.assert_awaited_once_with("alice", client, "alice-wx", "ctx", image_path, require_ack=True)
        delivery = next(row for (kind, _), row in records.items() if kind == "wechat_admin_delivery")
        self.assertEqual(delivery["status"], "sent")
        legacy.assert_not_awaited()
        save.assert_not_called()

    async def test_runtime_image_uses_stable_name_and_missing_identity_does_not_run(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp, "wx_random123.png")
            image.write_bytes(b"image-bytes")
            inputs = admin_bridge._runtime_image_inputs([{"filename": image.name, "full_path": str(image)}])
            self.assertEqual(inputs[0]["name"], "image-1.png")
            self.assertEqual(inputs[0]["data_url"], "data:image/png;base64," + base64.b64encode(b"image-bytes").decode())

        client = SimpleNamespace(send_text=AsyncMock(return_value={"ret": 0}))
        session = {"user_id": "alice", "conversation_id": "conv", "client": client}
        incoming = {"from_user_id": "alice-wx", "context_token": "", "item_list": []}
        with patch("app.runtime.chat.conversation_binding", return_value={"runtime_binding": {"runtime": "cursor"}, "runtime_session_id": "session"}), \
             patch("app.channels.wechat.rate_limiter.check_message_rate", return_value=(True, "")), \
             patch.object(admin_bridge, "_download_images", new_callable=AsyncMock) as download, \
             patch.object(admin_bridge, "_run_admin_runtime_and_reply", new_callable=AsyncMock) as run:
            await admin_bridge.handle_admin_wechat_message(session, incoming)
        download.assert_not_awaited()
        run.assert_not_awaited()
        self.assertIn("缺少消息编号", client.send_text.await_args.args[1])

    async def test_unbound_existing_conversation_keeps_deepagents(self):
        client = SimpleNamespace(send_text=AsyncMock(), send_typing=AsyncMock())
        session = {"user_id": "alice", "conversation_id": "old", "client": client}
        incoming = {"from_user_id": "alice-wx", "context_token": "",
                    "item_list": [{"type": 1, "text_item": {"text": "你好"}}]}
        with patch("app.runtime.chat.conversation_binding", return_value={"messages": []}), \
             patch("app.channels.wechat.rate_limiter.check_message_rate", return_value=(True, "")), \
             patch.object(admin_bridge, "_download_images", new_callable=AsyncMock, return_value=[]), \
             patch.object(admin_bridge, "_transcribe_voices", new_callable=AsyncMock, return_value=[]), \
             patch.object(admin_bridge, "_run_admin_agent_and_reply", new_callable=AsyncMock) as legacy, \
             patch.object(admin_bridge, "_run_admin_runtime_and_reply", new_callable=AsyncMock) as runtime, \
             patch.object(admin_bridge, "save_message") as save:
            await admin_bridge.handle_admin_wechat_message(session, incoming)
        legacy.assert_awaited_once_with(session, "alice-wx", "", "你好")
        runtime.assert_not_awaited()
        save.assert_called_once_with("alice", "old", "user", "你好", attachments=None)

    async def test_incomplete_cli_binding_fails_closed(self):
        client = SimpleNamespace(send_text=AsyncMock(return_value={"ret": 0}))
        session = {"user_id": "alice", "conversation_id": "broken", "client": client}
        incoming = {"message_id": 1, "from_user_id": "alice-wx", "context_token": "", "item_list": []}
        with patch("app.runtime.chat.conversation_binding", return_value={"runtime_binding": {"runtime": "codex"}}), \
             patch("app.channels.wechat.rate_limiter.check_message_rate", return_value=(True, "")), \
             patch.object(admin_bridge, "_run_admin_agent_and_reply", new_callable=AsyncMock) as legacy, \
             patch.object(admin_bridge, "_download_images", new_callable=AsyncMock) as download:
            await admin_bridge.handle_admin_wechat_message(session, incoming)
        legacy.assert_not_awaited()
        download.assert_not_awaited()
        self.assertIn("连接不完整", client.send_text.await_args.args[1])

        client.send_text.reset_mock()
        with patch("app.runtime.chat.conversation_binding", return_value={"runtime_binding": {"runtime": "unknown"}}), \
             patch("app.channels.wechat.rate_limiter.check_message_rate", return_value=(True, "")), \
             patch.object(admin_bridge, "_run_admin_agent_and_reply", new_callable=AsyncMock) as legacy:
            await admin_bridge.handle_admin_wechat_message(session, incoming)
        legacy.assert_not_awaited()
        self.assertIn("绑定无效", client.send_text.await_args.args[1])

    async def test_definite_rejection_retries_pending_delivery_but_unknown_does_not(self):
        result = {"id": "run", "status": "completed", "output": "回答", "artifacts": []}
        store, records = self.runtime_store()
        runtime = SimpleNamespace(store=store, runs=SimpleNamespace(own=Mock(return_value=result)))
        client = SimpleNamespace(send_text=AsyncMock(side_effect=[
            ILinkAPIError("rejected", explicit_rejection=True), {"ret": 0},
        ]))
        session = {"user_id": "alice", "conversation_id": "conv", "client": client}
        with patch("app.runtime.manager.get_runtime", return_value=runtime), \
             patch("app.runtime.chat.enqueue_chat", return_value={"id": "run"}) as enqueue:
            await admin_bridge._run_admin_runtime_and_reply(session, "alice-wx", "ctx", "问题", [], "request")
            self.assertEqual(records[("wechat_admin_delivery", "request")]["status"], "pending")
            await admin_bridge._run_admin_runtime_and_reply(
                session, "alice-wx", "ctx", "", [], "request", existing_run=result,
            )
        enqueue.assert_called_once()
        self.assertEqual(client.send_text.await_count, 2)
        self.assertEqual(records[("wechat_admin_delivery", "request")]["status"], "sent")

        store2, records2 = self.runtime_store()
        runtime2 = SimpleNamespace(store=store2, runs=SimpleNamespace(own=Mock(return_value=result)))
        uncertain = SimpleNamespace(send_text=AsyncMock(side_effect=RuntimeError("network disconnected")))
        session["client"] = uncertain
        with patch("app.runtime.manager.get_runtime", return_value=runtime2), \
             patch("app.runtime.chat.enqueue_chat", return_value={"id": "run"}):
            await admin_bridge._run_admin_runtime_and_reply(session, "alice-wx", "ctx", "问题", [], "other")
            await admin_bridge._run_admin_runtime_and_reply(
                session, "alice-wx", "ctx", "", [], "other", existing_run=result,
            )
        self.assertEqual(records2[("wechat_admin_delivery", "other")]["status"], "unknown")
        self.assertEqual(uncertain.send_text.await_count, 1)

    async def test_unknown_delivery_state_survives_store_reopen(self):
        result = {"id": "run", "status": "completed", "output": "回答", "artifacts": []}
        with tempfile.TemporaryDirectory() as temp:
            store = RuntimeStore(Path(temp))
            runtime = SimpleNamespace(store=store, runs=SimpleNamespace(own=Mock(return_value=result)))
            client = SimpleNamespace(send_text=AsyncMock(side_effect=RuntimeError("lost acknowledgement")))
            session = {"user_id": "alice", "conversation_id": "conv", "client": client}
            try:
                with patch("app.runtime.manager.get_runtime", return_value=runtime), \
                     patch("app.runtime.chat.enqueue_chat", return_value={"id": "run"}):
                    await admin_bridge._run_admin_runtime_and_reply(session, "alice-wx", "ctx", "问题", [], "request")
            finally:
                store.close()
            reopened = RuntimeStore(Path(temp))
            try:
                delivery = reopened.get("wechat_admin_delivery", "request")
                self.assertEqual(delivery["status"], "unknown")
                self.assertEqual(delivery["parts"][0]["status"], "unknown")
            finally:
                reopened.close()

    async def test_qr_confirmation_binds_current_runtime_choice(self):
        with tempfile.TemporaryDirectory() as temp:
            users = Path(temp, "users")
            users.mkdir()
            binding = {"runtime": "codex", "profile_id": "profile", "model": "model"}
            runtime = SimpleNamespace(runs=SimpleNamespace(create_session=Mock(return_value={"id": "session"})))
            client = SimpleNamespace(authorize_send=None)
            challenge = {"alice-qr": {"user_id": "alice", "expires_at": asyncio.get_running_loop().time() + 600,
                                      "result": None}}
            info = {"status": "confirmed", "bot_token": "token", "ilink_user_id": "alice-wx", "ilink_bot_id": "bot"}
            with patch("app.core.security.USERS_DIR", str(users)), \
                 patch.dict(os.environ, {"JELLYFISH_RUNTIME_DATA_DIR": str(Path(temp, "runtime"))}), \
                 patch.object(admin_router, "_admin_sessions", {}), \
                 patch.object(admin_router, "_admin_qr_challenges", challenge), \
                 patch.object(admin_router, "_admin_confirm_locks", {}), \
                 patch.object(admin_router, "poll_qrcode_status", new_callable=AsyncMock, return_value=info), \
                 patch.object(admin_router, "ILinkClient", return_value=client), \
                 patch.object(admin_router, "_start_admin_polling"), \
                 patch("app.runtime.chat.choice", return_value=binding) as choice, \
                 patch("app.runtime.chat.get_runtime", return_value=runtime):
                result = await admin_router.api_admin_qrcode_status("alice-qr", user={"user_id": "alice"})
                conv = get_conversation("alice", result["conversation_id"])
            choice.assert_called_once_with("alice", None)
            self.assertEqual(conv["runtime_session_id"], "session")
            self.assertEqual(conv["runtime_binding"], binding)
            self.assertEqual(runtime.runs.create_session.call_args.kwargs["conversation_id"], conv["id"])


if __name__ == "__main__":
    unittest.main()
