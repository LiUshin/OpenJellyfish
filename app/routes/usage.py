"""Per-admin token usage summary (admin web 端「用量统计」).

每个 admin 只看自己的 LLM token 用量，按模型 / Service / API Key / Provider /
渠道 / 日期 拆分。数据源是 ``users/{admin_id}/llm_usage/usage-YYYY-MM.jsonl``
（由 ``token_usage.record_llm_usage`` 落盘）。跨用户的超管全量视图在 Tauri
启动器里（直读同一批 jsonl），此端点不暴露跨用户数据。

Endpoint:
    GET /api/usage/summary?months=3  → 聚合 + service/key 友好名映射
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Query

from app.deps import get_current_user
from app.services.token_usage import aggregate_usage

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/usage", tags=["usage"])


def _service_name_map(admin_id: str) -> Dict[str, str]:
    try:
        from app.services.published import list_services
        return {
            s.get("id", ""): (s.get("name") or s.get("id", ""))
            for s in list_services(admin_id)
            if s.get("id")
        }
    except Exception:
        return {}


def _key_name_map(admin_id: str) -> Dict[str, str]:
    """key_id → "服务名 · key名"。遍历所有 service 的 key 列表。"""
    out: Dict[str, str] = {}
    try:
        from app.services.published import list_services, list_service_keys
        for s in list_services(admin_id):
            sid = s.get("id", "")
            if not sid:
                continue
            svc_name = s.get("name") or sid
            for k in list_service_keys(admin_id, sid):
                kid = k.get("id", "")
                if kid:
                    kname = k.get("name") or k.get("prefix") or kid
                    out[kid] = f"{svc_name} · {kname}"
    except Exception:
        pass
    return out


def _label_rows(
    rows: List[Dict[str, Any]],
    name_map: Dict[str, str],
    empty_label: str,
) -> List[Dict[str, Any]]:
    """把 by_service / by_key 的原始 id（name 字段）换成友好显示，保留 id。"""
    out: List[Dict[str, Any]] = []
    for r in rows:
        raw = r.get("name", "") or ""
        out.append({
            **r,
            "id": raw,
            "name": name_map.get(raw, raw) if raw else empty_label,
        })
    return out


@router.get("/summary")
async def api_usage_summary(
    months: int = Query(3, ge=1, le=24),
    user=Depends(get_current_user),
):
    admin_id = user["user_id"]
    agg = aggregate_usage(admin_id, months=months)

    agg["by_service"] = _label_rows(
        agg.get("by_service", []), _service_name_map(admin_id), "主链路（Admin）"
    )
    agg["by_key"] = _label_rows(
        agg.get("by_key", []), _key_name_map(admin_id), "—（无 Key）"
    )
    return agg
