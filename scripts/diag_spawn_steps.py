"""Pull the latest runs and grep for any spawn_child_task tool activity.

Targeted at the "spawn ran but no children" diagnosis.  Prints the
tool_call args + tool_result content for every spawn-related step.
"""
from __future__ import annotations

import json
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import scheduler as sch  # noqa: E402
from app.services import scheduler_tree as st  # noqa: E402

USERS_DIR = "users"

for uid in sorted(os.listdir(USERS_DIR)):
    udir = os.path.join(USERS_DIR, uid)
    if not os.path.isdir(udir):
        continue
    for root in st.list_root_tasks("admin", uid):
        rid = root.get("id")
        runs = sch.get_task_runs(uid, rid)
        for run_idx, r in enumerate(runs):
            steps = r.get("steps") or []
            spawn_hits = [
                s for s in steps
                if (s.get("type") in ("tool_call", "tool_result")
                    and "spawn" in (s.get("tool") or "").lower())
            ]
            if not spawn_hits:
                continue
            print(f"\n===== uid={uid}  task={rid}  run #{run_idx+1}/{len(runs)} =====")
            print(f"  status={r.get('status')}  started={r.get('started_at')}")
            for s in spawn_hits:
                print(f"  --- step type={s.get('type')} tool={s.get('tool')} ---")
                if s.get("type") == "tool_call":
                    args = s.get("args_preview") or "(empty)"
                    print(f"    args_preview: {args[:600]}")
                else:  # tool_result
                    res = s.get("result_preview") or "(empty)"
                    print(f"    result_preview: {res[:1200]}")
            output = (r.get("output") or "").strip()
            if output:
                print(f"  --- final output (truncated) ---")
                print(f"    {output[:300]}")
        if not any(
            (s.get("type") in ("tool_call", "tool_result")
             and "spawn" in (s.get("tool") or "").lower())
            for r in runs for s in (r.get("steps") or [])
        ):
            print(f"  task {rid}: no spawn-related steps in any of {len(runs)} run(s)")
