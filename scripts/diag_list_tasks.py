"""Diagnose what list_tasks() actually returns on real local data.

Helps debug "UI doesn't show spawn lineage" — checks whether the
backend list endpoint is emitting v2 fields and child rows.
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
print(f"USERS_DIR = {os.path.abspath(USERS_DIR)}\n")

for uid in sorted(os.listdir(USERS_DIR)):
    udir = os.path.join(USERS_DIR, uid)
    if not os.path.isdir(udir):
        continue

    print(f"===== uid={uid} =====")

    # 1. Raw root task discovery
    roots = st.list_root_tasks("admin", uid)
    print(f"  list_root_tasks: {len(roots)} root task(s)")
    for r in roots:
        rid = r.get("id")
        descs = st.list_descendants("admin", uid, rid, include_root=False)
        print(f"    - root {rid} ({r.get('name')!r})  → {len(descs)} descendant(s)")

    # 2. What list_tasks actually returns (this is what the API serves)
    items = sch.list_tasks(uid)
    print(f"  list_tasks() (what /api/scheduler returns): {len(items)} item(s)")
    for t in items:
        v2 = {
            "parent": t.get("parent_task_id"),
            "root":   t.get("root_task_id"),
            "depth":  t.get("spawn_depth"),
            "children": t.get("children_count"),
            "desc":   t.get("descendants_count"),
        }
        keys_present = [k for k, v in v2.items() if v is not None]
        print(f"    - {t.get('id')}  name={t.get('name')!r}")
        print(f"        v2 fields present: {keys_present or '⚠️ NONE'}")
        if "parent" in keys_present:
            print(f"        parent={v2['parent']} root={v2['root']} depth={v2['depth']} "
                  f"children={v2['children']} desc={v2['desc']}")

    # 3. Service tasks too
    services_dir = os.path.join(udir, "services")
    if os.path.isdir(services_dir):
        for svc in sorted(os.listdir(services_dir)):
            svc_roots = st.list_root_tasks("service", uid, svc)
            if svc_roots:
                print(f"  service {svc}: {len(svc_roots)} root task(s)")
                for r in svc_roots:
                    rid = r.get("id")
                    descs = st.list_descendants("service", uid, rid, svc,
                                                include_root=False)
                    print(f"    - root {rid} ({r.get('name')!r})  → {len(descs)} descendant(s)")
    print()
