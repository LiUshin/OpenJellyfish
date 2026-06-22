"""Targeted single-task diagnostic for the v2 scheduler tree.

Usage:
    python scripts/diag_one_task.py <task_id>
    python scripts/diag_one_task.py <task_id> --uid <user_id>          # admin scope
    python scripts/diag_one_task.py <task_id> --service <service_id>   # service scope

Dumps everything we need to diagnose "frontend doesn't show spawn lineage":
    1. Locate the task on disk (auto-search across all users + scopes)
    2. Print full _meta.json (focus on v2 fields)
    3. Walk the subtree (what GET /scheduler/{id}/tree returns)
    4. Show how this task would appear in list_tasks() output (what the
       sidebar list reads)
    5. Print the spawn_chain → root → list_descendants(root) mapping
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import scheduler as sch  # noqa: E402
from app.services import scheduler_tree as st  # noqa: E402
from app.core.security import USERS_DIR  # noqa: E402


def _hr(title: str = "") -> None:
    if title:
        print(f"\n{'─' * 6} {title} {'─' * (60 - len(title))}")
    else:
        print("─" * 70)


def _find_task(task_id: str, hint_uid: Optional[str],
               hint_service: Optional[str]) -> Optional[tuple]:
    """Brute-force locate (scope, uid, service_id) for a given task_id.

    Returns (scope, uid, service_id_or_None) or None if not found anywhere.
    Honours hints when given (faster + lets caller disambiguate when the
    same task_id exists in multiple scopes — shouldn't happen but be safe).
    """
    if hint_uid and hint_service:
        d = st.task_path_for("service", hint_uid, task_id, hint_service)
        if d:
            return ("service", hint_uid, hint_service)
        return None
    if hint_uid:
        d = st.task_path_for("admin", hint_uid, task_id)
        if d:
            return ("admin", hint_uid, None)
        # also try every service under this user
        services_dir = os.path.join(USERS_DIR, hint_uid, "services")
        if os.path.isdir(services_dir):
            for svc in os.listdir(services_dir):
                d = st.task_path_for("service", hint_uid, task_id, svc)
                if d:
                    return ("service", hint_uid, svc)
        return None

    # Brute-force across every user + scope
    for uid in os.listdir(USERS_DIR):
        udir = os.path.join(USERS_DIR, uid)
        if not os.path.isdir(udir):
            continue
        d = st.task_path_for("admin", uid, task_id)
        if d:
            return ("admin", uid, None)
        services_dir = os.path.join(udir, "services")
        if os.path.isdir(services_dir):
            for svc in os.listdir(services_dir):
                d = st.task_path_for("service", uid, task_id, svc)
                if d:
                    return ("service", uid, svc)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("task_id")
    ap.add_argument("--uid", default=None)
    ap.add_argument("--service", default=None)
    args = ap.parse_args()

    print(f"Looking up task_id={args.task_id} (USERS_DIR={USERS_DIR})")

    found = _find_task(args.task_id, args.uid, args.service)
    if not found:
        print(f"\n❌ Task {args.task_id} NOT found on disk in any scope/user.")
        print("   This means either:")
        print("   - The task was deleted")
        print("   - It only exists in v1 legacy layout that hasn't been migrated yet")
        print("     (try: python scripts/migrate_tasks_to_tree.py --verify)")
        return 1

    scope, uid, svc = found
    task_dir = st.task_path_for(scope, uid, args.task_id, svc)
    print(f"\n✓ Found at: {task_dir}")
    print(f"  scope={scope}  uid={uid}  service_id={svc}")

    # 1) Full _meta.json
    _hr("1. Raw _meta.json")
    meta = st.load_task_meta(task_dir) or {}
    print(json.dumps(meta, ensure_ascii=False, indent=2, default=str))

    # 2) v2 lineage fields
    _hr("2. v2 lineage fields (these MUST be present for UI to show tree)")
    for key in ("id", "parent_task_id", "root_task_id", "spawn_chain",
                "spawn_depth", "children_count", "descendants_count",
                "spawn_reason"):
        v = meta.get(key, "<MISSING>")
        marker = " ⚠️" if v == "<MISSING>" else ""
        print(f"  {key:<22} = {v!r}{marker}")

    # 3) Walk subtree (what GET /scheduler/{id}/tree returns)
    _hr("3. Subtree walk (GET /scheduler/{task_id}/tree shape)")
    tree = st.walk_tree(scope, uid, args.task_id, svc, max_depth=10)
    if tree:
        def _summarise(node, depth=0):
            m = node.get("meta") or {}
            indent = "  " * depth
            print(f"{indent}- {m.get('id')}  name={m.get('name')!r}  "
                  f"depth={m.get('spawn_depth')}  "
                  f"children={m.get('children_count')}  "
                  f"runs={len(m.get('runs', []))}")
            for c in node.get("children", []):
                _summarise(c, depth + 1)
        _summarise(tree)
    else:
        print("  (walk_tree returned None)")

    # 4) Where does this task appear in list_tasks() (admin scope only)?
    _hr("4. Appearance in list_tasks() / list_service_tasks()")
    if scope == "admin":
        items = sch.list_tasks(uid)
    else:
        items = sch.list_service_tasks(uid, svc)
    print(f"  Total items in list = {len(items)}")
    matches = [t for t in items if t.get("id") == args.task_id]
    if not matches:
        print(f"  ❌ {args.task_id} NOT in list — backend list endpoint missed it")
    else:
        m = matches[0]
        print(f"  ✓ Found in list. Fields seen by frontend:")
        for k in ("id", "name", "parent_task_id", "root_task_id",
                  "spawn_chain", "spawn_depth", "children_count",
                  "descendants_count", "enabled", "next_run_at", "run_count"):
            v = m.get(k, "<MISSING>")
            marker = " ⚠️" if v == "<MISSING>" else ""
            print(f"    {k:<22} = {v!r}{marker}")

    # 5) If this is a root, list all descendants flat
    _hr("5. Flat descendant list (what would appear under this root)")
    descs = st.list_descendants(scope, uid, args.task_id, svc, include_root=False)
    print(f"  list_descendants(include_root=False) → {len(descs)} descendant(s)")
    for d in descs:
        print(f"    - {d.get('id')}  depth={d.get('spawn_depth')}  "
              f"parent={d.get('parent_task_id')}  name={d.get('name')!r}")

    # 6) On-disk children directly under this task's directory
    _hr("6. On-disk child directories (ground truth)")
    child_dirs = list(st.iter_child_dirs(task_dir))
    print(f"  iter_child_dirs() → {len(child_dirs)} child dir(s)")
    for cd in child_dirs:
        cm = st.load_task_meta(cd) or {}
        print(f"    - {os.path.basename(cd)}  name={cm.get('name')!r}  "
              f"_meta.json {'EXISTS' if cm else '⚠️ MISSING'}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
