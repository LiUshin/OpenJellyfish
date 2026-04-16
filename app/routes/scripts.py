import httpx
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
    from app.core.api_config import get_api_config
    try:
        stt_key, stt_base = get_api_config("stt", user_id=user["user_id"])
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    audio_data = await file.read()
    if len(audio_data) == 0:
        raise HTTPException(status_code=400, detail="音频文件为空")
    if len(audio_data) > 25 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="音频文件过大，最大 25MB")

    filename = file.filename or "audio.webm"
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{stt_base}/audio/transcriptions",
                headers={"Authorization": f"Bearer {stt_key}"},
                files={"file": (filename, audio_data, file.content_type or "audio/webm")},
                data={"model": "whisper-1"},
            )
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Whisper API 错误: {resp.text[:200]}")
        return {"text": resp.json().get("text", "").strip()}
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Whisper API 超时")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"语音识别失败: {str(e)}")
