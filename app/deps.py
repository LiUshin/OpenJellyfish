from fastapi import HTTPException, Header, Response
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


async def get_host_user(response: Response, authorization: str = Header(default='')):
    """Only an explicit host key may enter the management API."""
    response.headers['Cache-Control'] = 'no-store'
    import hmac
    from app.core.host_auth import read_key, HOST_ID
    try:
        expected = read_key()
    except (OSError, ValueError):
        raise HTTPException(503, '超管 key 未就绪，请在宿主运行 launcher.py --superadmin-key')
    provided = authorization.removeprefix('Bearer ')
    if not authorization.startswith('Bearer ') or not hmac.compare_digest(provided.encode(), expected.encode()):
        raise HTTPException(401, '超管 key 无效', headers={'WWW-Authenticate': 'Bearer'})
    return {'user_id': HOST_ID, 'role': 'owner'}
