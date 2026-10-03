"""Project brief budget, admin write scope, and provider input contracts."""

import asyncio
import contextlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.runtime.business_tools import BusinessTools, ProjectBriefWrite
from app.runtime.consumer_tools import ServiceTools
from app.runtime.codex import CodexAdapter
from app.runtime.policy import DeploymentPolicy
from app.runtime.service import RunService
from app.runtime.store import RuntimeStore
from app.runtime.types import RuntimeEvent
from app.services import workspace_lock as wl
from app.services.project_context import (
    BRIEF_CONTEXT_TOKEN_LIMIT,
    admin_conversation_scope,
    build_project_brief_context,
    context_for_conversation,
    create_write_project_brief_tool,
    project_brief_metrics,
    reset_active_admin_conversation,
    set_active_admin_conversation,
    write_current_project_brief,
)


class BriefBudgetTests(unittest.TestCase):
    def test_empty_and_oversized_brief_budget_includes_wrapper_and_marker(self):
        self.assertEqual(build_project_brief_context(""), "")
        self.assertEqual(project_brief_metrics("")["brief_token_count"], 0)
        content = "中文项目进展。" * 2000
        metrics = project_brief_metrics(content)
        self.assertEqual(metrics["brief_budget_unit"], "utf8_bytes")
        self.assertTrue(metrics["brief_truncated"])
        bounded = build_project_brief_context(content)
        self.assertLessEqual(len(bounded.encode("utf-8")), BRIEF_CONTEXT_TOKEN_LIMIT)
        self.assertIn("已截断", bounded)
        self.assertTrue(bounded.startswith("<current-project-brief>"))
        self.assertTrue(bounded.endswith("</current-project-brief>\n\n"))
        self.assertNotIn("\ufffd", bounded)

    def test_brief_reloads_from_current_assignment(self):
        with patch("app.services.conversations.get_conversation_meta",
                   side_effect=[{"project_id": "p1"}, {"project_id": "p2"},
                                {"project_id": None}]), \
             patch("app.services.projects.read_project_brief",
                   side_effect=["first", "second"]) as read:
            self.assertIn("first", context_for_conversation("alice", "c1"))
            self.assertIn("second", context_for_conversation("alice", "c1"))
            self.assertEqual(context_for_conversation("alice", "c1"), "")
        self.assertEqual(read.call_args_list[0].args, ("alice", "p1"))
        self.assertEqual(read.call_args_list[1].args, ("alice", "p2"))

    def test_write_target_is_derived_from_owned_conversation(self):
        with patch("app.services.conversations.get_conversation_meta",
                   return_value={"project_id": "p1"}) as get_meta, \
             patch("app.services.projects.get_project", return_value={"id": "p1"}), \
             patch("app.services.projects.write_project_brief", return_value="updated") as write:
            result = write_current_project_brief("alice", "c1", "updated")
        self.assertTrue(result["updated"])
        get_meta.assert_called_once_with("alice", "c1")
        write.assert_called_once_with("alice", "p1", "updated")
        with self.assertRaises(PermissionError):
            write_current_project_brief("alice", None, "unscoped")

    def test_deepagent_tool_requires_trusted_admin_chat_scope(self):
        tool = create_write_project_brief_tool("alice")
        self.assertIn("DENIED", tool.invoke({"content": "new"}))
        wl.register_process("alice-c1", "alice", kind="interactive", label="chat")
        lock_tokens = wl.set_context("alice-c1", "alice")
        project_token = set_active_admin_conversation("alice", "c1")
        try:
            with patch("app.services.project_context.write_current_project_brief",
                       return_value={"updated": True}) as write:
                self.assertIn("updated", tool.invoke({"content": "new"}))
                write.assert_called_once_with("alice", "c1", "new")
        finally:
            reset_active_admin_conversation(project_token)
            wl.reset_context(lock_tokens)
            wl.unregister_process("alice-c1")


class BridgeScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_service_registry_and_bridge_reject_project_brief_write(self):
        bridge = BusinessTools(None, lambda actor, binding: None, SimpleNamespace(emit=lambda *_: None))
        session = {"actor_id": "alice", "conversation_id": "admin-c1",
                   "binding": {"runtime": "codex", "service_scope": {
                       "service_id": "svc1", "conversation_id": "consumer-c1",
                   }}}
        params = {"tool": "jellyfish_write_project_brief",
                  "arguments": {"content": "unauthorized"}}
        with patch.object(ServiceTools, "config", return_value={"allowed_scripts": []}), \
             patch("app.services.project_context.write_current_project_brief") as write:
            service_tools = ServiceTools(None, None, lambda actor, binding: None)
            names = {tool["name"] for tool in service_tools.specifications(session["binding"], "alice")}
            self.assertFalse(any("project" in name for name in names))
            for name in (params["tool"], "jellyfish_service_write_project_brief"):
                with self.subTest(tool=name):
                    denied = await bridge(session, {"channel": "web"}, {**params, "tool": name})
                    self.assertFalse(denied["success"])
            write.assert_not_called()

    async def test_personal_wechat_admin_scope_can_write_without_web_lock(self):
        tool = create_write_project_brief_tool("alice")
        with patch("app.services.project_context.write_current_project_brief",
                   return_value={"updated": True}) as write:
            async with admin_conversation_scope("alice", "c1", channel="wechat"):
                self.assertIn("updated", tool.invoke({"content": "wechat update"}))
            write.assert_called_once_with("alice", "c1", "wechat update")
            self.assertIn("DENIED", tool.invoke({"content": "outside scope"}))

    async def test_cli_write_scope_and_voice_denial(self):
        events = []
        bridge = BusinessTools(None, lambda actor, binding: None,
                               SimpleNamespace(emit=lambda *args: events.append(args)))
        session = {"actor_id": "alice", "conversation_id": "c1",
                   "binding": {"runtime": "codex"}}
        params = {"tool": "jellyfish_write_project_brief",
                  "arguments": {"content": "new"}, "callId": "call-1"}
        with patch("app.services.project_context.write_current_project_brief",
                   return_value={"updated": True}) as write:
            ok = await bridge(session, {"channel": "web"}, params)
            self.assertTrue(ok["success"])
            write.assert_called_once_with("alice", "c1", "new")
            denied = await bridge(session, {"channel": "voice"}, params)
            self.assertFalse(denied["success"])
            self.assertEqual(write.call_count, 1)
        with self.assertRaises(PermissionError):
            bridge.invoke("alice", "jellyfish_write_project_brief",
                          ProjectBriefWrite(content="new"))


class CapturingAdapter:
    def __init__(self):
        self.seen = []
        self.project_context = ""
        self.input_files = []

    async def open_session(self, *_args):
        return "thread"

    async def stream_turn(self, _thread, text):
        self.seen.append((text, self.project_context))
        yield RuntimeEvent("completed", {})

    async def close(self):
        pass


class CapturingBackend:
    def __init__(self, root):
        self.root = root
        self.adapters = []

    def workspace(self, session):
        return self.root / session["id"]

    @contextlib.asynccontextmanager
    async def execution(self, _session):
        adapter = CapturingAdapter()
        self.adapters.append(adapter)
        yield adapter


class ProviderContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_context_is_transient_and_admin_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = RuntimeStore(Path(tmp) / "runtime")
            backend = CapturingBackend(Path(tmp) / "work")
            service = RunService(store, DeploymentPolicy(), backend,
                                 lambda actor, binding: None)
            async def until_completed(run_id):
                for _ in range(100):
                    if store.get("run", run_id)["status"] == "completed":
                        return
                    await asyncio.sleep(.01)
                self.fail("run did not complete")
            try:
                with patch("app.services.project_context.context_for_conversation",
                           return_value="<current-project-brief>brief</current-project-brief>\n\n") as load:
                    cursor = service.create_session("alice", {"runtime": "cursor", "profile_id": "p1", "model": "m"},
                                                    conversation_id="c1")
                    run = service.enqueue("alice", cursor["id"], "cursor-1", "hello")
                    await until_completed(run["id"])
                    self.assertEqual(backend.adapters[-1].seen[0][0],
                                     "<current-project-brief>brief</current-project-brief>\n\nhello")
                    self.assertEqual(store.get("run", run["id"])["message"], "hello")

                    codex = service.create_session("alice", {"runtime": "codex", "profile_id": "p2", "model": "m"},
                                                   conversation_id="c2")
                    run = service.enqueue("alice", codex["id"], "codex-1", "hello")
                    await until_completed(run["id"])
                    self.assertEqual(backend.adapters[-1].seen[0],
                                     ("hello", "<current-project-brief>brief</current-project-brief>\n\n"))

                    voice = service.create_session("alice", {"runtime": "cursor", "profile_id": "p3", "model": "m"},
                                                   conversation_id="c3")
                    run = service.enqueue("alice", voice["id"], "voice-1", "hello", channel="voice")
                    await until_completed(run["id"])
                    self.assertEqual(backend.adapters[-1].seen[0], ("hello", ""))

                    for runtime_scope in ("service_scope", "scheduler_scope"):
                        scoped = service.create_session(
                            "alice", {"runtime": "cursor", "profile_id": runtime_scope,
                                      "model": "m", runtime_scope: {"id": "restricted"}},
                            conversation_id="c4")
                        run = service.enqueue("alice", scoped["id"], runtime_scope, "hello")
                        await until_completed(run["id"])
                        self.assertEqual(backend.adapters[-1].seen[0], ("hello", ""))
                    self.assertEqual(load.call_count, 2)
            finally:
                await service.shutdown()
                store.close()

    async def test_existing_cli_session_tool_upgrade_respects_codex_protocol(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = RuntimeStore(Path(tmp) / "runtime")
            backend = CapturingBackend(Path(tmp) / "work")
            service = RunService(store, DeploymentPolicy(), backend,
                                 lambda actor, binding: None, tool_bridge=object())
            async def complete(run_id):
                for _ in range(100):
                    if store.get("run", run_id)["status"] == "completed":
                        return
                    await asyncio.sleep(.01)
                self.fail("run did not complete")
            try:
                with patch("app.runtime.business_tools.instructions", return_value="v4") as instructions:
                    codex = service.create_session(
                        "alice", {"runtime": "codex", "profile_id": "p1", "model": "m"},
                        conversation_id="c1", dynamic_tools=[{"name": "old-tool"}])
                    codex.update({"thread_id": "native-thread", "instructions_version": 3})
                    store.put("session", codex)
                    run = service.enqueue("alice", codex["id"], "old-codex", "hello")
                    await complete(run["id"])
                    updated = store.get("session", codex["id"])
                    self.assertFalse(updated["project_brief_write_available"])
                    self.assertEqual(updated["dynamic_tools"], [{"name": "old-tool"}])
                    self.assertEqual(updated["instructions_version"], 4)
                    instructions.assert_any_call("alice", "codex", project_brief_write=False)

                    cursor = service.create_session(
                        "alice", {"runtime": "cursor", "profile_id": "p2", "model": "m"},
                        conversation_id="c2", dynamic_tools=[{"name": "old-tool"}])
                    cursor.update({"thread_id": "native-thread", "instructions_version": 3})
                    store.put("session", cursor)
                    run = service.enqueue("alice", cursor["id"], "old-cursor", "hello")
                    await complete(run["id"])
                    updated = store.get("session", cursor["id"])
                    self.assertTrue(updated["project_brief_write_available"])
                    self.assertIn("jellyfish_write_project_brief",
                                  {tool["name"] for tool in updated["dynamic_tools"]})
                    instructions.assert_any_call("alice", "cursor", project_brief_write=True)
            finally:
                await service.shutdown()
                store.close()

    async def test_codex_turn_start_uses_untrusted_additional_context(self):
        class RPC:
            def __init__(self):
                self.calls = []
            async def request(self, method, params):
                self.calls.append((method, params))
                return {"turn": {"id": "turn"}}
            async def next_event(self):
                return {"method": "turn/completed", "params": {"turn": {"status": "completed"}}}

        adapter = CodexAdapter("unused", Path("/tmp/home"), Path("/tmp/work"), "m")
        adapter.rpc = RPC()
        adapter.project_context = "<current-project-brief>brief</current-project-brief>\n\n"
        _ = [event async for event in adapter.stream_turn("thread", "original user text")]
        method, params = adapter.rpc.calls[0]
        self.assertEqual(method, "turn/start")
        self.assertEqual(params["input"][0]["text"], "original user text")
        self.assertEqual(params["additionalContext"], {
            "openjellyfish-project-brief": {
                "value": adapter.project_context,
                "kind": "untrusted",
            },
        })


if __name__ == "__main__":
    unittest.main()
