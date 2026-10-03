"""Exercise real framework graphs with an offline model and temporary user data."""

from collections import OrderedDict
from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage

from app.services import agent
from app.storage.local import LocalStorageService


class OfflineModel(FakeMessagesListChatModel):
    def bind_tools(self, *args, **kwargs):
        return self


class AgentGraphCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_admin_and_batch_graphs_can_run_one_turn(self):
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            users = Path(temporary) / "users"
            users.mkdir()
            stack.enter_context(patch("app.core.security.USERS_DIR", str(users)))
            stack.enter_context(patch("app.core.security.USERS_JSON", str(users / "users.json")))
            stack.enter_context(patch("app.storage.local.USERS_DIR", str(users)))
            stack.enter_context(patch("app.storage._storage_service", LocalStorageService()))
            stack.enter_context(patch.object(agent, "_agent_cache", OrderedDict()))
            stack.enter_context(patch.object(agent, "_checkpointer", None))
            model = OfflineModel(responses=[AIMessage(content="offline compatibility probe")])
            stack.enter_context(patch.object(agent, "_resolve_model", return_value=model))
            graphs = {
                "admin": agent.create_user_agent("release-probe", model="offline"),
                "batch": agent.create_batch_agent("release-probe", "offline", "Answer briefly."),
            }
            for name, graph in graphs.items():
                with self.subTest(graph=name):
                    result = await graph.ainvoke({"messages": [HumanMessage(content="Respond briefly.")]})
                    self.assertEqual(result["messages"][-1].content, "offline compatibility probe")


if __name__ == "__main__":
    unittest.main()
