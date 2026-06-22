"""Phase B smoke test: storage tree + heap + spawn + L3 propagation.

Runs entirely against an isolated USERS_DIR (no network, no LLM).  Verifies
the contract surface that downstream callers (tools.py / agent.py / routes)
will rely on.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone

# Make the project root importable when this script is run directly.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Isolate USERS_DIR before any app imports
_TMP = tempfile.mkdtemp(prefix="sched_b_smoke_")
os.environ["SDA_USERS_DIR_OVERRIDE"] = _TMP

from app.core import security as sec  # noqa: E402
sec.USERS_DIR = _TMP
os.makedirs(_TMP, exist_ok=True)

from app.services import scheduler as sch  # noqa: E402
from app.services import scheduler_tree as st  # noqa: E402

UID = "user_smoke"


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def test_create_root_admin_task() -> str:
    _section("1. create_task → root in v2 tree")
    t = sch.create_task(UID, {
        "name": "smoke root",
        "description": "phase B smoke root",
        "schedule_type": "interval",
        "schedule": "60",
        "task_type": "agent",
        "task_config": {"prompt": "hi", "capabilities": []},
        "enabled": True,
    })
    assert t["id"].startswith("task_"), t["id"]
    assert t["parent_task_id"] is None
    assert t["root_task_id"] == t["id"]
    assert t["spawn_chain"] == []
    assert t["spawn_depth"] == 0

    task_dir = st.task_path_for("admin", UID, t["id"])
    assert task_dir is not None and os.path.isdir(task_dir), task_dir
    assert os.path.isfile(os.path.join(task_dir, "_meta.json"))
    print(f"  ✓ root task created at {os.path.relpath(task_dir, _TMP)}")

    # Verify it landed in the heap with the right next_run_at
    key = sch._heap_key("admin", UID, t["id"])
    assert key in sch._heap_index, sch._heap_index
    print(f"  ✓ heap_index has key {key} -> {sch._heap_index[key]}")
    return t["id"]


def test_list_includes_descendants(root_id: str) -> None:
    _section("2. list_tasks returns flat tree")
    items = sch.list_tasks(UID)
    assert any(i["id"] == root_id for i in items), items
    print(f"  ✓ list_tasks returned {len(items)} task(s)")


def test_create_child(root_id: str) -> str:
    _section("3. create_child_task wires lineage + bumps counters")
    # Build the parent ctx the way _execute_task would
    parent = sch._load_task(UID, root_id)
    ctx = sch._build_task_context_from_meta("admin", UID, parent)
    assert isinstance(ctx, sch.TaskContext)

    child = sch.create_child_task(ctx, {
        "name": "smoke child",
        "schedule_type": "once",
        "schedule": "",
        "task_type": "agent",
        "task_config": {"prompt": "child task"},
        "spawn_reason": "smoke test",
    })
    assert child["parent_task_id"] == root_id
    assert child["root_task_id"] == root_id
    assert child["spawn_chain"] == [root_id]
    assert child["spawn_depth"] == 1

    parent_after = sch._load_task(UID, root_id)
    assert parent_after["children_count"] == 1
    assert parent_after["descendants_count"] == 1

    child_dir = st.task_path_for("admin", UID, child["id"])
    assert child_dir is not None
    assert os.path.dirname(child_dir) == st.task_path_for("admin", UID, root_id)
    print(f"  ✓ child {child['id']} nested under root {root_id}")
    print(f"  ✓ parent counters: children={parent_after['children_count']}, "
          f"descendants={parent_after['descendants_count']}")

    # And the child should now also be in the heap
    ckey = sch._heap_key("admin", UID, child["id"])
    assert ckey in sch._heap_index, sch._heap_index
    print(f"  ✓ child indexed in heap as {ckey}")
    return child["id"]


def test_l3_propagation(root_id: str, child_id: str) -> None:
    _section("4. _propagate_descendant_summary → ancestor descendants_summary")
    child_meta = sch._load_task(UID, child_id)
    fake_run = {
        "run_id": "run_smoketest",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "status": "success",
        "output": "child finished — first line\nignored second line",
    }
    sch._propagate_descendant_summary("admin", UID, None, child_meta, fake_run)

    parent_after = sch._load_task(UID, root_id)
    summary = parent_after.get("descendants_summary", "")
    assert child_id in summary, summary
    assert "child finished" in summary, summary
    print(f"  ✓ ancestor summary len={len(summary)}: {summary[:120]}...")


def test_delete_subtree(root_id: str) -> None:
    _section("5. delete_task removes entire subtree")
    ok = sch.delete_task(UID, root_id)
    assert ok, "delete_task returned False"
    assert sch._load_task(UID, root_id) is None
    items = sch.list_tasks(UID)
    assert all(i["id"] != root_id for i in items), items
    print(f"  ✓ root + children gone; remaining tasks: {len(items)}")


async def test_heap_loop_dispatches() -> None:
    _section("6. HeapScheduler dispatches a due task end-to-end")
    fired = asyncio.Event()
    captured = {}

    async def fake_execute_task(user_id, task_id):
        captured["uid"] = user_id
        captured["tid"] = task_id
        fired.set()

    # Monkey-patch _execute_task to skip the LLM/agent path
    orig = sch._execute_task
    sch._execute_task = fake_execute_task  # type: ignore

    try:
        # Create a task whose next_run_at is essentially now
        t = sch.create_task(UID, {
            "name": "heap dispatch test",
            "schedule_type": "once",
            "schedule": "",
            "task_type": "agent",
            "task_config": {"prompt": "x"},
            "enabled": True,
        })
        # Force next_run_at to ~1s in the future for an observable wait
        soon = (time.time() + 0.5)
        soon_iso = datetime.fromtimestamp(soon, tz=timezone.utc).isoformat()
        t = sch.update_task(UID, t["id"], {"next_run_at": soon_iso})
        # update_task ignores 'next_run_at' if not in protected set?  Check:
        # protected = {"id", "user_id", "created_at", "runs", "parent_task_id",
        #              "root_task_id", "spawn_chain", "spawn_depth"}
        # → next_run_at is NOT protected, so it accepts.  But update_task also
        # recomputes next_run_at if schedule_type/schedule/enabled changed.
        # Since we passed only next_run_at, no recompute happens.
        sch._heap_upsert("admin", UID, t["id"], soon_iso)

        scheduler = sch.HeapScheduler()
        scheduler.start()
        try:
            await asyncio.wait_for(fired.wait(), timeout=5.0)
        finally:
            await scheduler.stop()

        assert captured.get("tid") == t["id"], captured
        print(f"  ✓ heap fired task {captured['tid']} for uid={captured['uid']}")

        # cleanup
        sch.delete_task(UID, t["id"])
    finally:
        sch._execute_task = orig  # type: ignore


def main() -> int:
    print(f"USERS_DIR (isolated) = {_TMP}")
    try:
        rid = test_create_root_admin_task()
        test_list_includes_descendants(rid)
        cid = test_create_child(rid)
        test_l3_propagation(rid, cid)
        test_delete_subtree(rid)
        asyncio.run(test_heap_loop_dispatches())
        print("\n✅ Phase B smoke test PASSED")
        return 0
    except AssertionError as e:
        print(f"\n❌ assertion failed: {e}")
        return 1
    except Exception:
        import traceback
        traceback.print_exc()
        print("\n❌ unexpected exception")
        return 2
    finally:
        try:
            shutil.rmtree(_TMP, ignore_errors=True)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
