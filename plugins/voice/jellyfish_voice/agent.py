"""LiveKit Agents Worker 入口 —— JellyfishBot 语音前台。

数据流:
  浏览器 ──WebRTC──> LiveKit Server(SFU) <──音轨── 本 Worker
  本 Worker ──桥接令牌(读自参与者 metadata)──> Core /api/voice/live/session 引导
  前台 Copilot 委派 ──> Core /api/voice/live/delegate(SSE)──> JellyfishBot agent

运行:
  python -m jellyfish_voice.agent dev      # 开发(连本地 LiveKit)
  python -m jellyfish_voice.agent start    # 生产

环境变量:
  LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET  —— 连 SFU 与注册 worker
  VOICE_BRIDGE_SECRET                                  —— 必须与 Core 一致
  JELLYFISH_API_BASE                                   —— Core 内网基址
"""

from __future__ import annotations

import logging

from livekit.agents import AgentServer, AgentSession, JobContext, cli, stt
from livekit.plugins import openai, silero

# 顶部导入即注册 turn-detector 插件(Plugin.register_plugin),这样构建时
# `python -m jellyfish_voice.agent download-files` 才会把 EOU 模型(languages.json 等)
# 下进镜像。否则它只在 _turn_detection() 里懒加载、运行时模型缺失 → 回退 VAD。
# 导入失败不致命(可选依赖),运行期仍会优雅回退 VAD。
try:
    from livekit.plugins import turn_detector  # noqa: F401
except Exception:  # noqa: BLE001
    turn_detector = None

from .config import VoiceSession, api_base, fetch_session
from .copilot import build_copilot
from .orchestration import Orchestrator

logger = logging.getLogger("jellyfish-voice")

server = AgentServer()


def _fish_stt(providers: dict, vad):
    """Fish Audio STT(非流式 /v1/asr),用 VAD 断句后整段识别。"""
    from .fish_stt import STT as FishSTT

    creds = providers.get("fish") or {}
    base = FishSTT(
        api_key=creds.get("api_key") or None,
        base_url=creds.get("base_url") or None,
    )
    return stt.StreamAdapter(stt=base, vad=vad)


def _aliyun_stt(providers: dict, provider_cfg: dict):
    """阿里云 Paraformer 实时流式 STT(DashScope)。端点在国内,适合大陆低延迟。

    使用本地迁移模块 ``aliyun_stt``(而非已停更、锁死 livekit-agents 1.2.9 的
    ``livekit-plugins-aliyun``),以便全栈升级到 livekit-agents 1.6。
    """
    from . import aliyun_stt

    creds = providers.get("dashscope") or {}
    api_key = creds.get("api_key") or None
    model = provider_cfg.get("stt_model") or "paraformer-realtime-v2"
    kwargs = {"model": model, "api_key": api_key}
    if provider_cfg.get("aliyun_vocabulary_id"):
        kwargs["vocabulary_id"] = provider_cfg["aliyun_vocabulary_id"]
    return aliyun_stt.STT(**kwargs)


def _build_stt(providers: dict, provider_cfg: dict, vad):
    """按配置选 STT 供应商:fishaudio(批量) | aliyun(流式) | openai(默认,流式)。

    ⚠️ 用户**显式选择**的供应商初始化失败时**不静默回退 OpenAI**——否则真实原因
    (如缺 key)被 OpenAI 的下游报错掩盖,极难排查。直接打完整堆栈并抛出真实原因。
    """
    vendor = (provider_cfg.get("stt") or "openai").lower()
    if vendor == "fishaudio":
        try:
            return _fish_stt(providers, vad)
        except Exception as e:  # noqa: BLE001
            logger.exception("Fish Audio STT 初始化失败(已显式选择 fishaudio,不回退)")
            raise RuntimeError(f"Fish Audio STT 初始化失败: {e}(检查 FISH_API_KEY)") from e
    if vendor == "aliyun":
        try:
            return _aliyun_stt(providers, provider_cfg)
        except Exception as e:  # noqa: BLE001
            logger.exception("阿里云 STT 初始化失败(已显式选择 aliyun,不回退)")
            raise RuntimeError(f"阿里云 STT 初始化失败: {e}(检查 DASHSCOPE_API_KEY)") from e
    return _openai_stt(providers, provider_cfg)


# OpenAI 合法 STT 模型白名单(同理防跨供应商模型名残留,如阿里 paraformer-realtime-v2)。
_OPENAI_STT_MODELS = {"gpt-4o-mini-transcribe", "gpt-4o-transcribe", "whisper-1"}


def _openai_stt(providers: dict, provider_cfg: dict):
    creds = providers.get("stt") or providers.get("llm") or {}
    model = (provider_cfg.get("stt_model") or "").strip() or "gpt-4o-mini-transcribe"
    if model not in _OPENAI_STT_MODELS:
        logger.warning(
            "OpenAI STT 收到非 OpenAI 模型名 %r(可能是阿里云/Fish 残留),改用 gpt-4o-mini-transcribe",
            model,
        )
        model = "gpt-4o-mini-transcribe"
    return openai.STT(
        model=model,
        api_key=creds.get("api_key") or None,
        base_url=creds.get("base_url") or None,
    )


