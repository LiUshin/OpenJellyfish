"""Exercise real framework graphs with an offline model and temporary user data."""

from collections import OrderedDict
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage

from app.services import agent
from app.storage.local import LocalStorageService


class AsciiLocaleDatetime(datetime):
    """Reproduce Windows' rejection of non-ASCII strftime format literals."""
    def strftime(self, fmt):
        fmt.encode("ascii")
        return super().strftime(fmt)


class OfflineModel(FakeMessagesListChatModel):
    def bind_tools(self, *args, **kwargs):
        return self


class AgentGraphCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_admin_and_batch_graphs_can_run_one_turn(self):
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            stack.enter_context(patch.object(agent, "datetime", AsciiLocaleDatetime))
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

    def test_service_and_cli_prompts_accept_ascii_locale(self):
        from app.services import consumer_agent
        from app.runtime import business_tools
        with ExitStack() as stack:
            stack.enter_context(patch.object(consumer_agent, "datetime", AsciiLocaleDatetime))
            stack.enter_context(patch.object(business_tools, "datetime", AsciiLocaleDatetime))
            stack.enter_context(patch("app.services.preferences.get_tz_offset", return_value=8))
            stack.enter_context(patch("app.services.prompt.get_user_system_prompt", return_value="Today: {today}"))
            stack.enter_context(patch("app.services.prompt.build_user_profile_prompt", return_value=""))
            for prompt in [consumer_agent._build_consumer_system_prompt("probe", {}),
                           business_tools.instructions("probe")]:
                self.assertRegex(prompt, r"Today: \d{4}年\d{2}月\d{2}日")
                self.assertNotIn("{today}", prompt)


if __name__ == "__main__":
    unittest.main()
