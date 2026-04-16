from fastapi import HTTPException, Header
from typing import Optional

from app.core.security import verify_token


async def get_current_user(authorization: Optional[str] = Header(None)):
    token = ""
    if authorization:
        if authorization.startswith("Bearer "):
            token = authorization[7:]
        else:
            token = authorization
    user = verify_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="无效的 token，请重新登录")
    return user


async def get_service_context(authorization: Optional[str] = Header(None)):
    """Authenticate consumer requests via service API key (sk-svc-...)."""
    from app.services.published import verify_service_key

    key = ""
    if authorization:
        if authorization.startswith("Bearer "):
            key = authorization[7:]
        else:
            key = authorization
    if not key:
        raise HTTPException(status_code=401, detail="Missing API key")
    ctx = verify_service_key(key)
    if not ctx:
        raise HTTPException(status_code=401, detail="Invalid API key")
    svc_config = ctx.get("service_config", {})
    if not svc_config.get("published", True):
        raise HTTPException(status_code=403, detail="Service is not published")
    return ctx
