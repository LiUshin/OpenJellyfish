"""Admin routes for scheduled task management (admin + service tasks)."""

import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel

from app.deps import get_current_user
from app.services.scheduler import (
    create_task, list_tasks, get_task, update_task, delete_task,
    get_task_runs, get_scheduler,
    create_service_task, list_service_tasks, get_service_task,
    update_service_task, delete_service_task, get_service_task_runs,
    list_all_service_tasks,
)
from app.services import scheduler_tree as _st
from app.services import spawn_limits as _sl

router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])


# ── Schemas ───────────────────────────────────────────────────────────────

class CreateTaskRequest(BaseModel):
    name: str
    description: str = ""
    schedule_type: str                  # once | cron | interval
    schedule: str                       # value depends on schedule_type
    task_type: str                      # script | agent
    task_config: Dict[str, Any] = {}
    reply_to: Optional[Dict[str, Any]] = None
    enabled: bool = True
    tz_offset_hours: Optional[float] = None


class UpdateTaskRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    schedule_type: Optional[str] = None
    schedule: Optional[str] = None
    task_type: Optional[str] = None
    task_config: Optional[Dict[str, Any]] = None
    reply_to: Optional[Dict[str, Any]] = None
    enabled: Optional[bool] = None
    tz_offset_hours: Optional[float] = None


class CreateServiceTaskRequest(BaseModel):
    name: str
    description: str = ""
    schedule_type: str
    schedule: str
    task_config: Dict[str, Any] = {}
    reply_to: Optional[Dict[str, Any]] = None
    enabled: bool = True
    tz_offset_hours: Optional[float] = None


class UpdateServiceTaskRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    schedule_type: Optional[str] = None
    schedule: Optional[str] = None
    task_config: Optional[Dict[str, Any]] = None
    reply_to: Optional[Dict[str, Any]] = None
    enabled: Optional[bool] = None
    tz_offset_hours: Optional[float] = None


# ── Admin task endpoints ──────────────────────────────────────────────────

@router.get("")
async def api_list_tasks(
    user=Depends(get_current_user),
    roots_only: bool = Query(
        True,
        description=(
            "Hide spawn descendants (default). The sidebar would explode "
            "if a long-running root spawned many children — descendants "
            "are reachable via the pedigree graph (/scheduler/{id}/tree)."
        ),
    ),
):
    return list_tasks(user["user_id"], roots_only=roots_only)


@router.post("")
async def api_create_task(req: CreateTaskRequest, user=Depends(get_current_user)):
    task = create_task(user["user_id"], req.dict())
    return task


# ── Service task endpoints (must be before /{task_id} catch-all) ──────────

@router.get("/services/all")
async def api_list_all_service_tasks(
    user=Depends(get_current_user),
    roots_only: bool = Query(
        True,
        description=(
            "Hide spawn descendants (default). Same rationale as the "
            "per-service list endpoint."
        ),
    ),
):
    """List tasks across all services for the current admin."""
    return list_all_service_tasks(user["user_id"], roots_only=roots_only)


@router.get("/services/{service_id}")
async def api_list_service_tasks(
    service_id: str,
    user=Depends(get_current_user),
    roots_only: bool = Query(
        True,
        description=(
            "Hide spawn descendants (default). Same rationale as the admin "
            "list endpoint — descendants are reachable via the per-task "
            "pedigree graph endpoint."
        ),
    ),
):
    return list_service_tasks(user["user_id"], service_id,
                              roots_only=roots_only)


@router.post("/services/{service_id}")
async def api_create_service_task(
    service_id: str, req: CreateServiceTaskRequest, user=Depends(get_current_user)
):
    task = create_service_task(user["user_id"], service_id, req.dict())
    return task


