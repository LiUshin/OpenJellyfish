"""
SiliconFlow (硅基流动) request-shaping helpers.

SiliconFlow is an OpenAI-compatible aggregator, so it goes through
``init_chat_model(model_provider="openai")`` like OpenRouter does. The one thing
it does differently is how thinking is controlled, which is what this module
exists for.

Two request fields, both sent via ``extra_body``:

- ``enable_thinking`` — turns thinking on/off for *hybrid* models (Qwen3, GLM-4.6+,
  DeepSeek-V3.1/V3.2 …). Only those models accept the field; sending it to any
  other model is rejected, so we keep an explicit list rather than guessing.
- ``thinking_budget`` — caps chain-of-thought length (128..32768). Accepted by
  reasoning models generally, and it is the main lever on cost since thinking
  tokens are billed as output.

Reasoning output arrives as ``reasoning_content`` on the delta, which is a
channel ``chat.py`` / ``consumer.py`` already read for OpenRouter.

Docs: https://docs.siliconflow.cn/cn/api-reference/chat-completions/chat-completions
"""

import os
from typing import Any, Dict, Optional

DEFAULT_BASE_URL = "https://api.siliconflow.cn/v1"

# Models accepting ``enable_thinking``, per the API reference. Compared after
# ``_normalize`` (lowercased, "pro/" tier prefix dropped), so the paid "Pro/"
# variants match their base entry.
#
# SiliconFlow adds models continuously; ``SILICONFLOW_HYBRID_MODELS`` (comma
# separated) extends this list without a code change. A model missing from here
# is not broken — it just runs in its own default thinking mode.
_HYBRID_THINKING_MODELS = frozenset({
    "qwen/qwen3-8b",
    "qwen/qwen3-14b",
    "qwen/qwen3-32b",
    "qwen/qwen3-30b-a3b",
    "qwen/qwen3-235b-a22b",
    "qwen/qwen3.5-4b",
    "qwen/qwen3.5-9b",
    "qwen/qwen3.5-27b",
    "qwen/qwen3.5-35b-a3b",
    "qwen/qwen3.5-122b-a10b",
    "qwen/qwen3.5-397b-a17b",
    "deepseek-ai/deepseek-v3.1",
    "deepseek-ai/deepseek-v3.1-terminus",
    "deepseek-ai/deepseek-v3.2",
    "deepseek-ai/deepseek-v3.2-exp",
    "zai-org/glm-4.5v",
    "zai-org/glm-4.6",
    "zai-org/glm-4.6v",
    "zai-org/glm-4.7",
    "zai-org/glm-5",
    "zai-org/glm-5v-turbo",
    "tencent/hunyuan-a13b-instruct",
})

_ENV_EXTRA_HYBRID = "SILICONFLOW_HYBRID_MODELS"

MIN_THINKING_BUDGET = 128
MAX_THINKING_BUDGET = 32768


def _normalize(model: str) -> str:
    """Drop the billing-tier prefix and case so ``Pro/Qwen/Qwen3-32B`` matches."""
    name = (model or "").strip().lower()
    for tier in ("pro/", "ltd/"):
        if name.startswith(tier):
            name = name[len(tier):]
    return name


def supports_enable_thinking(model: str) -> bool:
    name = _normalize(model)
    if name in _HYBRID_THINKING_MODELS:
        return True
    extra = os.getenv(_ENV_EXTRA_HYBRID, "")
    return any(name == _normalize(m) for m in extra.split(",") if m.strip())


def clamp_thinking_budget(value: Any) -> Optional[int]:
    """Coerce a budget into the accepted range, or None if unusable."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(MIN_THINKING_BUDGET, min(MAX_THINKING_BUDGET, int(value)))


def build_extra_body(model: str, meta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Thinking controls for one model, from its catalog/whitelist entry.

    ``meta["reasoning"]`` is the Admin's per-model toggle. For a hybrid model it
    is sent through explicitly in both directions — turning thinking *off* is
    what keeps Qwen3 from spending output tokens on a chain of thought nobody
    asked for. For every other model an unset toggle means "send nothing and let
    the model do what it does by default".
    """
    body: Dict[str, Any] = {}
    wants_thinking = bool(meta.get("reasoning"))
    if supports_enable_thinking(model):
        body["enable_thinking"] = wants_thinking
    if wants_thinking:
        budget = clamp_thinking_budget(meta.get("thinking_budget"))
        if budget is not None:
            body["thinking_budget"] = budget
    return body or None
