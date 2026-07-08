"""LiveKit 实时语音插件 —— Core 侧路由。

两类端点:

A) 管理员侧(``get_current_user`` 鉴权):
   - ``GET  /api/voice/live/status``  探活:LiveKit 是否已配置 + 公网 url
   - ``POST /api/voice/live/token``   为某会话签发 LiveKit 接入令牌(room 绑定会话)
   - ``GET  /api/voice/live/config``  读取前台 Copilot 配置
   - ``PUT  /api/voice/live/config``  更新前台配置
   - ``DELETE /api/voice/live/config`` 恢复默认

B) Worker 侧(桥接令牌 ``X-Bridge-Token`` 鉴权,见 live_token.verify_bridge_token):
   - ``GET  /api/voice/live/session``  Worker 启动引导:返回 admin/会话/前台配置/供应商凭据
   - ``POST /api/voice/live/delegate`` Worker 委派任务:复用 /api/chat 的 agent 流(SSE)

Worker 永远不接触管理员真实登录 token —— 仅持有 Core 在签发 LiveKit 令牌时
塞进参与者 metadata 的桥接令牌。
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse

from app.deps import get_current_user
from app.voice.live_token import (
    mint_livekit_token,
    create_bridge_token,
    verify_bridge_token,
    livekit_server_config,
    is_livekit_configured,
)
from app.services.voice_agent_config import (
    get_voice_agent_config,
    update_voice_agent_config,
    reset_voice_agent_config,
)

router = APIRouter(prefix="/api/voice/live", tags=["voice-live"])


# ── 工具 ────────────────────────────────────────────────────────────

def _room_name(admin_id: str, conv_id: str) -> str:
    """会话 → 房间名(确定性:重连回到同一 room)。

    room 名编码身份 ``jf-<admin_id>-<conv_id>``,非字母数字字符替换为 ``_``。
    LiveKit 房间名对字符较宽容,这里仍做收敛以避免歧义。
    """
    safe_admin = re.sub(r"[^A-Za-z0-9]", "", admin_id)[:32]
    safe_conv = re.sub(r"[^A-Za-z0-9]", "_", conv_id)[:48]
    return f"jf-{safe_admin}-{safe_conv}"


async def get_voice_worker_session(x_bridge_token: Optional[str] = Header(None)):
    """Worker 鉴权依赖:校验桥接令牌,返回会话上下文。"""
    data = verify_bridge_token(x_bridge_token or "")
    if not data or not data.get("admin_id") or not data.get("conv_id"):
        raise HTTPException(status_code=401, detail="Invalid or missing bridge token")
    return data


# ── A) 管理员侧 ─────────────────────────────────────────────────────

@router.get("/status")
async def voice_live_status(user=Depends(get_current_user)):
    cfg = livekit_server_config()
    return {
        "configured": is_livekit_configured(),
        "url": cfg["url"],
    }


@router.post("/token")
async def voice_live_token(req: dict, user=Depends(get_current_user)):
    """为某会话签发 LiveKit 接入令牌。

    body: ``{conversation_id, model?, capabilities?}``
    返回: ``{url, token, room, identity}``
    """
    if not is_livekit_configured():
        raise HTTPException(status_code=503, detail="LiveKit 未配置(缺 LIVEKIT_URL/API_KEY/API_SECRET)")

    admin_id = user["user_id"]
    conv_id = (req.get("conversation_id") or "").strip()
    if not conv_id:
        raise HTTPException(status_code=400, detail="缺少 conversation_id")
    model = req.get("model") or None
    capabilities = req.get("capabilities") or []

    cfg = livekit_server_config()
    room = _room_name(admin_id, conv_id)
    identity = f"admin-{admin_id}"

    # 桥接令牌塞进参与者 metadata,Worker 加入后读取并回调 Core。
    bridge = create_bridge_token(admin_id, conv_id, model=model, capabilities=capabilities)
    token = mint_livekit_token(
        cfg["api_key"], cfg["api_secret"],
        room=room,
        identity=identity,
        name=user.get("username", admin_id),
        metadata=bridge,
    )
    return {"url": cfg["url"], "token": token, "room": room, "identity": identity}


@router.get("/config")
async def voice_live_get_config(user=Depends(get_current_user)):
    return get_voice_agent_config(user["user_id"])


@router.put("/config")
async def voice_live_put_config(req: dict, user=Depends(get_current_user)):
    return update_voice_agent_config(user["user_id"], req or {})


@router.delete("/config")
async def voice_live_reset_config(user=Depends(get_current_user)):
    return reset_voice_agent_config(user["user_id"])


# ── B) Worker 侧 ────────────────────────────────────────────────────

def _provider_creds(admin_id: str) -> dict:
    """汇总语音 Worker 所需的供应商凭据(一期默认 OpenAI 系)。

    STT/TTS 走 capability 优先逻辑(可被 STT_API_KEY/TTS_API_KEY 覆盖),
    LLM 走 OpenAI 通用 chat 配置。
    """
    from app.core.api_config import (
        get_api_config,
        get_openai_llm_config,
        get_provider_credentials,
    )

    out: dict = {}
    try:
        k, b = get_api_config("stt", user_id=admin_id)
        out["stt"] = {"provider": "openai", "api_key": k, "base_url": b}
    except RuntimeError:
        out["stt"] = None
    try:
        k, b = get_api_config("tts", user_id=admin_id)
        out["tts"] = {"provider": "openai", "api_key": k, "base_url": b}
    except RuntimeError:
        out["tts"] = None
    llm_key, llm_base = get_openai_llm_config(user_id=admin_id)
    out["llm"] = {
        "provider": "openai",
        "api_key": llm_key,
        "base_url": llm_base or "https://api.openai.com/v1",
    }
    # Fish Audio TTS 凭据(全局环境变量,非 per-user)。
    # FISH_API_KEY: 必填(选用 fishaudio TTS 时);FISH_REFERENCE_ID: 默认音色;FISH_MODEL: 默认 s1
    fish_key = os.environ.get("FISH_API_KEY") or os.environ.get("FISH_AUDIO_API_KEY")
    if fish_key:
        out["fish"] = {
            "api_key": fish_key,
            "reference_id": os.environ.get("FISH_REFERENCE_ID")
            or os.environ.get("FISH_AUDIO_VOICE_ID")
            or "",
            "model": os.environ.get("FISH_MODEL", "s1"),
            # STT(/v1/asr)与 TTS 共用同一 key,可选自定义网关
            "base_url": os.environ.get("FISH_BASE_URL", ""),
        }
    else:
        out["fish"] = None

    # Bedrock LLM 凭据(per-user,走 OpenAI 兼容端点用 Bearer key)。
    # 语音前台 LLM 选 bedrock 时使用;复用主 agent 的 Bedrock API Key。
    try:
        bcreds = get_provider_credentials("bedrock", user_id=admin_id)
        if bcreds.get("api_key"):
            out["bedrock"] = {
                "api_key": bcreds["api_key"],
                "region": bcreds.get("region") or "us-east-1",
            }
        else:
            out["bedrock"] = None
    except Exception:
        out["bedrock"] = None
    # Kimi/Moonshot(OpenAI 兼容,语音前台可直接用 openai.LLM 走其端点)
    try:
        kcreds = get_provider_credentials("kimi", user_id=admin_id)
        if kcreds.get("api_key"):
            out["kimi"] = {
                "api_key": kcreds["api_key"],
                "base_url": kcreds.get("base_url") or "https://api.moonshot.cn/v1",
            }
        else:
            out["kimi"] = None
    except Exception:
        out["kimi"] = None
    # Anthropic 原生(语音 worker 用 livekit anthropic 插件)
    try:
        acreds = get_provider_credentials("anthropic", user_id=admin_id)
        if acreds.get("api_key"):
            out["anthropic"] = {
                "api_key": acreds["api_key"],
                "base_url": acreds.get("base_url") or "",
            }
        else:
            out["anthropic"] = None
    except Exception:
        out["anthropic"] = None
    # MiniMax LLM 走 Anthropic 兼容端点,只需 api_key(group_id 仅 TTS/Video 用)
    try:
        mcreds = get_provider_credentials("minimax", user_id=admin_id)
        if mcreds.get("api_key"):
            out["minimax"] = {
                "api_key": mcreds["api_key"],
                "base_url": "https://api.minimax.io/anthropic",
            }
        else:
            out["minimax"] = None
    except Exception:
        out["minimax"] = None

    # 阿里云 DashScope(Paraformer STT 等)凭据,全局环境变量。
    dashscope_key = os.environ.get("DASHSCOPE_API_KEY")
    out["dashscope"] = {"api_key": dashscope_key} if dashscope_key else None

    return out


@router.get("/session")
async def voice_live_session(sess=Depends(get_voice_worker_session)):
    """Worker 启动引导:一次拿全 —— 会话标识 + 前台配置 + 供应商凭据。"""
    admin_id = sess["admin_id"]
    return {
        "admin_id": admin_id,
        "conversation_id": sess["conv_id"],
        "model": sess.get("model"),
        "capabilities": sess.get("capabilities") or [],
        "config": get_voice_agent_config(admin_id),
        "providers": _provider_creds(admin_id),
    }


@router.post("/delegate")
async def voice_live_delegate(req: dict, sess=Depends(get_voice_worker_session)):
    """Worker 委派一段用户指令给 OpenJellyfish agent,流式返回(SSE)。

    复用 /api/chat 的 ``_stream_agent``:
    - 与文字对话**共享同一 thread_id**(``{admin_id}-{conv_id}``)→ 语音/文字共用上下文;
    - ``yolo=True`` 自动批准 HITL,语音场景不弹审批卡;
    - 任务轮次持久化进**同一 conversation**,刷新后可见。

    body: ``{message: str}``
    """
    from app.routes.chat import (
        _create_user_agent_bounded, _stream_agent, _sse_response,
    )
    from app.services.conversations import save_message
    from app.services.prompt import stamp_message

    admin_id = sess["admin_id"]
    conv_id = sess["conv_id"]
    message = (req.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="缺少 message")

    model = sess.get("model")
    capabilities = sess.get("capabilities") or []

    save_message(admin_id, conv_id, "user", message)

    agent = await _create_user_agent_bounded(
        admin_id, model=model, capabilities=capabilities, username=admin_id
    )
    thread_id = f"{admin_id}-{conv_id}"
    config = {"configurable": {"thread_id": thread_id}}
    user_content = stamp_message(message, admin_id)
    return _sse_response(
        _stream_agent(
            agent,
            {"messages": [{"role": "user", "content": user_content}]},
            config, admin_id, conv_id, yolo=True,
        )
    )


# ── C) LLM 网关(OpenAI 兼容,专供语音 Worker)────────────────────────
#
# 语音 Worker 的实时 LLM 不再各自对接 OpenAI/Anthropic/Bedrock —— 而是统一指向
# 本端点(一个 OpenAI 兼容的 /chat/completions)。端点内部复用 openjellyfish 的
# ``_resolve_model``(Bedrock 自然走 Invoke REST),把任意 LangChain chat model 的
# 流式输出(含 tool_calls)转成 OpenAI SSE chunk 回吐。这样:
#   • 模型选择/凭据/Bedrock Invoke 全在 Core 一处,与主 Chat 完全一致;
#   • Worker 仍用原生 ``openai.LLM`` → LiveKit 原生 function_tool / 填充语全保住;
#   • Worker 不再依赖 langchain / 各供应商插件。
# 鉴权: ``Authorization: Bearer <桥接令牌>``(Worker 把 bridge_token 当 api_key)。

logger = logging.getLogger("voice-live")


async def get_voice_llm_user(authorization: Optional[str] = Header(None)):
    """OAI 网关鉴权:``Authorization: Bearer <桥接令牌>`` → 会话上下文。

    openai 客户端(Worker 侧)始终把 api_key 以 Bearer 形式放在 Authorization 头,
    这里把它当作桥接令牌校验。
    """
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    data = verify_bridge_token(token)
    if not data or not data.get("admin_id"):
        raise HTTPException(status_code=401, detail="Invalid or missing bridge token")
    return data


def _extract_text(content: Any) -> str:
    """从 LangChain/OAI 消息 content 中提取纯文本(忽略 thinking / tool_use 块)。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text" and block.get("text"):
                    parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return ""


