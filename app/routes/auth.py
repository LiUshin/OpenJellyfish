from fastapi import APIRouter, HTTPException, Depends

from app.core import security
from app.schemas.requests import AuthRequest, RegisterRequest
from app.deps import get_current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register")
async def api_register(req: RegisterRequest):
    result = security.register(req.username, req.password, req.reg_key)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/login")
async def api_login(req: AuthRequest):
    result = security.login(req.username, req.password)
    if not result["success"]:
        raise HTTPException(status_code=401, detail=result["error"])
    return result


@router.get("/me")
async def api_me(user=Depends(get_current_user)):
    return user
