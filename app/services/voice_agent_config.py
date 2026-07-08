"""前台语音 Copilot Agent 的配置存储。

存储位置: ``{user_dir}/voice_agent_config.json``

这是「上层对话编排」的可调参数集合,与底层 OpenJellyfish agent(任务引擎)解耦。
前端「语音前台调音台」读写本配置;语音 Worker 在 session 启动时通过
``/api/voice/live/session`` 拉取(改完即时生效,无需重部署 worker)。

字段:
- ``enabled``        是否启用语音前台
- ``greeting``       接通后的开场白
- ``system_prompt``  前台 Copilot 人格/语气/边界(纯对话层,不含任务执行逻辑)
- ``routing_policy`` 闲聊直答 vs 委派重活的判定指引(拼到 system_prompt 之后)
- ``fillers``        分状态的填充语(委派/检索/长任务期间用来「占位」不冷场)
- ``interruption``   打断行为(是否允许打断 + 需要几个词才算实质打断)
- ``providers``      STT / 前台 LLM / TTS 的供应商与模型/音色(一期默认 OpenAI)
"""

from __future__ import annotations

import os
import json
from typing import Any, Dict

from app.core.security import get_user_dir

_DEFAULT_SYSTEM_PROMPT = (
    "你是用户的实时语音 Copilot,运行在 OpenJellyfish 之上。"
    "你说话简洁、口语化、自然,像真人助理。回答尽量控制在 1-2 句话。"
    "你可以闲聊、确认意图;遇到需要查资料、读写文档、跑脚本、做多步任务时,"
    "调用 delegate_to_jellyfish 把任务交给后台执行,并在等待时用简短的话安抚用户。"
)

_DEFAULT_ROUTING_POLICY = (
    "判定规则:\n"
    "- 寒暄、澄清、对已知信息的简单复述 → 直接口头回答,不要委派。\n"
    "- 任何需要访问文档/文件、检索网络、运行脚本、生成内容、多步推理的请求 →"
    " 调用 delegate_to_jellyfish,并先说一句简短的承接语(如「好的,我来查一下」)。\n"
    "- 不确定时,倾向于委派,但先用一句话向用户确认。"
)

_DEFAULTS: Dict[str, Any] = {
    "enabled": True,
    "greeting": "你好,我是你的语音助手,有什么可以帮你?",
    "system_prompt": _DEFAULT_SYSTEM_PROMPT,
    "routing_policy": _DEFAULT_ROUTING_POLICY,
    "fillers": {
        "delegating": ["好的,我来处理一下。", "收到,我去查一下。", "好的,稍等。"],
        "tool_running": ["正在执行,马上好。", "处理中……"],
        "long_task": ["还在进行中,再给我一点时间。", "马上就好,正在收尾。"],
    },
    "interruption": {
        "allow_interruptions": True,
        "min_interruption_words": 2,
    },
    "providers": {
        # STT 供应商: openai(流式) | fishaudio(批量) | aliyun(Paraformer 流式)
        "stt": "openai",
        # STT 模型: openai 用 gpt-4o-mini-transcribe; aliyun 用 paraformer-realtime-v2
        "stt_model": "gpt-4o-mini-transcribe",
        # 阿里云 STT 热词表 ID(可选)
        "aliyun_vocabulary_id": "",
        # 前台 LLM 供应商: openai | bedrock
        "llm": "openai",
        # LLM 模型: openai 用 gpt-4o-mini; bedrock 用 claude-sonnet-4-6 等(自动映射 profile)
        "llm_model": "gpt-4o-mini",
        # TTS 供应商: openai | fishaudio | aliyun(CosyVoice)
        "tts": "openai",
        # TTS 模型: openai 用 gpt-4o-mini-tts/tts-1/tts-1-hd; fishaudio 用 s1/s2-pro; aliyun 用 cosyvoice-v2
        "tts_model": "gpt-4o-mini-tts",
        # OpenAI 音色
        "tts_voice": "alloy",
        # Fish Audio 当前音色模型 ID(留空用凭据里的默认 / Fish 通用音色)
        "fish_reference_id": "",
        # Fish Audio 音色库:可保存多个 {id, label},在调音台选其一设为当前
        "fish_voices": [],
        # 阿里云 CosyVoice 音色(如 longcheng_v2)
        "aliyun_tts_voice": "longcheng_v2",
    },
}

# 允许前端写入的顶层 key(白名单,避免注入任意字段)
_ALLOWED_KEYS = set(_DEFAULTS.keys())


def _cfg_path(user_id: str) -> str:
    return os.path.join(get_user_dir(user_id), "voice_agent_config.json")


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """对 dict 字段做一层深合并,保证新增默认字段对老配置可见。"""
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def get_voice_agent_config(user_id: str) -> Dict[str, Any]:
    """读取前台配置(与默认值深合并)。"""
    result = _deep_merge(_DEFAULTS, {})
    path = _cfg_path(user_id)
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                stored = json.load(f)
            if isinstance(stored, dict):
                result = _deep_merge(result, stored)
        except Exception:
            pass
    return result


def update_voice_agent_config(user_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    """按白名单合并更新前台配置,返回更新后的完整配置。"""
    current = get_voice_agent_config(user_id)
    for k, v in updates.items():
        if k not in _ALLOWED_KEYS:
            continue
        if isinstance(v, dict) and isinstance(current.get(k), dict):
            current[k] = _deep_merge(current[k], v)
        else:
            current[k] = v
    path = _cfg_path(user_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    from app.core.fileutil import atomic_json_save
    atomic_json_save(path, current, ensure_ascii=False, indent=2)
    return current


def reset_voice_agent_config(user_id: str) -> Dict[str, Any]:
    """恢复默认配置(删除用户覆盖文件)。"""
    path = _cfg_path(user_id)
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass
    return get_voice_agent_config(user_id)


def get_default_voice_agent_config() -> Dict[str, Any]:
    return _deep_merge(_DEFAULTS, {})
