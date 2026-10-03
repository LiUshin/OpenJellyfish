"""Admin-only project groups and their single Markdown brief."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.deps import get_current_user
from app.schemas.requests import (
    CreateProjectRequest,
    UpdateProjectRequest,
    WriteProjectBriefRequest,
)
from app.services.conversations import list_conversations
from app.services.projects import (
    create_project,
    delete_project,
    get_project,
    list_projects,
    read_project_brief,
    search_project,
    update_project,
    write_project_brief,
)


router = APIRouter(prefix="/api/projects", tags=["projects"])


def _detail(user_id: str, project_id: str) -> dict:
    try:
        meta = get_project(user_id, project_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if meta is None:
        raise HTTPException(404, "项目不存在")
    brief = read_project_brief(user_id, project_id)
    count = sum(1 for conversation in list_conversations(user_id)
                if conversation.get("project_id") == project_id)
    from app.services.project_context import project_brief_metrics
    return {**meta, "brief": brief, "conversation_count": count,
            **project_brief_metrics(brief)}


@router.get("")
async def api_list_projects(user=Depends(get_current_user)):
    return list_projects(user["user_id"])


@router.post("")
async def api_create_project(request: CreateProjectRequest, user=Depends(get_current_user)):
    try:
        project = create_project(user["user_id"], request.name)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return _detail(user["user_id"], project["id"])


@router.get("/{project_id}")
async def api_get_project(project_id: str, user=Depends(get_current_user)):
    return _detail(user["user_id"], project_id)


@router.patch("/{project_id}")
async def api_update_project(project_id: str, request: UpdateProjectRequest,
                             user=Depends(get_current_user)):
    try:
        project = update_project(user["user_id"], project_id, request.name)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if project is None:
        raise HTTPException(404, "项目不存在")
    return _detail(user["user_id"], project_id)


@router.put("/{project_id}/brief")
async def api_write_project_brief(project_id: str, request: WriteProjectBriefRequest,
                                  user=Depends(get_current_user)):
    try:
        write_project_brief(user["user_id"], project_id, request.content)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    return _detail(user["user_id"], project_id)


@router.get("/{project_id}/search")
async def api_search_project(project_id: str, q: str = Query(default=""),
                             limit: int = Query(default=30, ge=1, le=50),
                             user=Depends(get_current_user)):
    try:
        return search_project(user["user_id"], project_id, q, limit)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")


@router.delete("/{project_id}")
async def api_delete_project(project_id: str, user=Depends(get_current_user)):
    try:
        deleted = delete_project(user["user_id"], project_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not deleted:
        raise HTTPException(404, "项目不存在")
    return {"success": True}
