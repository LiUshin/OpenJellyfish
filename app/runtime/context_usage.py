"""Small, provider-independent context snapshot exposed to the chat UI.

Codex's ``last`` usage describes its latest model response. Its ``total``
counter accumulates across the thread and is deliberately never used here.
The installed Codex app-server schema declares ``last.totalTokens`` and the
nullable ``modelContextWindow``; neither includes a System/Skills/MCP split.
"""

from typing import Any


def _token_count(value: Any) -> int | None:
    # JSON integer counts only. In particular bool is an int subclass, and
    # coercing strings or floating-point values can turn invalid data into 0.
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 2**53 - 1:
        return value
    return None


def codex_context_usage(snapshot: Any) -> dict[str, int | str | None]:
    """Normalize only measured window fields; missing values stay unknown."""
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    last = snapshot.get('last')
    last = last if isinstance(last, dict) else {}
    return {
        'used_tokens': _token_count(last.get('totalTokens')),
        'limit_tokens': _token_count(snapshot.get('modelContextWindow')),
        'source': 'codex',
    }
