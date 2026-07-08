"""Worker 启动引导:从 OpenJellyfish Core 拉取会话上下文。

Worker 自身无状态——它在加入 room 后从参与者 metadata 读到桥接令牌,
凭此调用 Core 的 ``GET /api/voice/live/session`` 一次性拿全:
会话标识、前台 Copilot 配置、STT/LLM/TTS 供应商凭据。

这样前台配置改完即时生效(下一通电话重新拉取),无需重部署 Worker。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx


def api_base() -> str:
    """OpenJellyfish Core 的内网基址(Worker → Core)。"""
    return os.environ.get("JELLYFISH_API_BASE", "http://localhost:8000").rstrip("/")


@dataclass
class VoiceSession:
    """一通语音会话的完整上下文。"""

    bridge_token: str
    admin_id: str
    conversation_id: str
    model: Optional[str]
    capabilities: List[str]
    config: Dict[str, Any] = field(default_factory=dict)
    providers: Dict[str, Any] = field(default_factory=dict)

    # ── 便捷读取前台配置 ──
    @property
    def greeting(self) -> str:
        return self.config.get("greeting", "") or ""

    @property
    def system_prompt(self) -> str:
        sp = self.config.get("system_prompt", "") or ""
        rp = self.config.get("routing_policy", "") or ""
        return (sp + "\n\n" + rp).strip() if rp else sp

    @property
    def fillers(self) -> Dict[str, List[str]]:
        return self.config.get("fillers", {}) or {}

    @property
    def interruption(self) -> Dict[str, Any]:
        return self.config.get("interruption", {}) or {}

    @property
    def provider_cfg(self) -> Dict[str, Any]:
        return self.config.get("providers", {}) or {}


async def fetch_session(bridge_token: str, *, timeout: float = 15.0) -> VoiceSession:
    """凭桥接令牌向 Core 拉取会话上下文。"""
    url = f"{api_base()}/api/voice/live/session"
    headers = {"X-Bridge-Token": bridge_token}
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    return VoiceSession(
        bridge_token=bridge_token,
        admin_id=data.get("admin_id", ""),
        conversation_id=data.get("conversation_id", ""),
        model=data.get("model"),
        capabilities=data.get("capabilities") or [],
        config=data.get("config") or {},
        providers=data.get("providers") or {},
    )
