"""Per-service consumer-side request log.

每个 published service 都有一份轻量的「调用记录」，给 admin 在后台看
"我的服务被怎么用"。和 consumer 对话历史相互独立 —— 对话保存
*内容*，这里保存 *元数据*（谁、什么时候、调了什么、多快、成不成）。

存储布局
--------
    users/{admin_id}/services/{svc_id}/usage/usage-YYYY-MM.jsonl

按月轮转 —— 老月份的文件会冷下来，新月份只 append。读侧合并
最近 N 个月的 tail，避免单文件无限增长又能看到跨月的最近记录。
不做归档/删除，量真的爆了再说（典型 service 一天几十~几百条，
JSONL 最小行 ~120 字节，一年也就 几 MB ~ 几十 MB）。

字段约定
--------
最小集 —— 多了反而成排查负担。如果以后要 token 计数，再扩字段，
读侧用 ``rec.get(...)`` 就向后兼容。

* ts:           ISO8601 with timezone
* channel:      "web" | "api" | "wechat"
* key_id:       sk-svc 的 key_id（"key_xxxxxx"），WeChat 没 key 写 ""
* conv_id:      命中的 / 新建的 conversation id；建会话失败则 ""
* endpoint:     "POST /api/v1/chat" 之类，便于过滤
* status_code:  HTTP 状态（WeChat 入口约定 200 / 500）
* latency_ms:   端到端耗时
* ok:           True / False（4xx/5xx 视作 False）

线程安全
--------
``append_jsonl`` 用 ``open(path, "ab")`` + 单次 ``write``，POSIX
保证 < PIPE_BUF 的写原子性，多 worker 并发也不会撕行。
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core.security import get_user_dir
from app.core.jsonl_store import append_jsonl, read_jsonl_tail


def _usage_dir(admin_id: str, service_id: str) -> str:
    return os.path.join(get_user_dir(admin_id), "services", service_id, "usage")


def _usage_path(admin_id: str, service_id: str, year: int, month: int) -> str:
    return os.path.join(
        _usage_dir(admin_id, service_id),
        f"usage-{year:04d}-{month:02d}.jsonl",
    )


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def record_request(
    admin_id: str,
    service_id: str,
    *,
    channel: str,
    key_id: str = "",
    conv_id: str = "",
    endpoint: str = "",
    status_code: int = 200,
    latency_ms: int = 0,
    ok: bool = True,
) -> None:
    """Append one record. Swallows all exceptions — logging must never
    break the user-facing request."""
    try:
        now = datetime.now()
        path = _usage_path(admin_id, service_id, now.year, now.month)
        rec: Dict[str, Any] = {
            "ts": _now_iso(),
            "channel": channel,
            "key_id": key_id or "",
            "conv_id": conv_id or "",
            "endpoint": endpoint or "",
            "status_code": int(status_code),
            "latency_ms": int(latency_ms),
            "ok": bool(ok),
        }
        append_jsonl(path, rec)
    except Exception:
        pass


def _list_month_files(admin_id: str, service_id: str) -> List[str]:
    """Return month JSONL paths sorted newest-first."""
    d = _usage_dir(admin_id, service_id)
    if not os.path.isdir(d):
        return []
    files = [
        os.path.join(d, name)
        for name in os.listdir(d)
        if name.startswith("usage-") and name.endswith(".jsonl")
    ]
    files.sort(reverse=True)
    return files


def list_records(
    admin_id: str,
    service_id: str,
    *,
    limit: int = 100,
    channel: Optional[str] = None,
    max_months: int = 6,
) -> List[Dict[str, Any]]:
    """Newest-first list of records, optionally filtered by channel.

    跨月份合并：默认最多翻 6 个月文件。每个月用 ``read_jsonl_tail``
    保证不会把整个月文件吸进内存。当某条命中过滤器才计数，最终把
    最新的 ``limit`` 条返回给调用方。
    """
    out: List[Dict[str, Any]] = []
    for path in _list_month_files(admin_id, service_id)[:max_months]:
        # 取一个比 limit 大一些的批量，给 channel 过滤留余地
        batch = read_jsonl_tail(path, last_n=max(limit * 4, 200))
        # tail 是文件尾部 = 时间最新；先翻成最新在前
        batch.reverse()
        for rec in batch:
            if channel and rec.get("channel") != channel:
                continue
            out.append(rec)
            if len(out) >= limit:
                return out
    return out


def count_recent(
    admin_id: str,
    service_id: str,
    *,
    channel: Optional[str] = None,
) -> Dict[str, int]:
    """轻量摘要 —— 用来在 Tab 角标显示「最近 N 条 / 失败 M 条」。
    只看当月文件，避免每次刷新都做跨月扫描。"""
    now = datetime.now()
    path = _usage_path(admin_id, service_id, now.year, now.month)
    recs = read_jsonl_tail(path, last_n=500)
    if channel:
        recs = [r for r in recs if r.get("channel") == channel]
    fails = sum(1 for r in recs if not r.get("ok", True))
    return {"recent": len(recs), "failed": fails}