def _oai_to_lc_messages(messages: List[dict]):
    """OpenAI chat 消息 → LangChain 消息(含 assistant.tool_calls / tool 结果)。"""
    from langchain_core.messages import (
        SystemMessage, HumanMessage, AIMessage, ToolMessage,
    )

    out: list = []
    for m in messages or []:
        role = m.get("role")
        text = _extract_text(m.get("content"))
        if role == "system":
            out.append(SystemMessage(content=text))
        elif role == "user":
            out.append(HumanMessage(content=text))
        elif role == "assistant":
            tool_calls = []
            for tc in (m.get("tool_calls") or []):
                fn = tc.get("function") or {}
                raw_args = fn.get("arguments")
                try:
                    parsed = (
                        json.loads(raw_args)
                        if isinstance(raw_args, str) and raw_args.strip()
                        else (raw_args or {})
                    )
                except Exception:
                    parsed = {}
                tool_calls.append({
                    "name": fn.get("name", ""),
                    "args": parsed if isinstance(parsed, dict) else {},
                    "id": tc.get("id", ""),
                })
            if tool_calls:
                out.append(AIMessage(content=text, tool_calls=tool_calls))
            else:
                out.append(AIMessage(content=text))
        elif role == "tool":
            out.append(ToolMessage(content=text, tool_call_id=m.get("tool_call_id", "")))
        elif text:
            out.append(HumanMessage(content=text))
    return out


