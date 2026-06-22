"""Phase C smoke test: spawn_child_task tool + memory tree-read tools.

Verifies:
  1. spawn_child_task **outside** ContextVar → clear error (no orphan task)
  2. spawn_child_task **inside** ContextVar → child created, lineage wired
  3. spawn rate limit kicks in after the configured threshold
  4. read_task_tree / list_task_trees render the tree readable to humans
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

_TMP = tempfile.mkdtemp(prefix="sched_c_smoke_")
os.environ["SCHED_SPAWN_RATE_PER_HOUR"] = "3"  # tiny limit so we hit it fast

from app.core import security as sec  # noqa: E402
sec.USERS_DIR = _TMP
os.makedirs(_TMP, exist_ok=True)

from app.services import scheduler as sch  # noqa: E402
from app.services import scheduler_tree as st  # noqa: E402
from app.services import spawn_limits  # noqa: E402
from app.services.tools import create_spawn_child_task_tool  # noqa: E402
from app.services.memory_tools import create_admin_memory_tools  # noqa: E402

UID = "user_smoke_c"


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def test_spawn_outside_context() -> None:
    _section("1. spawn_child_task outside execution context → clear error")
    tool = create_spawn_child_task_tool()
    out = tool.invoke({
        "name": "should_fail",
        "prompt": "noop",
    })
    assert "错误" in out and "spawn_child_task" in out, out
    print(f"  ✓ refused with: {out[:90]}...")


def _make_root_task() -> str:
    t = sch.create_task(UID, {
        "name": "smoke-c-root",
        "schedule_type": "interval",
        "schedule": "60",
        "task_type": "agent",
        "task_config": {"prompt": "x", "capabilities": ["humanchat"]},
        "enabled": True,
    })
    return t["id"]


def test_spawn_inside_context_creates_child() -> str:
    _section("2. spawn_child_task inside ContextVar → child wired correctly")
    root_id = _make_root_task()
    parent = sch._load_task(UID, root_id)
    ctx = sch._build_task_context_from_meta("admin", UID, parent)
    token = sch._current_task_var.set(ctx)

    try:
        tool = create_spawn_child_task_tool()
        out = tool.invoke({
            "name": "smoke child A",
            "prompt": "do follow-up A",
            "schedule_type": "once",
            "schedule": "",
            "reason": "phase C smoke",
        })
        print(f"  spawn return:\n    {out.replace(chr(10), chr(10)+'    ')}")
        assert "已派生子任务" in out, out
        assert root_id in out, out

        # Verify on disk
        children = st.list_descendants("admin", UID, root_id)
        assert len(children) == 1, children
        child = children[0]
        assert child["parent_task_id"] == root_id
        assert child["spawn_chain"] == [root_id]
        assert child["spawn_depth"] == 1
        assert child["spawn_reason"] == "phase C smoke"
        # capabilities inherited from parent
        assert "humanchat" in (child.get("task_config", {}).get("capabilities") or [])
        print(f"  ✓ child {child['id']} inherits capabilities, lineage correct")
    finally:
        sch._current_task_var.reset(token)
    return root_id


def test_spawn_rate_limit(root_id: str) -> None:
    _section("3. spawn rate limit (limit=3 set via env) blocks the 4th")
    spawn_limits.reset_chain("admin", UID, root_id)

    parent = sch._load_task(UID, root_id)
    ctx = sch._build_task_context_from_meta("admin", UID, parent)
    token = sch._current_task_var.set(ctx)
    try:
        tool = create_spawn_child_task_tool()
        results = []
        for i in range(4):
            results.append(tool.invoke({
                "name": f"burst child {i}",
                "prompt": "rate-test",
            }))
        ok_count = sum(1 for r in results if "已派生子任务" in r)
        blocked = [r for r in results if "派生频次超限" in r]
        assert ok_count == 3, f"expected 3 ok, got {ok_count}: {results}"
        assert len(blocked) == 1, f"expected 1 blocked, got {len(blocked)}"
        print(f"  ✓ 3 spawned, 1 blocked → {blocked[0][:100]}...")
    finally:
        sch._current_task_var.reset(token)


def test_memory_tree_tools(root_id: str) -> None:
    _section("4. memory tools render task tree readable")
    mem_tools = create_admin_memory_tools(UID)
    tool_map = {t.name: t for t in mem_tools}
    assert "list_task_trees" in tool_map, list(tool_map)
    assert "read_task_tree" in tool_map, list(tool_map)

    out_list = tool_map["list_task_trees"].invoke({"scope": "admin"})
    print(f"  list_task_trees → \n    {out_list.replace(chr(10), chr(10)+'    ')}")
    assert root_id in out_list, out_list

    out_tree = tool_map["read_task_tree"].invoke({
        "task_id": root_id, "scope": "admin", "max_depth": 5
    })
    print(f"  read_task_tree → \n    {out_tree.replace(chr(10), chr(10)+'    ')}")
    assert root_id in out_tree, out_tree
    # Should show at least one child rendered with deeper indent
    child_ids = [m["id"] for m in st.list_descendants("admin", UID, root_id)]
    assert any(cid in out_tree for cid in child_ids), child_ids


def main() -> int:
    print(f"USERS_DIR (isolated) = {_TMP}")
    print(f"SCHED_SPAWN_RATE_PER_HOUR = {os.environ['SCHED_SPAWN_RATE_PER_HOUR']}")
    try:
        test_spawn_outside_context()
        rid = test_spawn_inside_context_creates_child()
        test_spawn_rate_limit(rid)
        test_memory_tree_tools(rid)
        print("\n✅ Phase C smoke test PASSED")
        return 0
    except AssertionError as e:
        print(f"\n❌ assertion failed: {e}")
        return 1
    except Exception:
        import traceback
        traceback.print_exc()
        return 2
    finally:
        try:
            shutil.rmtree(_TMP, ignore_errors=True)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
