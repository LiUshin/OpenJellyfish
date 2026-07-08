"""Workspace lock introspection API.

Exposes the currently active admin agent processes and the workspace regions
they hold write locks on, so the operator can see "who is doing what" and, if a
lock is stuck (e.g. an abandoned scheduled task guarded only by TTL), force it
free.

Note: the human operator's own file operations via the FilePanel REST endpoints
are intentionally NOT gated by these locks — the operator is the authority. This
panel exists purely for awareness + manual recovery.
"""

from fastapi import APIRouter, Depends

from app.deps import get_current_user
from app.services import workspace_lock as wl

router = APIRouter(tags=["workspace"])


@router.get("/api/workspace/locks")
async def api_list_workspace_locks(user=Depends(get_current_user)):
    """List active processes + their locked regions for the current admin."""
    return {"processes": wl.list_processes(user["user_id"])}


@router.post("/api/workspace/locks/release")
async def api_release_workspace_lock(payload: dict, user=Depends(get_current_user)):
    """Force-release the locks held by a given owner (scoped to the caller).

    Body: {"owner": "<owner token>"}. Only affects processes owned by the same
    user. This frees the region for other processes but does NOT abort the
    running agent (use /api/chat/stop for that).
    """
    owner = (payload or {}).get("owner", "")
    info = wl.get_process(owner)
    if not info or info.user_id != user["user_id"]:
        return {"status": "not_found"}
    wl.release_locks(owner)
    return {"status": "released", "owner": owner}
