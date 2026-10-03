"""Synthetic scheduler regressions: no LLM, live accounts or message delivery.

Run: python -m unittest discover -s tests -p test_scheduler_hardening.py -v
"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.core import security as sec
from app.services import scheduler as sch, scheduler_tree as st, scheduled_inject as inj
from app.services import scheduler_policy as policy, spawn_limits
from app.services.scheduler_owner import SchedulerOwner
from app.routes import scheduler as routes


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="scheduler-regression-")
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {"DISABLE_SCHEDULER": "0", "SCHED_SPAWN_RATE_PER_HOUR": "30"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.users = patch.object(sec, "USERS_DIR", self.tmp.name)
        self.users.start()
        self.addCleanup(self.users.stop)
        st.invalidate_path_cache()
        sch._heap.clear()
        sch._heap_index.clear()
        sch._wake_event = None
        sch._main_loop_ref = None
        spawn_limits.reset_all()
        inj._lock = asyncio.Lock()
        inj._gate = asyncio.Condition(inj._lock)
        inj._pending.clear()
        inj._active_refcount.clear()
        inj._injecting.clear()
        inj._drainer_tasks.clear()
        self.schedulers = []

    async def asyncTearDown(self):
        for scheduler in self.schedulers:
            await scheduler.stop()
        tasks = list(inj._drainer_tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def make(self, **updates):
        data = dict(name="regression", schedule_type="interval", schedule="3600",
                    task_type="agent", task_config={"prompt": "stub"}, tz_offset_hours=8)
        data.update(updates)
        return sch.create_task("test", data)

    def start(self):
        scheduler = sch.HeapScheduler()
        scheduler.start()
        self.schedulers.append(scheduler)
        return scheduler

    async def settled(self, scheduler):
        async with asyncio.timeout(2):
            while scheduler._running_tasks:
                await asyncio.sleep(.005)

    async def result(self, *args, **kwargs):
        return {"success": True, "output": "stub", "steps": [{"type": "finish"}]}

    def test_permission_paths_reject_escape_before_mkdir(self):
        root = Path(sec.get_user_filesystem_dir("test"))
        root.mkdir(parents=True, exist_ok=True)
        outside = Path(self.tmp.name, "outside")
        outside.mkdir()
        (root / "alias").symlink_to(outside, target_is_directory=True)
        for names in (["docs", "../escape"], ["alias/nested"], ["..\\escape"], "docs"):
            with self.subTest(names=names), self.assertRaises((PermissionError, ValueError)):
                sch._resolve_permission_dirs("test", names)
        self.assertFalse((outside / "nested").exists())
        self.assertEqual(sch._resolve_permission_dirs("test", ["*"]), [str(root.resolve())])

    def test_service_preload_obeys_canonical_published_files(self):
        docs = Path(sec.get_user_filesystem_dir("test"), "docs")
        (docs / "public").mkdir(parents=True, exist_ok=True)
        (docs / "private.txt").write_text("private")
        (docs / "public" / "alias.txt").symlink_to(docs / "private.txt")
        (docs / "public" / "allowed.txt").write_text("allowed")
        with patch("app.services.published.get_service", return_value={"allowed_docs": ["public"]}):
            self.assertEqual(policy.service_doc_paths("test", "svc", ["docs/public/allowed.txt"]), ["public/allowed.txt"])
            for path in ("private.txt", "public/../private.txt", "public/alias.txt"):
                with self.assertRaises(PermissionError):
                    policy.service_doc_paths("test", "svc", [path])

    def test_reply_identity_is_not_taken_from_payload(self):
        with self.assertRaises(PermissionError):
            policy.validate_reply({"channel": "wechat", "admin_id": "other"}, "test")
        session = SimpleNamespace(admin_id="other", service_id="svc", conversation_id="conv")
        manager = SimpleNamespace(get_session=lambda _: session)
        with patch("app.channels.wechat.session_manager.get_session_manager", return_value=manager):
            with self.assertRaises(PermissionError):
                policy.validate_reply({"channel": "wechat", "service_id": "svc", "session_id": "x", "conversation_id": "conv"}, "test", "svc")

    def test_invalid_schedules_never_create_tasks(self):
        for kind, value in [("interval", "-1"), ("interval", "0"), ("cron", "bad"), ("cron", "* * * * * *"), ("unknown", ""), ("once", "tomorrow")]:
            with self.subTest(kind=kind, value=value), self.assertRaises(ValueError):
                self.make(schedule_type=kind, schedule=value)
        self.assertEqual(sch.list_tasks("test"), [])

    def test_metadata_edit_preserves_schedule_and_failure_budget(self):
        task = self.make()
        task["consecutive_failures"] = 3
        sch._save_task("test", task)
        changed = sch.update_task("test", task["id"], {"name": "renamed", "enabled": True, "revision": 99})
        self.assertEqual(changed["next_run_at"], task["next_run_at"])
        self.assertEqual(changed["consecutive_failures"], 3)
        self.assertEqual(changed["revision"], 2)
        cron = self.make(schedule_type="cron", schedule="0 9 * * *")
        changed = sch.update_task("test", cron["id"], {"tz_offset_hours": 0})
        self.assertNotEqual(changed["next_run_at"], cron["next_run_at"])

    def test_pause_remains_available_for_invalid_legacy_task(self):
        task = self.make()
        task["schedule"] = "invalid legacy value"
        sch._save_task("test", task)
        paused = sch.update_task("test", task["id"], {"enabled": False})
        self.assertFalse(paused["enabled"])
        self.assertIsNone(paused["next_run_at"])

    async def test_queued_pause_and_edit_are_rechecked(self):
        for mutation in ({"enabled": False}, {"name": "changed"}):
            task = self.make()
            scheduler = self.start()
            scheduler._exec_sem = asyncio.Semaphore(0)
            runner = AsyncMock(side_effect=self.result)
            with patch.object(sch, "_run_agent_task", runner):
                self.assertTrue(scheduler.run_now("test", task["id"]))
                await asyncio.sleep(.01)
                sch.update_task("test", task["id"], mutation)
                scheduler._exec_sem.release()
                await self.settled(scheduler)
                runner.assert_not_called()
            await scheduler.stop()

    async def test_queued_delete_and_disable_reenable_are_rechecked(self):
        scheduler = self.start()
        for delete in (True, False):
            task = self.make()
            scheduler._exec_sem = asyncio.Semaphore(0)
            with patch.object(sch, "_run_agent_task", AsyncMock()) as runner:
                self.assertTrue(scheduler.run_now("test", task["id"]))
                await asyncio.sleep(.01)
                if delete:
                    sch.delete_task("test", task["id"])
                else:
                    sch.update_task("test", task["id"], {"enabled": False})
                    sch.update_task("test", task["id"], {"enabled": True})
                scheduler._exec_sem.release()
                await self.settled(scheduler)
                runner.assert_not_called()

    async def test_manual_run_preserves_cursor_and_exposes_steps_and_total(self):
        scheduler = self.start()
        task = self.make()
        with patch.object(sch, "_run_agent_task", side_effect=self.result):
            self.assertTrue(scheduler.run_now("test", task["id"]))
            self.assertFalse(scheduler.run_now("test", task["id"]))
            await self.settled(scheduler)
        fresh = sch.get_task("test", task["id"])
        self.assertEqual(fresh["next_run_at"], task["next_run_at"])
        self.assertEqual(fresh["runs"][0]["trigger"], "manual")
        self.assertEqual(sch.get_task_runs("test", task["id"])[0]["steps"], [{"type": "finish"}])
        self.assertEqual(sch.list_tasks("test")[0]["run_count"], 1)
        self.assertEqual(sch.list_tasks("test", roots_only=False)[0]["run_count"], 1)

    async def test_manual_once_does_not_consume_automatic_occurrence(self):
        task = self.make(schedule_type="once", schedule="")
        with patch.object(sch, "_run_agent_task", side_effect=self.result):
            await sch._execute_task("test", task["id"], manual=True)
            manual = sch.get_task("test", task["id"])
            self.assertEqual(manual["next_run_at"], task["next_run_at"])
            self.assertIsNotNone(manual["last_run_at"])
            self.assertIsNone(manual["last_scheduled_run_at"])
            await sch._execute_task("test", task["id"])
        automatic = sch.get_task("test", task["id"])
        self.assertIsNone(automatic["next_run_at"])
        self.assertEqual(automatic["run_count"], 2)

    async def test_workspace_deferred_once_is_retried_without_failure(self):
        task = self.make(schedule_type="once", schedule="")
        result = {"success": False, "deferred": True, "output": "busy", "steps": []}
        with patch.object(sch, "_run_agent_task", AsyncMock(return_value=result)):
            await sch._execute_task("test", task["id"])
        saved = sch.get_task("test", task["id"])
        self.assertEqual(saved["runs"][0]["status"], "deferred")
        self.assertEqual(saved["consecutive_failures"], 0)
        self.assertIsNone(saved["last_scheduled_run_at"])
        self.assertGreater(saved["next_run_at"], task["next_run_at"])

    async def test_service_queue_rechecks_pause_and_manual_keeps_cursor(self):
        task = sch.create_service_task("test", "svc", {
            "name": "service", "schedule_type": "interval", "schedule": "3600",
            "tz_offset_hours": 8, "task_config": {"prompt": "stub"}})
        scheduler = self.start()
        scheduler._exec_sem = asyncio.Semaphore(0)
        with patch.object(sch, "_run_service_agent_task", side_effect=self.result) as runner:
            scheduler.run_service_task_now("test", "svc", task["id"])
            await asyncio.sleep(.01)
            sch.update_service_task("test", "svc", task["id"], {"enabled": False})
            scheduler._exec_sem.release()
            await self.settled(scheduler)
            runner.assert_not_called()
            resumed = sch.update_service_task("test", "svc", task["id"], {"enabled": True})
            self.assertTrue(scheduler.run_service_task_now("test", "svc", task["id"]))
            await self.settled(scheduler)
            runner.assert_called_once()
        saved = sch.get_service_task("test", "svc", task["id"])
        self.assertEqual(saved["next_run_at"], resumed["next_run_at"])
        self.assertEqual(sch.list_service_tasks("test", "svc")[0]["run_count"], 1)
        self.assertEqual(sch.get_service_task_runs("test", "svc", task["id"])[-1]["steps"], [{"type": "finish"}])

    async def test_running_edit_wins_over_old_run_completion(self):
        task = self.make()
        started, release = asyncio.Event(), asyncio.Event()
        async def run(*args, **kwargs):
            started.set()
            await release.wait()
            return await self.result()
        with patch.object(sch, "_run_agent_task", run):
            execution = asyncio.create_task(sch._execute_task("test", task["id"]))
            await started.wait()
            edited = sch.update_task("test", task["id"], {"schedule": "7200"})
            release.set()
            await execution
        self.assertEqual(sch.get_task("test", task["id"])["next_run_at"], edited["next_run_at"])

    async def test_stop_drains_running_and_cancels_queued(self):
        scheduler = self.start()
        scheduler._exec_sem = asyncio.Semaphore(1)
        a, b = self.make(), self.make()
        started, cancelled = asyncio.Event(), asyncio.Event()
        async def run(*args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        with patch.object(sch, "_run_agent_task", run):
            scheduler.run_now("test", a["id"])
            scheduler.run_now("test", b["id"])
            await started.wait()
            await scheduler.stop()
        self.assertTrue(cancelled.is_set())
        self.assertFalse(scheduler._running_tasks)
        self.assertFalse(scheduler._handles)
        self.assertEqual(sch.get_task("test", a["id"])["runs"][0]["status"], "cancelled")
        self.assertEqual(sch.get_task("test", b["id"])["runs"], [])
        self.assertFalse(scheduler.run_now("test", a["id"]))

    async def test_disabled_switch_and_api_rejected_trigger(self):
        scheduler = self.start()
        task = self.make()
        with patch.dict(os.environ, {"DISABLE_SCHEDULER": "true"}):
            self.assertFalse(scheduler.run_now("test", task["id"]))
            with patch.object(routes, "get_scheduler", return_value=scheduler):
                with self.assertRaises(routes.HTTPException) as error:
                    await routes.api_run_now(task["id"], user={"user_id": "test"})
                self.assertEqual(error.exception.status_code, 409)

    async def test_single_owner_across_instances_and_processes(self):
        scheduler = self.start()
        with self.assertRaises(RuntimeError):
            sch.HeapScheduler().start()
        code = "from app.services.scheduler_owner import SchedulerOwner; import sys; SchedulerOwner(sys.argv[1])"
        result = await asyncio.to_thread(subprocess.run, [sys.executable, "-c", code, self.tmp.name], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Another scheduler owns", result.stderr)
        await scheduler.stop()
        owner = SchedulerOwner(self.tmp.name)
        owner.close()

    async def test_queue_is_bounded(self):
        scheduler = self.start()
        scheduler._exec_sem = asyncio.Semaphore(0)
        tasks = [self.make() for _ in range(3)]
        with patch.dict(os.environ, {"SCHEDULER_MAX_PENDING": "2"}):
            self.assertTrue(scheduler.run_now("test", tasks[0]["id"]))
            self.assertTrue(scheduler.run_now("test", tasks[1]["id"]))
            self.assertFalse(scheduler.run_now("test", tasks[2]["id"]))

    async def test_steps_retention_tracks_committed_summaries(self):
        task = self.make()
        with patch.object(sch, "_run_agent_task", side_effect=self.result):
            for _ in range(25):
                await sch._execute_task("test", task["id"], manual=True)
        saved = sch.get_task("test", task["id"])
        files = list(Path(st.task_path_for("admin", "test", task["id"]), "runs").glob("*.jsonl"))
        self.assertEqual(len(saved["runs"]), 20)
        self.assertEqual(len(files), 20)
        self.assertEqual(saved["run_count"], 25)

    async def test_scripts_do_not_block_and_cancellation_drains_worker(self):
        stopped = False
        def blocking(**kwargs):
            nonlocal stopped
            time.sleep(.12)
            stopped = True
            return {"error": None, "stdout": "", "stderr": "", "exit_code": 0}
        with patch("app.services.script_runner.run_script", blocking), patch("app.services.venv_manager.get_user_python", return_value=sys.executable):
            start = time.monotonic()
            task = asyncio.create_task(sch._run_script_task("test", {"script_path": "stub.py"}))
            await asyncio.sleep(.025)
            self.assertLess(time.monotonic() - start, .10)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(stopped)

    def test_shared_creation_boundary_enforces_chain_budget(self):
        parent = self.make()
        ctx = sch._build_task_context_from_meta("admin", "test", parent)
        token = sch._current_task_var.set(ctx)
        try:
            with patch.dict(os.environ, {"SCHED_SPAWN_RATE_PER_HOUR": "1"}):
                child = self.make()
                self.assertEqual(child["parent_task_id"], parent["id"])
                with self.assertRaises(ValueError):
                    sch.create_child_task(ctx, {"schedule_type": "once", "task_type": "agent"})
                with self.assertRaises(PermissionError):
                    sch.create_service_task("test", "another", {})
        finally:
            sch._current_task_var.reset(token)

    async def test_patch_can_clear_reply_and_invalid_input_returns_422(self):
        task = self.make(reply_to={"channel": "web", "conversation_id": "conv"})
        changed = await routes.api_update_task(task["id"], routes.UpdateTaskRequest(reply_to=None), user={"user_id": "test"})
        self.assertIsNone(changed["reply_to"])
        with self.assertRaises(routes.HTTPException) as err:
            await routes.api_update_task(task["id"], routes.UpdateTaskRequest(schedule="-1"), user={"user_id": "test"})
        self.assertEqual(err.exception.status_code, 422)

    def legacy(self):
        root = Path(st.scope_root("admin", "test"))
        root.mkdir(parents=True, exist_ok=True)
        source = root / "task_legacy.json"
        source.write_text(json.dumps({"id": "task_legacy", "enabled": False, "runs": []}))
        steps = root / "task_legacy.steps"
        steps.mkdir()
        (steps / "run_old.jsonl").write_text('{"type":"finish"}\n')
        return root, source, steps

    async def test_nonempty_migration_api_and_discovery(self):
        root, source, steps = self.legacy()
        dry = await routes.api_admin_migrate_v1_to_v2(routes.MigrateAdminRequest(), user={"user_id": "test"})
        self.assertEqual(dry["legacy_files"], ["task_legacy.json"])
        tasks = sch.list_tasks("test")
        self.assertEqual([t["id"] for t in tasks], ["task_legacy"])
        self.assertFalse(source.exists())
        self.assertTrue((root / "task_legacy/runs/run_old.jsonl").exists())

    def test_migration_copy_failure_keeps_sources_and_retries(self):
        root, source, steps = self.legacy()
        with patch.object(st.shutil, "copy2", side_effect=OSError("injected failure")):
            self.assertIsNone(st.migrate_legacy_task("admin", "test", "task_legacy"))
        self.assertTrue(source.exists())
        self.assertTrue((steps / "run_old.jsonl").exists())
        self.assertFalse((root / "task_legacy/_meta.json").exists())
        self.assertIsNotNone(st.migrate_legacy_task("admin", "test", "task_legacy"))
        self.assertEqual((root / "task_legacy/runs/run_old.jsonl").read_text(), '{"type":"finish"}\n')

    def test_rescan_evicts_deleted_and_caps_far_future_sleep(self):
        task = self.make(schedule="86400")
        scheduler = sch.HeapScheduler()
        self.assertLessEqual(scheduler._compute_sleep(), sch._RESCAN_INTERVAL_S)
        st.delete_task_subtree("admin", "test", task["id"])
        scheduler._reload_from_disk()
        self.assertFalse(sch._heap_index)

    async def test_l2_large_batches_and_summary_stay_bounded(self):
        messages = []
        class Agent:
            async def aget_state(self, config):
                return SimpleNamespace(values={"messages": messages})
            async def aupdate_state(self, config, update):
                for message in update["messages"]:
                    if message.type == "remove":
                        messages[:] = [m for m in messages if m.id != message.id]
                    else:
                        messages.append(message)
        async def factory():
            return Agent()
        for batch in range(4):
            items = [inj._build_item({"task_id": str(i), "task_name": "name" * 50}, "result", True, None, factory) for i in range(20)]
            await inj._inject_batch("conversation", items)
            self.assertEqual(len(inj._scan_existing_pairs(messages)), 5)
            summaries = [m for m in messages if m.type == "tool" and m.additional_kwargs.get("_sched_summary")]
            self.assertEqual(len(summaries), 1)
            self.assertLessEqual(len(summaries[0].content), inj._TOOL_CONTENT_MAX_CHARS + len(inj._SUMMARY_PREFIX))

    async def test_l2_stream_admission_waits_for_inflight_injection(self):
        injected, finish = asyncio.Event(), asyncio.Event()
        async def inject(*args):
            injected.set()
            await finish.wait()
        item = inj._build_item({"task_id": "task"}, "result", True, None, AsyncMock())
        with patch.object(inj, "_DRAIN_SETTLE_S", .001), patch.object(inj, "_inject_batch", inject):
            await inj._enqueue("conv", item)
            await asyncio.wait_for(injected.wait(), 1)
            entering = asyncio.create_task(inj.mark_thread_active("conv"))
            await asyncio.sleep(.01)
            self.assertFalse(entering.done())
            finish.set()
            await asyncio.wait_for(entering, 1)
            await inj.mark_thread_inactive("conv")

    async def test_l2_active_queue_is_bounded_and_expires(self):
        await inj.mark_thread_active("conv")
        with patch.object(inj, "_DRAIN_SETTLE_S", .001), patch.object(inj, "_QUEUE_MAX_WAIT_S", .01):
            for i in range(inj._MAX_PENDING + 10):
                await inj._enqueue("conv", inj._build_item({"task_id": str(i)}, "x", True, None, AsyncMock()))
            self.assertEqual(inj._pending["conv"].qsize(), inj._MAX_PENDING)
            await asyncio.sleep(.04)
            self.assertNotIn("conv", inj._pending)
        await inj.mark_thread_inactive("conv")


if __name__ == "__main__":
    unittest.main()
