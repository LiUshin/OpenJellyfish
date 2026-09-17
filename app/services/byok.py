"""BYOK（调用方自带 LLM 凭据）的解析与校验。

用于 ``billing == "byok"`` 的 service key：调用方在请求体里自带
``provider`` / ``api_key`` / ``model``（``base_url`` 可代传），平台只把**主对话模型**
的出站凭据换成调用方的，图片 / 联网 / TTS / 脚本等能力仍走 Admin 凭据。

安全约束：
  - 解析结果只在单次请求内存活，绝不写入 ``api_keys.json`` 或进程环境变量。
  - ``base_url`` 只接受 https + host 白名单，避免把平台变成 SSRF 出站代理。
  - 明文 key 只允许出现在出站请求里；日志 / 缓存键一律用 :func:`credentials_fingerprint`。
"""

from __future__ import annotations

import hashlib
import os
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse


class BYOKError(ValueError):
    """调用方凭据不合法。路由层统一转成 HTTP 400。"""


# provider → 默认 base_url（与 api_config / _resolve_model 里的默认值保持一致）
PROVIDER_DEFAULT_BASE: Dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
    "openrouter": "https://openrouter.ai/api/v1",
    "siliconflow": "https://api.siliconflow.cn/v1",
    "kimi": "https://api.moonshot.cn/v1",
    "minimax": "https://api.minimax.io/anthropic",
}

# provider → 允许代传的 base_url host。bedrock 不吃 base_url（只吃 region）。
PROVIDER_BUILTIN_HOSTS: Dict[str, Tuple[str, ...]] = {
    "openai": ("api.openai.com",),
    "anthropic": ("api.anthropic.com",),
    "openrouter": ("openrouter.ai",),
    # .cn is the domestic endpoint, .com the international one — same account
    # system, and callers pick whichever is closer to them.
    "siliconflow": ("api.siliconflow.cn", "api.siliconflow.com"),
    "kimi": ("api.moonshot.cn",),
    "minimax": ("api.minimax.io",),
    "bedrock": (),
}

BYOK_PROVIDERS: Tuple[str, ...] = tuple(PROVIDER_BUILTIN_HOSTS.keys())

BEDROCK_DEFAULT_REGION = "us-east-1"

# 运维可用逗号分隔的 env 追加自建网关域名：
#   BYOK_EXTRA_ALLOWED_HOSTS="gw.example.com,openai=proxy.corp.internal"
# 不带 provider= 前缀的条目对所有 provider 生效。
_EXTRA_HOSTS_ENV = "BYOK_EXTRA_ALLOWED_HOSTS"


def _extra_allowed_hosts(provider: str) -> List[str]:
    raw = os.environ.get(_EXTRA_HOSTS_ENV, "")
    hosts: List[str] = []
    for item in raw.split(","):
        item = item.strip().lower()
        if not item:
            continue
        if "=" in item:
            scoped_provider, _, host = item.partition("=")
            if scoped_provider.strip() == provider and host.strip():
                hosts.append(host.strip())
        else:
            hosts.append(item)
    return hosts


def allowed_hosts(provider: str) -> List[str]:
    """该 provider 允许代传的 base_url host 列表（内置 + env 追加）。"""
    builtin = list(PROVIDER_BUILTIN_HOSTS.get(provider, ()))
    for host in _extra_allowed_hosts(provider):
        if host not in builtin:
            builtin.append(host)
    return builtin


def allowed_hosts_map() -> Dict[str, List[str]]:
    return {p: allowed_hosts(p) for p in BYOK_PROVIDERS}


def allowed_models(admin_id: str) -> List[Dict[str, Any]]:
    """该 Admin 的 catalog 里、provider 支持 BYOK 的 LLM 条目。

    刻意**不加** ``only_available`` 过滤：凭据由调用方提供，Admin 自己有没有配
    对应厂商的 key 与此无关；catalog 在这里只承担「允许用哪些模型」的白名单职责。
    """
    from app.services.model_catalog import list_models

    rows: List[Dict[str, Any]] = []
    for item in list_models("llm", user_id=admin_id):
        model_id = item.get("id") or ""
        provider = model_id.split(":", 1)[0] if ":" in model_id else ""
        if provider not in BYOK_PROVIDERS:
            continue
        rows.append({
            "id": model_id,
            "provider": provider,
            "display_name": item.get("display_name") or model_id,
        })
    return rows


def _validate_base_url(provider: str, base_url: str) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme != "https":
        raise BYOKError("base_url 必须是 https")
    if parsed.username or parsed.password:
        raise BYOKError("base_url 不允许包含用户名/密码")
    host = (parsed.hostname or "").lower()
    if not host:
        raise BYOKError("base_url 缺少主机名")
    if parsed.port not in (None, 443):
        raise BYOKError("base_url 只允许 443 端口")
    permitted = allowed_hosts(provider)
    if host not in permitted:
        raise BYOKError(
            f"base_url 的主机 {host} 不在 {provider} 的白名单内"
            f"（允许：{', '.join(permitted) or '无'}）"
        )
    return base_url.rstrip("/")


def resolve_request_credentials(
    *,
    admin_id: str,
    svc_config: Dict[str, Any],
    provider: Optional[str],
    api_key: Optional[str],
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    region: Optional[str] = None,
) -> Tuple[str, Dict[str, Any]]:
    """校验调用方凭据，返回 ``(model_id, credentials)``。

    credentials 的形状与 ``api_config.get_provider_credentials`` 一致，可直接喂给
    ``agent._resolve_model(..., credentials=...)``。
    """
    api_key = (api_key or "").strip()
    if not api_key:
        raise BYOKError("该 Key 需要调用方自带凭据，请在请求中提供 api_key")

    model_id = (model or "").strip() or (svc_config.get("model") or "").strip()
    if not model_id:
        raise BYOKError("未指定 model，且该服务没有配置默认模型")

    permitted = {m["id"]: m for m in allowed_models(admin_id)}
    if model_id not in permitted:
        raise BYOKError(f"模型 {model_id} 不在该服务允许的模型列表内")

    model_provider = permitted[model_id]["provider"]
    provider = (provider or "").strip().lower()
    if not provider:
        raise BYOKError("缺少 provider")
    if provider != model_provider:
        raise BYOKError(f"provider（{provider}）与模型 {model_id} 的厂商不一致")

    if provider == "bedrock":
        if base_url:
            raise BYOKError("bedrock 不接受 base_url，请改用 region")
        return model_id, {
            "api_key": api_key,
            "region": (region or "").strip() or BEDROCK_DEFAULT_REGION,
        }

    if region:
        raise BYOKError(f"{provider} 不接受 region 参数")

    resolved_base = (
        _validate_base_url(provider, base_url.strip())
        if base_url and base_url.strip()
        else PROVIDER_DEFAULT_BASE[provider]
    )
    return model_id, {"api_key": api_key, "base_url": resolved_base}


def credentials_fingerprint(model_id: str, credentials: Dict[str, Any]) -> str:
    """凭据的不可逆指纹，供 agent 缓存键使用（明文 key 绝不入键）。"""
    material = "|".join([
        model_id,
        str(credentials.get("base_url", "")),
        str(credentials.get("region", "")),
        str(credentials.get("api_key", "")),
    ])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
