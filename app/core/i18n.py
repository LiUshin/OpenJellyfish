"""Backend i18n for user-facing API messages.

Scope is intentionally narrow — only success/error messages returned by
HTTP routes. Agent system prompts, tool descriptions, and capability
prompts stay in Chinese for now; switching them would change agent
output language too, which is a separate decision.

Design:
- Keys are namespaced with dots (e.g. ``user_profile.updated``).
- Languages live inline in :data:`MESSAGES` so we don't ship a separate
  JSON for ~20 short strings; if it grows past ~50 keys, split into
  ``app/locales/<lang>.json``.
- :func:`resolve_lang` reads the ``Accept-Language`` request header and
  normalises to one of :data:`SUPPORTED`. The frontend `api.ts` always
  attaches a header derived from `localStorage['jf-lang']` (in turn
  reconciled with the user's `preferences.language` on app mount), so
  backend doesn't need to look up user prefs per-request.
- Missing keys fall through to the key string itself, making accidental
  untranslated messages visible during dev.
"""

from typing import Optional

from fastapi import Request

SUPPORTED = ("zh", "en")
DEFAULT = "zh"

MESSAGES = {
    "user_profile.updated": {
        "zh": "个性规则已更新，下次对话将根据规则个性化回复",
        "en": "Personal rules updated. Future conversations will follow them.",
    },
    "system_prompt.updated": {
        "zh": "System prompt 已更新，下次对话将使用新 prompt",
        "en": "System prompt updated. The next conversation will use it.",
    },
    "system_prompt.empty": {
        "zh": "Prompt 不能为空",
        "en": "Prompt cannot be empty",
    },
    "version.not_found": {
        "zh": "版本不存在",
        "en": "Version not found",
    },
}


def resolve_lang(request: Optional[Request]) -> str:
    """Pick a language from ``Accept-Language``; fall back to :data:`DEFAULT`.

    Only the *first* tag is inspected — q-values and additional tags are
    ignored on purpose (we only support two languages, and the frontend
    sets a single tag).
    """
    if request is None:
        return DEFAULT
    raw = request.headers.get("accept-language", "") or ""
    first = raw.split(",")[0].strip().lower()
    if not first:
        return DEFAULT
    if first.startswith("en"):
        return "en"
    if first.startswith("zh"):
        return "zh"
    return DEFAULT


def t(key: str, lang: str = DEFAULT, **kwargs) -> str:
    """Translate a key. ``kwargs`` go through ``str.format`` if present."""
    if lang not in SUPPORTED:
        lang = DEFAULT
    entry = MESSAGES.get(key) or {}
    text = entry.get(lang) or entry.get(DEFAULT) or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except Exception:
            return text
    return text