def _build_llm(provider_cfg: dict, bridge_token: str):
    """装配前台实时 LLM —— 统一指向 Core 的 OpenAI 兼容网关。

    Worker 不再各自对接 OpenAI/Anthropic/Bedrock:``llm_model`` 存与主 Chat 一致的
    catalog id(``provider:model``,如 ``bedrock:claude-sonnet-4-6``),原样作为
    ``model`` 发给 Core ``/api/voice/live/llm/v1/chat/completions``。Core 内部用
    jellyfishbot 的 ``_resolve_model`` 解析(Bedrock 走 Invoke),凭据/模型选择全在
    Core 一处,与主 Chat 完全一致。

    鉴权:把桥接令牌当 api_key —— openai 客户端会以 ``Authorization: Bearer`` 发出,
    Core 网关据此校验身份。这里仍用原生 ``openai.LLM``,LiveKit 原生 function_tool /
    填充语 / 打断全部保留。
    """
    model_id = (provider_cfg.get("llm_model") or "").strip() or "openai:gpt-4o-mini"
    base_url = f"{api_base()}/api/voice/live/llm/v1"
    logger.info("前台 LLM(经 Core OAI 网关) model=%s base_url=%s", model_id, base_url)
    return openai.LLM(model=model_id, api_key=bridge_token, base_url=base_url)


# OpenAI 合法 TTS 模型白名单。`tts_model` 是跨供应商共享字段,切到/回退到 OpenAI 时
# 可能残留其他供应商的模型名(如 Fish 的 s2-pro)→ OpenAI 报 404 model_not_found。
_OPENAI_TTS_MODELS = {"gpt-4o-mini-tts", "tts-1", "tts-1-hd"}


def _openai_tts(providers: dict, provider_cfg: dict):
    creds = providers.get("tts") or providers.get("llm") or {}
    model = (provider_cfg.get("tts_model") or "").strip() or "gpt-4o-mini-tts"
    if model not in _OPENAI_TTS_MODELS:
        logger.warning(
            "OpenAI TTS 收到非 OpenAI 模型名 %r(可能是 Fish/阿里云模型残留),改用 gpt-4o-mini-tts",
            model,
        )
        model = "gpt-4o-mini-tts"
    return openai.TTS(
        model=model,
        voice=provider_cfg.get("tts_voice", "alloy"),
        api_key=creds.get("api_key") or None,
        base_url=creds.get("base_url") or None,
    )


def _fish_tts(providers: dict, provider_cfg: dict):
    """Fish Audio TTS(流式 WebSocket)。音色用 reference_id(配置优先,回退凭据里的默认)。

    ⚠️ 两个版本兼容性坑:
    1. **api_key/base_url 缺失时绝不传 None**:插件签名是 ``NotGivenOr``,传 ``None`` 会被
       当成"已显式给定 None" → 不再回退读 ``FISH_API_KEY`` 环境变量。只在确有值时才放进 kwargs。
    2. **不同插件版本构造签名不一致**(音色参数名 reference_id/voice/voice_id 不一,
       latency_mode 有无)。按 ``inspect.signature`` 实际接受的参数过滤,避免
       "unexpected keyword argument" 直接把 TTS 挂掉。
    """
    import inspect

    from livekit.plugins import fishaudio

    creds = providers.get("fish") or {}
    reference_id = provider_cfg.get("fish_reference_id") or creds.get("reference_id") or None
    model = provider_cfg.get("tts_model") or creds.get("model") or "s1"

    try:
        sig_params = set(inspect.signature(fishaudio.TTS.__init__).parameters)
    except (ValueError, TypeError):  # 极端情况拿不到签名,放过全部
        sig_params = {"api_key", "base_url", "model", "latency_mode", "reference_id"}

    desired: dict = {"model": model, "latency_mode": "balanced"}
    if creds.get("api_key"):
        desired["api_key"] = creds["api_key"]
    if creds.get("base_url"):
        desired["base_url"] = creds["base_url"]
    # 音色参数名按版本择一(本版本不认 reference_id 时尝试 voice/voice_id)
    if reference_id:
        for voice_key in ("reference_id", "voice", "voice_id"):
            if voice_key in sig_params:
                desired[voice_key] = reference_id
                break

    kwargs = {k: v for k, v in desired.items() if k in sig_params}
    logger.info(
        "Fish TTS 构造: kwargs=%s 插件签名接受=%s",
        list(kwargs),
        sorted(sig_params - {"self"}),
    )
    return fishaudio.TTS(**kwargs)


