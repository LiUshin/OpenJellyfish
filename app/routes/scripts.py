from fastapi import APIRouter, HTTPException, Depends, UploadFile, File

from app.schemas.requests import RunScriptRequest
from app.services.script_runner import run_script
from app.deps import get_current_user
from app.storage import get_storage_service

router = APIRouter(tags=["scripts"])


@router.post("/api/scripts/run")
async def api_run_script(req: RunScriptRequest, user=Depends(get_current_user)):
    storage = get_storage_service()
    with storage.script_execution(user["user_id"], req.script_path) as ctx:
        if "error" in ctx:
            return {"success": False, "stdout": "", "stderr": "", "exit_code": -1,
                    "error": ctx["error"]}
        return run_script(
            script_path=req.script_path,
            scripts_dir=ctx["scripts_dir"],
            input_data=req.input_data,
            args=req.args,
            timeout=req.timeout,
            allowed_read_dirs=[ctx["docs_dir"]],
            allowed_write_dirs=ctx["write_dirs"],
        )


@router.post("/api/audio/transcribe")
async def api_transcribe_audio(file: UploadFile = File(...), user=Depends(get_current_user)):
    """STT 上传转写。模型来源：用户偏好 > catalog defaults（Phase 0 起走 dispatch）。"""
    from app.services.providers import dispatch, ProviderError
    from app.services.model_catalog import resolve_model

    user_id = user["user_id"]

    audio_data = await file.read()
    if len(audio_data) == 0:
        raise HTTPException(status_code=400, detail="音频文件为空")
    if len(audio_data) > 25 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="音频文件过大，最大 25MB")

    model_id = resolve_model("stt", user_id=user_id)
    if not model_id:
        raise HTTPException(status_code=500, detail="未配置可用的 STT 模型")

    filename = file.filename or "audio.webm"
    content_type = file.content_type or "audio/webm"
    try:
        # dispatch 是同步调用；STT 文件较小（<=25MB）放进线程池避免阻塞 event loop。
        import asyncio
        text = await asyncio.to_thread(
            dispatch, "stt", model_id,
            user_id=user_id,
            audio_bytes=audio_data,
            filename=filename,
            content_type=content_type,
        )
        return {"text": text}
    except ProviderError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"语音识别失败: {e}")
