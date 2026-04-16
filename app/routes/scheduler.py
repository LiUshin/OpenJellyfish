"""Admin routes for scheduled task management (admin + service tasks)."""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from app.deps import get_current_user
from app.services.scheduler import (
    create_task, list_tasks, get_task, update_task, delete_task,
    get_task_runs, get_scheduler,
    create_service_task, list_service_tasks, get_service_task,
    update_service_task, delete_service_task, get_service_task_runs,
    list_all_service_tasks,
)

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
async def api_list_tasks(user=Depends(get_current_user)):
    return list_tasks(user["user_id"])


@router.post("")
async def api_create_task(req: CreateTaskRequest, user=Depends(get_current_user)):
    task = create_task(user["user_id"], req.dict())
    return task


# ── Service task endpoints (must be before /{task_id} catch-all) ──────────

@router.get("/services/all")
async def api_list_all_service_tasks(user=Depends(get_current_user)):
    """List tasks across all services for the current admin."""
    return list_all_service_tasks(user["user_id"])


@router.get("/services/{service_id}")
async def api_list_service_tasks(service_id: str, user=Depends(get_current_user)):
    return list_service_tasks(user["user_id"], service_id)


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
