from fastapi import APIRouter, Depends
from app.core.api_config import has_provider
from app.deps import get_current_user

router = APIRouter(tags=["models"])

AVAILABLE_MODELS = [
    # Anthropic — Thinking
    {"id": "anthropic:claude-opus-4-6-thinking",    "name": "Claude Opus 4.6 (Thinking)",    "provider": "anthropic", "tier": "thinking"},
    {"id": "anthropic:claude-sonnet-4-6-thinking",  "name": "Claude Sonnet 4.6 (Thinking)",  "provider": "anthropic", "tier": "thinking"},
    {"id": "anthropic:claude-haiku-4-5-thinking",   "name": "Claude Haiku 4.5 (Thinking)",   "provider": "anthropic", "tier": "thinking"},
    {"id": "anthropic:claude-sonnet-4-5-thinking",  "name": "Claude Sonnet 4.5 (Thinking)",  "provider": "anthropic", "tier": "thinking"},
    # Anthropic — Latest
    {"id": "anthropic:claude-opus-4-6",             "name": "Claude Opus 4.6",               "provider": "anthropic", "tier": "high"},
    {"id": "anthropic:claude-sonnet-4-6",           "name": "Claude Sonnet 4.6",             "provider": "anthropic", "tier": "high"},
    {"id": "anthropic:claude-haiku-4-5-20251001",   "name": "Claude Haiku 4.5",              "provider": "anthropic", "tier": "fast"},
    # Anthropic — Previous generation
    {"id": "anthropic:claude-opus-4-5-20251101",    "name": "Claude Opus 4.5",               "provider": "anthropic", "tier": "high"},
    {"id": "anthropic:claude-opus-4-1-20250805",    "name": "Claude Opus 4.1",               "provider": "anthropic", "tier": "high"},
    {"id": "anthropic:claude-sonnet-4-5-20250929",  "name": "Claude Sonnet 4.5",             "provider": "anthropic", "tier": "high"},
    {"id": "anthropic:claude-opus-4-20250514",      "name": "Claude Opus 4",                 "provider": "anthropic", "tier": "high"},
    {"id": "anthropic:claude-sonnet-4-20250514",    "name": "Claude Sonnet 4",               "provider": "anthropic", "tier": "high"},
    # OpenAI — Reasoning
    {"id": "openai:gpt-5.4",                        "name": "GPT-5.4 (Reasoning)",           "provider": "openai",    "tier": "thinking"},
    # OpenAI — Standard
    {"id": "openai:gpt-5.3-chat-latest",            "name": "GPT-5.3",                       "provider": "openai",    "tier": "high"},
    {"id": "openai:gpt-5.2-2025-12-11",             "name": "GPT-5.2",                       "provider": "openai",    "tier": "high"},
    {"id": "openai:gpt-4o",                         "name": "GPT-4o",                        "provider": "openai",    "tier": "high"},
    {"id": "openai:gpt-4o-mini",                    "name": "GPT-4o Mini",                   "provider": "openai",    "tier": "fast"},
    {"id": "openai:o3-mini",                        "name": "o3-mini",                       "provider": "openai",    "tier": "reasoning"},
]


@router.get("/api/models")
async def api_list_models(user=Depends(get_current_user)):
    user_id = user["user_id"]
    has_anthropic = has_provider("anthropic", user_id=user_id)
    has_openai = has_provider("openai", user_id=user_id)
    models = [m for m in AVAILABLE_MODELS
              if (m["provider"] == "anthropic" and has_anthropic) or (m["provider"] == "openai" and has_openai)]
    default_model = "anthropic:claude-sonnet-4-5-20250929" if has_anthropic else ("openai:gpt-4o" if has_openai else "")
    return {"models": models, "default": default_model}