@router.get("/services/{service_id}/{task_id}")
async def api_get_service_task(
    service_id: str, task_id: str, user=Depends(get_current_user)
):
    task = get_service_task(user["user_id"], service_id, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@router.put("/services/{service_id}/{task_id}")
async def api_update_service_task(
    service_id: str, task_id: str, req: UpdateServiceTaskRequest,
    user=Depends(get_current_user),
):
    updates = {k: v for k, v in req.dict().items() if v is not None}
    task = update_service_task(user["user_id"], service_id, task_id, updates)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@router.delete("/services/{service_id}/{task_id}")
async def api_delete_service_task(
    service_id: str, task_id: str, user=Depends(get_current_user)
):
    if not delete_service_task(user["user_id"], service_id, task_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"success": True}


@router.get("/services/{service_id}/{task_id}/runs")
async def api_get_service_task_runs(
    service_id: str, task_id: str, user=Depends(get_current_user)
):
    task = get_service_task(user["user_id"], service_id, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return get_service_task_runs(user["user_id"], service_id, task_id)


@router.post("/services/{service_id}/{task_id}/run-now")
async def api_run_service_task_now(
    service_id: str, task_id: str, user=Depends(get_current_user)
):
    task = get_service_task(user["user_id"], service_id, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    get_scheduler().run_service_task_now(user["user_id"], service_id, task_id)
    return {"success": True, "message": "任务已触发，稍后可在运行记录中查看结果"}


# ── Admin task detail endpoints (/{task_id} catch-all, must be last) ──────

@router.get("/{task_id}")
async def api_get_task(task_id: str, user=Depends(get_current_user)):
    task = get_task(user["user_id"], task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@router.put("/{task_id}")
async def api_update_task(task_id: str, req: UpdateTaskRequest, user=Depends(get_current_user)):
    updates = {k: v for k, v in req.dict().items() if v is not None}
    task = update_task(user["user_id"], task_id, updates)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@router.delete("/{task_id}")
async def api_delete_task(task_id: str, user=Depends(get_current_user)):
    if not delete_task(user["user_id"], task_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"success": True}


@router.get("/{task_id}/runs")
async def api_get_runs(task_id: str, user=Depends(get_current_user)):
    task = get_task(user["user_id"], task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return get_task_runs(user["user_id"], task_id)


@router.post("/{task_id}/run-now")
async def api_run_now(task_id: str, user=Depends(get_current_user)):
    """Trigger a task immediately regardless of schedule."""
    task = get_task(user["user_id"], task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    get_scheduler().run_now(user["user_id"], task_id)
    return {"success": True, "message": "任务已触发，稍后可在运行记录中查看结果"}


# ── v2 spawn-tree endpoints ───────────────────────────────────────────────
#
# Pattern: every "tree-shaped" view ships in two flavors —
#   admin scope:    /api/scheduler/{task_id}/...
#   service scope:  /api/scheduler/services/{service_id}/{task_id}/...
# so the frontend can lazily reuse the same component for both.

@router.get("/{task_id}/tree")
async def api_admin_task_tree(
    task_id: str,
    max_depth: int = Query(5, ge=1, le=20),
    user=Depends(get_current_user),
):
    """Return the spawn subtree rooted at ``task_id`` as a nested dict.

    Shape (recursive):
        {"meta": {... full _meta.json ...},
         "children": [{"meta": ..., "children": [...]}], "truncated"?: bool}
    """
    tree = _st.walk_tree("admin", user["user_id"], task_id,
                         max_depth=max_depth)
    if not tree:
        raise HTTPException(status_code=404, detail="任务不存在")
    return tree


@router.get("/{task_id}/children")
async def api_admin_task_children(task_id: str, user=Depends(get_current_user)):
    """Return direct children (one level only) of an admin task."""
    parent_dir = _st.task_path_for("admin", user["user_id"], task_id)
    if not parent_dir:
        raise HTTPException(status_code=404, detail="任务不存在")
    children: List[Dict[str, Any]] = []
    for sub in _st.iter_child_dirs(parent_dir):
        m = _st.load_task_meta(sub)
        if m:
            # Drop heavy `runs` for list payload; clients fetch /runs separately
            m = {k: v for k, v in m.items() if k != "runs"}
            m["run_count"] = len((_st.load_task_meta(sub) or {}).get("runs", []))
            children.append(m)
    children.sort(key=lambda c: c.get("created_at", ""))
    return children


@router.get("/{task_id}/ancestors")
async def api_admin_task_ancestors(task_id: str, user=Depends(get_current_user)):
    """Return the chain root → … → parent of ``task_id`` (excluding the task itself).

    Reads ``spawn_chain`` from the task meta (cheap; no tree walk needed).
    """
    task = get_task(user["user_id"], task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    chain = task.get("spawn_chain") or []
    out: List[Dict[str, Any]] = []
    for ancestor_id in chain:
        a_dir = _st.task_path_for("admin", user["user_id"], ancestor_id)
        if not a_dir:
            continue
        m = _st.load_task_meta(a_dir)
        if m:
            out.append({k: v for k, v in m.items() if k != "runs"})
    return out


@router.get("/quotas/{root_task_id}")
async def api_admin_chain_quota(
    root_task_id: str, user=Depends(get_current_user),
):
    """Read-only spawn quota status for an admin task's chain (root id)."""
    q = _sl.peek_chain_quota("admin", user["user_id"], root_task_id)
    return q.as_dict()


class MigrateAdminRequest(BaseModel):
    dry_run: bool = True


@router.post("/admin/migrate")
async def api_admin_migrate_v1_to_v2(
    req: MigrateAdminRequest, user=Depends(get_current_user),
):
    """One-shot lazy-migrate trigger for the current user's tasks.

    With ``dry_run=true`` (default) returns a list of legacy files that *would*
    be migrated.  With ``dry_run=false`` performs the migration and returns
    the new task dirs.  Idempotent — already-migrated tasks are a no-op.
    """
    legacy = _st.find_legacy_task_files("admin", user["user_id"])
    if req.dry_run:
        return {
            "dry_run": True,
            "scope": "admin",
            "user_id": user["user_id"],
            "legacy_count": len(legacy),
            "legacy_files": [os.path.basename(p) for p in legacy],
        }
    migrated: List[str] = []
    for f in legacy:
        task_id = os.path.splitext(os.path.basename(f))[0]
        dest = _st.migrate_legacy_task("admin", user["user_id"], task_id)
        if dest:
            migrated.append(task_id)
    return {
        "dry_run": False,
        "scope": "admin",
        "user_id": user["user_id"],
        "migrated_count": len(migrated),
        "migrated_task_ids": migrated,
    }


# ── Service-scope tree endpoints (mirror admin shape) ─────────────────────

@router.get("/services/{service_id}/{task_id}/tree")
async def api_service_task_tree(
    service_id: str, task_id: str,
    max_depth: int = Query(5, ge=1, le=20),
    user=Depends(get_current_user),
):
    tree = _st.walk_tree("service", user["user_id"], task_id,
                         service_id, max_depth=max_depth)
    if not tree:
        raise HTTPException(status_code=404, detail="任务不存在")
    return tree


@router.get("/services/{service_id}/{task_id}/children")
async def api_service_task_children(
    service_id: str, task_id: str, user=Depends(get_current_user),
):
    parent_dir = _st.task_path_for("service", user["user_id"],
                                   task_id, service_id)
    if not parent_dir:
        raise HTTPException(status_code=404, detail="任务不存在")
    children: List[Dict[str, Any]] = []
    for sub in _st.iter_child_dirs(parent_dir):
        m = _st.load_task_meta(sub)
        if m:
            m = {k: v for k, v in m.items() if k != "runs"}
            m["run_count"] = len((_st.load_task_meta(sub) or {}).get("runs", []))
            children.append(m)
    children.sort(key=lambda c: c.get("created_at", ""))
    return children


@router.get("/services/{service_id}/{task_id}/ancestors")
async def api_service_task_ancestors(
    service_id: str, task_id: str, user=Depends(get_current_user),
):
    task = get_service_task(user["user_id"], service_id, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    chain = task.get("spawn_chain") or []
    out: List[Dict[str, Any]] = []
    for ancestor_id in chain:
        a_dir = _st.task_path_for("service", user["user_id"],
                                  ancestor_id, service_id)
        if not a_dir:
            continue
        m = _st.load_task_meta(a_dir)
        if m:
            out.append({k: v for k, v in m.items() if k != "runs"})
    return out


@router.get("/services/{service_id}/quotas/{root_task_id}")
async def api_service_chain_quota(
    service_id: str, root_task_id: str, user=Depends(get_current_user),
):
    q = _sl.peek_chain_quota("service", user["user_id"], root_task_id,
                             service_id=service_id)
    return q.as_dict()
