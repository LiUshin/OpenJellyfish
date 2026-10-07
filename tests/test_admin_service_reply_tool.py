"""Admin Service reply tool binds a tool call to one durable delivery request."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services.tools import create_send_service_message_tool


class AdminServiceReplyToolTests(unittest.TestCase):
    def test_queues_exact_target_and_reuses_call_key(self):
        tool = create_send_service_message_tool("admin")
        runtime = SimpleNamespace(
            tool_call_id="call_123",
            config={"configurable": {"thread_id": "admin-conv"}},
        )
        queued = {
            "message": {"id": "msg_123"},
            "deliveries": [{"id": "delivery_1", "channel": "web", "status": "pending"}],
        }
        with patch("app.services.service_messaging.send_service_message", return_value=queued) as send:
            first = json.loads(tool.func("svc", "conv", "Hello", runtime, "inbox_1"))
            tool.func("svc", "conv", "Hello", runtime, "inbox_1")

        self.assertEqual(first["status"], "queued")
        self.assertEqual(first["message_id"], "msg_123")
        self.assertEqual(first["deliveries"], [{"id": "delivery_1", "channel": "web", "status": "pending"}])
        self.assertEqual(send.call_count, 2)
        self.assertEqual(send.call_args.args, ("admin", "svc", "conv", "Hello"))
        self.assertEqual(send.call_args.kwargs["inbox_id"], "inbox_1")
        self.assertEqual(
            send.call_args_list[0].kwargs["idempotency_key"],
            send.call_args_list[1].kwargs["idempotency_key"],
        )

    def test_missing_call_id_does_not_send(self):
        tool = create_send_service_message_tool("admin")
        with patch("app.services.service_messaging.send_service_message") as send:
            result = tool.func("svc", "conv", "Hello", SimpleNamespace(tool_call_id=None))
        self.assertIn("缺少本次工具调用编号", result)
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
