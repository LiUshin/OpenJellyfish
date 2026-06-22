"""Phase D smoke: 5 new v2 endpoints + auth/route shape sanity."""
from __future__ import annotations

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

_TMP = tempfile.mkdtemp(prefix="sched_d_smoke_")
from app.core import security as sec  # noqa: E402
sec.USERS_DIR = _TMP
os.makedirs(_TMP, exist_ok=True)

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.routes import scheduler as routes_scheduler  # noqa: E402
from app.services import scheduler as sch  # noqa: E402

UID = "user_smoke_d"


def _override_user():
    return {"user_id": UID, "username": "smoke", "role": "admin"}


# Mount the router with an auth override so endpoints are reachable without JWT.
app = FastAPI()
app.include_router(routes_scheduler.router)
from app.deps import get_current_user  # noqa: E402
app.dependency_overrides[get_current_user] = _override_user
client = TestClient(app)


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def _make_root() -> str:
    return sch.create_task(UID, {
        "name": "smoke-d-root",
        "schedule_type": "interval",
        "schedule": "60",
        "task_type": "agent",
        "task_config": {"prompt": "x"},
        "enabled": True,
    })["id"]


def _spawn_child(parent_id: str, name: str) -> str:
    parent = sch._load_task(UID, parent_id)
    ctx = sch._build_task_context_from_meta("admin", UID, parent)
    token = sch._current_task_var.set(ctx)
    try:
        child = sch.create_child_task(ctx, {
            "name": name,
            "schedule_type": "once",
            "schedule": "",
            "task_type": "agent",
            "task_config": {"prompt": name},
            "enabled": True,
        })
        return child["id"]
    finally:
        sch._current_task_var.reset(token)


def main() -> int:
    print(f"USERS_DIR (isolated) = {_TMP}")
    try:
        rid = _make_root()
        c1 = _spawn_child(rid, "child A")
        c2 = _spawn_child(rid, "child B")
        gc1 = _spawn_child(c1, "grandchild A1")

        _section("1. GET /{task_id}/tree → walk_tree result")
        r = client.get(f"/api/scheduler/{rid}/tree?max_depth=3")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["meta"]["id"] == rid
        names = [c["meta"]["name"] for c in body["children"]]
        assert "child A" in names and "child B" in names, names
        # grandchild A1 should be nested under child A
        a_node = next(c for c in body["children"] if c["meta"]["id"] == c1)
        assert any(g["meta"]["id"] == gc1 for g in a_node["children"]), a_node
        print(f"  ✓ tree shape: root({rid}) → children={len(body['children'])} → grandchild")

        _section("2. GET /{task_id}/children → 直接子节点列表")
        r = client.get(f"/api/scheduler/{rid}/children")
        assert r.status_code == 200, r.text
        ids = [c["id"] for c in r.json()]
        assert set(ids) == {c1, c2}, ids
        print(f"  ✓ direct children: {ids}")

        _section("3. GET /{task_id}/ancestors → spawn_chain dehydrated")
        r = client.get(f"/api/scheduler/{gc1}/ancestors")
        assert r.status_code == 200, r.text
        anc_ids = [a["id"] for a in r.json()]
        assert anc_ids == [rid, c1], anc_ids
        print(f"  ✓ ancestors of grandchild: {anc_ids}")

        _section("4. GET /quotas/{root_task_id} → peek 配额")
        r = client.get(f"/api/scheduler/quotas/{rid}")
        assert r.status_code == 200, r.text
        q = r.json()
        assert "limit" in q and "current" in q and "remaining" in q, q
        print(f"  ✓ quota dict: {q}")

        _section("5. POST /admin/migrate dry_run=True → 0 legacy files")
        r = client.post("/api/scheduler/admin/migrate",
                        json={"dry_run": True})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["dry_run"] is True
        assert body["legacy_count"] == 0  # all created via v2 path
        print(f"  ✓ migrate dry-run: {body}")

        _section("6. existing GET /{task_id} returns v2 fields")
        r = client.get(f"/api/scheduler/{c1}")
        assert r.status_code == 200, r.text
        b = r.json()
        for k in ("parent_task_id", "root_task_id", "spawn_chain",
                  "spawn_depth", "children_count", "descendants_count",
                  "descendants_summary"):
            assert k in b, f"missing {k} in {list(b)}"
        assert b["parent_task_id"] == rid
        assert b["root_task_id"] == rid
        assert b["spawn_chain"] == [rid]
        assert b["spawn_depth"] == 1
        print(f"  ✓ v2 fields present on detail response: depth={b['spawn_depth']}, "
              f"chain={b['spawn_chain']}")

        _section("7. service-scope endpoints exist & 404 on missing task")
        r = client.get("/api/scheduler/services/missing_svc/missing_task/tree")
        assert r.status_code == 404, r.status_code
        print(f"  ✓ /services/{{}}/.../tree returns 404 on unknown task")

        print("\n✅ Phase D smoke test PASSED")
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