def _chunk(cmpl_id: str, created: int, model: str, delta: dict, finish: Optional[str] = None) -> str:
    """组装一帧 OpenAI ``chat.completion.chunk`` SSE。"""
    payload = {
        "id": cmpl_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/llm/v1/chat/completions")
async def voice_llm_chat_completions(req: dict, sess=Depends(get_voice_llm_user)):
    """OpenAI 兼容 ``/chat/completions`` —— 语音 Worker 的统一 LLM 入口。

    支持 ``stream=True`` 的 SSE(LiveKit openai 插件固定用流式),透传 ``tools``
    给底层模型并把 tool_calls 增量转回 OpenAI 格式。
    """
    from app.services.agent import _resolve_model, THINKING_MODEL_CONFIG

    admin_id = sess["admin_id"]
    model_id = (req.get("model") or "").strip() or "openai:gpt-4o-mini"
    messages = req.get("messages") or []
    tools = req.get("tools") or []

    # 实时语音不开扩展思考:thinking 变体取其 base 模型(查 openjellyfish 同一张表)。
    resolve_id = model_id
    cfg = THINKING_MODEL_CONFIG.get(model_id)
    if cfg and cfg.get("base_model") and cfg["base_model"] != model_id:
        resolve_id = cfg["base_model"]

    try:
        model = _resolve_model(resolve_id, user_id=admin_id)
        if isinstance(model, str):
            from langchain.chat_models import init_chat_model
            model = init_chat_model(model=model)
        if tools:
            model = model.bind_tools(tools)
    except Exception as e:  # noqa: BLE001
        logger.exception("语音 LLM 网关装配失败 model=%s: %s", model_id, e)
        raise HTTPException(status_code=400, detail=f"LLM 装配失败: {e}")

    lc_messages = _oai_to_lc_messages(messages)
    created = int(time.time())
    cmpl_id = f"chatcmpl-voice-{created}"

    logger.info(
        "语音 LLM 网关: admin=%s model=%s(resolve=%s) msgs=%d tools=%d",
        admin_id, model_id, resolve_id, len(messages), len(tools),
    )

    async def gen():
        saw_tool = False
        # 首帧带 role,符合 OpenAI 约定。
        yield _chunk(cmpl_id, created, model_id, {"role": "assistant", "content": ""})
        try:
            async for ch in model.astream(lc_messages):
                delta: dict = {}
                text = _extract_text(getattr(ch, "content", ""))
                if text:
                    delta["content"] = text

                oai_tcs = []
                for tc in (getattr(ch, "tool_call_chunks", None) or []):
                    saw_tool = True
                    entry: dict = {"index": tc.get("index") or 0}
                    if tc.get("id"):
                        entry["id"] = tc["id"]
                        entry["type"] = "function"
                    fn: dict = {}
                    if tc.get("name"):
                        fn["name"] = tc["name"]
                    if tc.get("args"):
                        fn["arguments"] = tc["args"]
                    if fn:
                        entry["function"] = fn
                    oai_tcs.append(entry)
                if oai_tcs:
                    delta["tool_calls"] = oai_tcs

                if delta:
                    yield _chunk(cmpl_id, created, model_id, delta)
        except Exception as e:  # noqa: BLE001
            logger.exception("语音 LLM 网关流式出错: %s", e)
            err = {"error": {"message": str(e), "type": "server_error"}}
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
            return

        yield _chunk(cmpl_id, created, model_id, {}, finish="tool_calls" if saw_tool else "stop")
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
