"""DeepAgents Service record grants without a model or network connection."""

import json
import unittest
from collections import OrderedDict
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

from app.services import agent, memory_tools


class ServiceRecordsDeepAgentsTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "service_records_enabled": False,
            "include_consumer_conversations": False,
            "memory_subagent_enabled": False,
            "soul_edit_enabled": False,
        }
        self.config_patch = patch.object(
            memory_tools, "get_soul_config", side_effect=lambda _user_id: dict(self.config)
        )
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)

    def _build_agent(self, *, service_message_enabled=True):
        with ExitStack() as stack:
            stack.enter_context(patch.object(agent, "_agent_cache", OrderedDict()))
            stack.enter_context(patch.object(agent, "get_user_filesystem_dir", return_value="/unused"))
            stack.enter_context(patch.object(agent, "create_agent_backend", return_value=object()))
            stack.enter_context(patch.object(agent, "_resolve_model", return_value=object()))
            stack.enter_context(patch.object(agent, "create_deep_agent", side_effect=lambda **kwargs: kwargs))
            stack.enter_context(patch("app.storage.get_storage_service", return_value=SimpleNamespace(ensure_user_dirs=lambda _: None)))
            stack.enter_context(patch.object(memory_tools, "sync_soul_symlink"))
            stack.enter_context(patch("app.services.preferences.get_tz_offset", return_value=8))
            stack.enter_context(patch("app.services.prompt.get_user_system_prompt", return_value="test prompt"))
            stack.enter_context(patch("app.services.prompt.build_user_profile_prompt", return_value=""))
            stack.enter_context(patch("app.services.prompt.get_resolved_capability_prompt", return_value=""))
            stack.enter_context(patch("app.services.subagents.build_subagents_for_agent", return_value=[]))
            stack.enter_context(patch("app.services.document_tools.create_document_tools", return_value=[]))
            for name in (
                "create_run_script_tool", "create_list_files_sorted_tool", "create_move_file_tool",
                "create_update_personal_memory_tool", "create_schedule_tool",
                "create_manage_scheduled_tasks_tool", "create_spawn_child_task_tool",
                "create_publish_service_task_tool", "create_send_service_message_tool",
            ):
                stack.enter_context(patch(f"app.services.tools.{name}", return_value=SimpleNamespace(name=name)))
            for name in ("create_workspace_lock_tools", "create_web_tools"):
                stack.enter_context(patch(f"app.services.tools.{name}", return_value=[]))
            return agent.create_user_agent("owner", model="fake-model",
                                           service_message_enabled=service_message_enabled)

    def test_old_grant_only_keeps_legacy_memory_access(self):
        self.config["include_consumer_conversations"] = True
        built = self._build_agent()
        names = {tool.name for tool in built["tools"]}
        self.assertNotIn("list_service_records", names)
        self.assertNotIn("read_service_record", names)
        self.assertIn("create_send_service_message_tool", names)
        self.assertIn("send_service_message", built["interrupt_on"])

        legacy = {tool.name: tool for tool in memory_tools.create_admin_memory_tools("owner")}
        with patch("app.services.published.list_services", return_value=[]) as list_services:
            self.assertIn("没有找到 Service", legacy["list_service_conversations"].invoke({}))
            list_services.assert_called_once_with("owner")
        with patch("app.services.published.get_consumer_conversation",
                   return_value={"title": "legacy", "messages": []}) as get_conversation:
            self.assertIn("Service 对话「legacy」", legacy["read_service_conversation"].invoke(
                {"service_id": "svc", "conv_id": "conv"}))
            get_conversation.assert_called_once_with("owner", "svc", "conv")
        with patch("app.services.inbox.list_inbox", return_value=[]) as list_inbox:
            self.assertIn("收件箱为空", legacy["read_inbox"].invoke({}))
            list_inbox.assert_called_once()

    def test_new_grant_adds_top_level_tools_and_revokes_cached_instance(self):
        self.config["service_records_enabled"] = True
        built = self._build_agent()
        tools = {tool.name: tool for tool in built["tools"]}
        self.assertIn("list_service_records", tools)
        self.assertIn("read_service_record", tools)
        with patch("app.services.service_records.list_area", return_value={"path": "/service-records", "items": []}) as list_area:
            result = json.loads(tools["list_service_records"].invoke({}))
            self.assertEqual(result["path"], "/service-records")
            list_area.assert_called_once_with("owner", "/service-records", offset=0, limit=20)
        with patch("app.services.service_records.read_area", return_value={"path": "/service-records/inbox/one"}) as read_area:
            tools["read_service_record"].invoke({"path": "/service-records/inbox/one", "limit": 1,
                                                 "content_offset": 9, "message_ref": "a" * 64})
            read_area.assert_called_once_with("owner", "/service-records/inbox/one",
                                              offset=0, limit=1, content_offset=9, message_ref="a" * 64)

        self.config["service_records_enabled"] = False
        with patch("app.services.service_records.list_area") as list_area:
            self.assertIn("未开放", tools["list_service_records"].invoke({}))
            list_area.assert_not_called()

    def test_background_agent_does_not_get_direct_reply(self):
        built = self._build_agent(service_message_enabled=False)
        self.assertNotIn("create_send_service_message_tool", {tool.name for tool in built["tools"]})
        self.assertNotIn("send_service_message", built["interrupt_on"])

    def test_inbox_is_closed_without_either_grant(self):
        legacy = {tool.name: tool for tool in memory_tools.create_admin_memory_tools("owner")}
        with patch("app.services.inbox.list_inbox") as list_inbox:
            self.assertIn("未开放", legacy["read_inbox"].invoke({}))
            list_inbox.assert_not_called()


if __name__ == "__main__":
    unittest.main()
