"""Admin routes for managing published services and their API keys."""

from fastapi import APIRouter, HTTPException, Depends

from app.deps import get_current_user
from app.schemas.service import CreateServiceRequest, UpdateServiceRequest, CreateKeyRequest
from app.services.published import (
    create_service, list_services, get_service, update_service, delete_service,
    create_service_key, list_service_keys, delete_service_key,
)

router = APIRouter(prefix="/api/services", tags=["services"])


@router.post("")
async def api_create_service(req: CreateServiceRequest, user=Depends(get_current_user)):
    data = req.dict()
    svc = create_service(user["user_id"], data)
    return svc


@router.get("")
async def api_list_services(user=Depends(get_current_user)):
    return list_services(user["user_id"])


@router.get("/{service_id}")
async def api_get_service(service_id: str, user=Depends(get_current_user)):
    svc = get_service(user["user_id"], service_id)
    if not svc:
        raise HTTPException(status_code=404, detail="Service 不存在")
    return svc


@router.put("/{service_id}")
async def api_update_service(service_id: str, req: UpdateServiceRequest, user=Depends(get_current_user)):
    updates = {k: v for k, v in req.dict().items() if v is not None}

    if "capabilities" in updates:
        existing = get_service(user["user_id"], service_id)
        if existing:
            wc = existing.get("wechat_channel", {})
            if wc.get("enabled") and "humanchat" not in updates["capabilities"]:
                updates["capabilities"].append("humanchat")

    svc = update_service(user["user_id"], service_id, updates)
    if not svc:
        raise HTTPException(status_code=404, detail="Service 不存在")

    from app.services.consumer_agent import clear_consumer_cache
    clear_consumer_cache(admin_id=user["user_id"], service_id=service_id)

    return svc


@router.delete("/{service_id}")
async def api_delete_service(service_id: str, user=Depends(get_current_user)):
    if not delete_service(user["user_id"], service_id):
        raise HTTPException(status_code=404, detail="Service 不存在")
    return {"success": True}


# ── API Keys ─────────────────────────────────────────────────────────

@router.post("/{service_id}/keys")
async def api_create_key(service_id: str, req: CreateKeyRequest, user=Depends(get_current_user)):
    if not get_service(user["user_id"], service_id):
        raise HTTPException(status_code=404, detail="Service 不存在")
    result = create_service_key(user["user_id"], service_id, req.name)
    if not result:
        raise HTTPException(status_code=500, detail="Key 创建失败")
    return result


@router.get("/{service_id}/keys")
async def api_list_keys(service_id: str, user=Depends(get_current_user)):
    if not get_service(user["user_id"], service_id):
        raise HTTPException(status_code=404, detail="Service 不存在")
    return list_service_keys(user["user_id"], service_id)


@router.delete("/{service_id}/keys/{key_id}")
async def api_delete_key(service_id: str, key_id: str, user=Depends(get_current_user)):
    if not delete_service_key(user["user_id"], service_id, key_id):
        raise HTTPException(status_code=404, detail="Key 不存在")
    return {"success": True}