def _aliyun_tts(providers: dict, provider_cfg: dict):
    """阿里云 CosyVoice TTS(DashScope)。国内端点,音色用 voice。

    使用本地迁移模块 ``aliyun_tts``(替代已停更的 ``livekit-plugins-aliyun``)。
    """
    from . import aliyun_tts

    creds = providers.get("dashscope") or {}
    api_key = creds.get("api_key") or None
    return aliyun_tts.TTS(
        api_key=api_key,
        model=provider_cfg.get("tts_model") or "cosyvoice-v2",
        voice=provider_cfg.get("aliyun_tts_voice") or "longcheng_v2",
    )


def _build_tts(providers: dict, provider_cfg: dict):
    """按配置选 TTS 供应商:fishaudio | aliyun(CosyVoice) | openai(默认)。

    ⚠️ 用户**显式选择**的供应商初始化失败时**不静默回退 OpenAI**——否则真实原因
    (如缺 key)被 OpenAI 的 404/429 等下游报错掩盖,极难排查。直接打完整堆栈并抛真实原因。
    """
    vendor = (provider_cfg.get("tts") or "openai").lower()
    if vendor == "fishaudio":
        try:
            return _fish_tts(providers, provider_cfg)
        except Exception as e:  # noqa: BLE001
            logger.exception("Fish Audio TTS 初始化失败(已显式选择 fishaudio,不回退)")
            raise RuntimeError(f"Fish Audio TTS 初始化失败: {e}(检查 FISH_API_KEY)") from e
    if vendor == "aliyun":
        try:
            return _aliyun_tts(providers, provider_cfg)
        except Exception as e:  # noqa: BLE001
            logger.exception("阿里云 CosyVoice TTS 初始化失败(已显式选择 aliyun,不回退)")
            raise RuntimeError(f"阿里云 TTS 初始化失败: {e}(检查 DASHSCOPE_API_KEY)") from e
    return _openai_tts(providers, provider_cfg)


def _turn_detection():
    """可选:多语种 turn 检测模型。不可用(未下载/未安装)时回退到 VAD。"""
    try:
        from livekit.plugins.turn_detector.multilingual import MultilingualModel
        return MultilingualModel()
    except Exception as e:  # noqa: BLE001
        logger.warning("turn_detector 不可用,回退 VAD: %s", e)
        return None


@server.rtc_session()
async def entrypoint(ctx: JobContext):
    await ctx.connect()
    logger.info("已加入房间 room=%s", ctx.room.name)

    # 1) 等人类参与者,从其 metadata 读桥接令牌
    participant = await ctx.wait_for_participant()
    bridge_token = (getattr(participant, "metadata", "") or "").strip()
    if not bridge_token:
        logger.error("参与者缺少桥接令牌 metadata,拒绝服务")
        return

    # 2) 凭桥接令牌向 Core 拉取会话上下文(标识/前台配置/供应商凭据)
    try:
        sess: VoiceSession = await fetch_session(bridge_token)
    except Exception as e:  # noqa: BLE001
        logger.exception("拉取会话上下文失败: %s", e)
        return

    if not sess.config.get("enabled", True):
        logger.info("该管理员未启用语音前台,退出")
        return

    logger.info("会话引导完成 admin=%s conv=%s", sess.admin_id, sess.conversation_id)

    # 3) 用拉取到的凭据装配 STT/LLM/TTS(一期 OpenAI 系)
    providers = sess.providers
    pcfg = sess.provider_cfg
    orch = Orchestrator(
        fillers=sess.fillers,
    )
    agent = build_copilot(sess, orch)

    interruption = sess.interruption
    vad = silero.VAD.load()
    try:
        llm = _build_llm(pcfg, sess.bridge_token)
        stt_impl = _build_stt(providers, pcfg, vad)
        tts_impl = _build_tts(providers, pcfg)
    except Exception as e:  # noqa: BLE001
        logger.error("前台 STT/LLM/TTS 装配失败(检查所选供应商凭据): %s", e)
        return
    # 1.6:turn_detection/allow_interruptions/min_interruption_words 已弃用(v2.0 移除),
    # 统一收进 turn_handling=TurnHandlingOptions(...)。
    turn_handling: dict = {
        "interruption": {
            "enabled": bool(interruption.get("allow_interruptions", True)),
            "min_words": int(interruption.get("min_interruption_words", 2)),
        },
    }
    _td = _turn_detection()
    if _td is not None:
        # 有 EOU 模型则用之;否则省略该键 → 会话自动回退到已传入的 VAD。
        turn_handling["turn_detection"] = _td

    session = AgentSession(
        stt=stt_impl,
        llm=llm,
        tts=tts_impl,
        vad=vad,
        turn_handling=turn_handling,
    )

    await session.start(agent=agent, room=ctx.room)

    # 4) 开场白
    if sess.greeting:
        session.say(sess.greeting, allow_interruptions=True)


def main():
    logging.basicConfig(level=logging.INFO)
    cli.run_app(server)


if __name__ == "__main__":
    main()
