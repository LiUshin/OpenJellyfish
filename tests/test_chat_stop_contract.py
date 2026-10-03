"""Exercise the production stop handler without starting models or app services."""
import ast
import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


class ChatStopContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / "app/routes/chat.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        handler = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "_deepagents_stop")
        self.state = {
            "StopChatRequest": SimpleNamespace,
            "_cancel_flags": {},
            "_follow_up_on_cancel": {},
            "_interrupt_state": {},
            "_log": logging.getLogger(__name__),
        }
        exec(compile(ast.Module(body=[handler], type_ignores=[]), str(path), "exec"), self.state)
        self.stop = self.state["_deepagents_stop"]
        self.user = {"user_id": "owner"}
        self.thread = "owner-conversation"

    def request(self, follow_up=None, queue_id=None):
        return SimpleNamespace(conversation_id="conversation", follow_up=follow_up, queue_id=queue_id)

    async def test_inactive_follow_up_is_not_registered(self):
        result = await self.stop(self.request("next message", "q1"), self.user)
        self.assertEqual(result, {"status": "not_running"})
        self.assertEqual(self.state["_follow_up_on_cancel"], {})

    async def test_active_follow_up_is_registered_and_cancellation_requested(self):
        event = asyncio.Event()
        self.state["_cancel_flags"][self.thread] = event
        result = await self.stop(self.request("next message", "q1"), self.user)
        self.assertEqual(result, {"status": "stopping"})
        self.assertTrue(event.is_set())
        self.assertEqual(self.state["_follow_up_on_cancel"][self.thread], {"message": "next message", "queue_id": "q1"})

    async def test_ordinary_stop_revokes_an_accepted_continuation(self):
        event = asyncio.Event()
        self.state["_cancel_flags"][self.thread] = event
        await self.stop(self.request("next message", "q1"), self.user)
        result = await self.stop(self.request(), self.user)
        self.assertEqual(result, {"status": "stopping"})
        self.assertTrue(event.is_set())
        self.assertNotIn(self.thread, self.state["_follow_up_on_cancel"])

    async def test_inactive_interrupt_cannot_turn_a_later_stop_into_continue(self):
        await self.stop(self.request("must stay queued", "q1"), self.user)
        event = asyncio.Event()  # The previously pending agent now starts.
        self.state["_cancel_flags"][self.thread] = event
        result = await self.stop(self.request(), self.user)
        self.assertEqual(result, {"status": "stopping"})
        self.assertTrue(event.is_set())
        self.assertIsNone(self.state["_follow_up_on_cancel"].pop(self.thread, None))

    async def test_inactive_stop_clears_stale_continuation_only_for_its_owner(self):
        self.state["_follow_up_on_cancel"].update({self.thread: {"message": "stale"}, "other-conversation": {"message": "keep"}})
        self.assertEqual(await self.stop(self.request(), self.user), {"status": "not_running"})
        self.assertEqual(self.state["_follow_up_on_cancel"], {"other-conversation": {"message": "keep"}})

    async def test_paused_approval_clears_continuation_and_releases_lock(self):
        self.state["_interrupt_state"][self.thread] = {"actions": ["write_file"]}
        self.state["_follow_up_on_cancel"][self.thread] = {"message": "stale"}
        lock = SimpleNamespace(unregister_process=Mock())
        services = SimpleNamespace(workspace_lock=lock)
        with patch.dict("sys.modules", {"app.services": services}):
            result = await self.stop(self.request("must not be queued", "q1"), self.user)
        self.assertEqual(result, {"status": "stopped"})
        self.assertEqual(self.state["_interrupt_state"], {})
        self.assertEqual(self.state["_follow_up_on_cancel"], {})
        lock.unregister_process.assert_called_once_with(self.thread)


if __name__ == "__main__":
    unittest.main()
