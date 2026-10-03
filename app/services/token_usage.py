"""Per-admin LLM token usage log.

每个 admin 一份「大模型 token 用量」流水，给超管在启动器（或 SSH）里看
"谁、用什么模型、花了多少 token"。与 ``usage_log``（per-service 请求级元数据）
正交：那个记"请求多快/成不成"，这个记"烧了多少 token"。

存储布局
--------
    users/{admin_id}/llm_usage/usage-YYYY-MM.jsonl

按月轮转、append-only、不归档（量爆了再说）。

字段（rich per-call，每次 LLM 调用一条）
----------------------------------------
* ts:             ISO8601 with timezone
* model:          catalog id（如 "bedrock:claude-sonnet-4-6"）或裸模型名
* input_tokens:   prompt tokens
* output_tokens:  completion tokens
* total_tokens:   input + output
* service_id:     消费侧归属的 service（admin 主链路为 ""）
* channel:        "web" | "api" | "wechat" | "scheduler" | "batch" | "voice"
* conv_id:        归属对话（可空）
* billing:        "hosted"（运营方付费）| "byok"（调用方自带凭据付费）

捕获方式
--------
DeepAgents 的 ``TokenUsageCallback``（LangChain ``BaseCallbackHandler``）挂进
agent 的 ``config["callbacks"]``，在 ``on_llm_end`` 读 ``usage_metadata`` 落盘；
Service CLI 由 RuntimeConsumerAgent 在完成时写入同一 JSONL。
原生 ChatAnthropic / ChatOpenAI 自带 ``usage_metadata``；自有的
``ChatBedrockInvoke`` 已在 ``_anthropic_event_to_chunk`` 发射 usage_metadata
chunk（见 bedrock.py），故同一回调可统一覆盖所有 provider。

可靠性
------
``record_llm_usage`` 与回调内部一律吞异常——用量统计绝不能拖垮用户请求。
``append_jsonl`` 用 ``open(path,"ab")`` + 单次 write，POSIX 保证 < PIPE_BUF
写原子性，多 worker 并发也不撕行。
"""

from __future__ import annotations

import logging
import os
import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.security import get_user_dir
from app.core.settings import ROOT_DIR
from app.core.jsonl_store import append_jsonl, read_jsonl_tail
from app.core.observability import get_langfuse_callbacks

log = logging.getLogger(__name__)


def _usage_dir(admin_id: str) -> str:
    return os.path.join(get_user_dir(admin_id), "llm_usage")


def _usage_path(admin_id: str, year: int, month: int) -> str:
    return os.path.join(_usage_dir(admin_id), f"usage-{year:04d}-{month:02d}.jsonl")


def record_llm_usage(
    admin_id: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    service_id: str = "",
    channel: str = "",
    conv_id: str = "",
    key_id: str = "",
    billing: str = "hosted",
    runtime: str = "deepagents",
) -> None:
    """Append one LLM-call usage record. Swallows all exceptions.

    ``key_id`` 是消费侧 sk-svc API key 的标识（"key_xxxxxx"）；admin 主链路
    （web/wechat/scheduler/batch）没有 key，写 ""。provider 维度不单独存字段，
    聚合时从 ``model`` 前缀派生（catalog id 为 ``provider:model``）。

    ``billing="byok"`` 的记录，token 由调用方自带的凭据付费，不构成 admin 成本。
    """
    if not admin_id:
        return
    try:
        in_tok = int(input_tokens or 0)
        out_tok = int(output_tokens or 0)
        if in_tok <= 0 and out_tok <= 0:
            return  # 没有任何 token 信息，不落空记录
        now = datetime.now().astimezone()
        path = _usage_path(admin_id, now.year, now.month)
        rec: Dict[str, Any] = {
            "ts": now.isoformat(),
            "model": model or "",
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "total_tokens": in_tok + out_tok,
            "service_id": service_id or "",
            "channel": channel or "",
            "conv_id": conv_id or "",
            "key_id": key_id or "",
            "billing": billing or "hosted",
            "runtime": runtime or "deepagents",
        }
        append_jsonl(path, rec)
    except Exception:
        pass


def _list_month_files(admin_id: str) -> List[str]:
    d = _usage_dir(admin_id)
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
    *,
    limit: int = 500,
    max_months: int = 6,
) -> List[Dict[str, Any]]:
    """Return most-recent token usage records (newest-first), merged across
    up to ``max_months`` month files. The Tauri launcher reads these JSONL files
    and admin CLI Runtime runs directly in Rust."""
    out: List[Dict[str, Any]] = []
    for path in _list_month_files(admin_id)[:max_months]:
        recs = read_jsonl_tail(path, limit)
        recs.reverse()  # tail 是顺序的，转成 newest-first
        out.extend(recs)
        if len(out) >= limit:
            break
    return out[:limit]


def _provider_of(model: str) -> str:
    """从 catalog id（``provider:model``）派生 provider；无冒号回退 model 本身。"""
    if not model:
        return "(未知)"
    return model.split(":", 1)[0] if ":" in model else model


def _month_paths(admin_id: str, months: int) -> List[str]:
    """Current calendar month plus the preceding months, including empty months."""
    now = datetime.now().astimezone()
    number = now.year * 12 + now.month - 1
    paths = []
    for offset in range(months):
        year, zero_month = divmod(number - offset, 12)
        path = _usage_path(admin_id, year, zero_month + 1)
        if os.path.isfile(path):
            paths.append(path)
    return paths


def _runtime_usage(usage: Any) -> Optional[tuple[int, int]]:
    """Extract one turn's measured tokens, never a cumulative thread total."""
    if not isinstance(usage, dict):
        return None
    # Legacy Codex runs stored the raw {total, last} supplier notification.
    # `last` is one model response, not the whole Jellyfish turn, and `total`
    # is the native thread ledger. Neither can be treated as this run's total.
    if "last" in usage or "total" in usage:
        return None
    inp = usage.get("inputTokens", usage.get("input_tokens"))
    out = usage.get("outputTokens", usage.get("output_tokens"))
    if inp is None or out is None:
        return None
    try:
        inp, out = int(inp), int(out)
    except (TypeError, ValueError):
        return None
    return (inp, out) if inp >= 0 and out >= 0 and inp + out > 0 else None


def _runtime_runs(admin_id: str) -> tuple[List[Dict[str, Any]], bool]:
    """Read durable CLI runs without starting the scheduler or changing state."""
    root = Path(os.getenv("JELLYFISH_RUNTIME_DATA_DIR", str(Path(ROOT_DIR) / "data" / "runtime"))).resolve()
    path = root / "runtime.sqlite3"
    if not path.is_file():
        return [], False
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
            rows = db.execute(
                "SELECT data FROM records WHERE kind='run' AND json_extract(data,'$.actor_id')=?",
                (admin_id,),
            )
            return [json.loads(raw) for raw, in rows], False
    except (sqlite3.Error, OSError, ValueError):
        log.warning("Unable to read local CLI usage snapshot", exc_info=True)
        return [], True


def aggregate_usage(
    admin_id: str, *, months: int = 3, service_id: Optional[str] = None
) -> Dict[str, Any]:
    """聚合当前 admin 自己的 token 用量（admin web 端「用量统计」用）。

    返回 ``{total, by_model, by_service, by_key, by_provider, by_channel, by_day,
    by_billing, by_core, coverage}``，
    每个 by_* 为 ``[{name, calls, input_tokens, output_tokens, total_tokens}]``。
    ``by_day`` 按日期升序，其余按 total_tokens 降序。``name`` 是原始 id（service_id /
    key_id），友好名称映射由路由层补（避免本模块依赖 published）。

    若传 ``service_id``，只统计该 service 的 JSONL Token 记录与未完整上报的
    Service CLI 运行；为 ``None`` 时还合并主链路和定时任务的 CLI Runtime run。
    已计量的 Service CLI 不从 run 再算一次。
    """
    months = max(1, min(int(months or 3), 24))
    svc_filter = None if service_id is None else str(service_id)

    total = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    buckets: Dict[str, Dict[str, Dict[str, int]]] = {
        "model": {}, "service": {}, "key": {}, "provider": {}, "channel": {},
        "day": {}, "billing": {}, "core": {},
    }
    unreported: Dict[str, int] = {}
    runtime_read_error = False

    def _add(dim: str, name: str, inp: int, out: int, tot: int) -> None:
        b = buckets[dim].setdefault(
            name, {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        )
        b["calls"] += 1
        b["input_tokens"] += inp
        b["output_tokens"] += out
        b["total_tokens"] += tot

    from app.core.jsonl_store import read_jsonl

    def _count(rec: Dict[str, Any], inp: int, out: int, *, runtime: str) -> None:
        tot = inp + out
        total["calls"] += 1
        total["input_tokens"] += inp
        total["output_tokens"] += out
        total["total_tokens"] += tot
        model = str(rec.get("model", "") or "") or "(未知)"
        _add("model", model, inp, out, tot)
        _add("provider", _provider_of(model), inp, out, tot)
        _add("service", str(rec.get("service_id", "") or ""), inp, out, tot)
        _add("key", str(rec.get("key_id", "") or ""), inp, out, tot)
        _add("channel", str(rec.get("channel", "") or "") or "(其它)", inp, out, tot)
        _add("billing", str(rec.get("billing", "") or "hosted"), inp, out, tot)
        _add("core", runtime, inp, out, tot)
        day = str(rec.get("ts", "") or "")[:10]
        if len(day) == 10:
            _add("day", day, inp, out, tot)

    for path in _month_paths(admin_id, months):
        for rec in read_jsonl(path):
            try:
                inp = int(rec.get("input_tokens", 0) or 0)
                out = int(rec.get("output_tokens", 0) or 0)
            except (TypeError, ValueError):
                continue
            if inp < 0 or out < 0 or inp + out <= 0:
                continue
            if svc_filter is not None and str(rec.get("service_id", "") or "") != svc_filter:
                continue
            model = str(rec.get("model", "") or "") or "(未知)"
            runtime = str(rec.get("runtime", "") or "") or (model.split(":", 1)[0] if model.startswith(("codex:", "cursor:")) else "deepagents")
            _count(rec, inp, out, runtime=runtime)

    # Service CLI turns are already written to llm_usage by RuntimeConsumerAgent.
    # Never sum their durable runs again. We still inspect them for missing
    # telemetry, including in the single-Service view.
    now = datetime.now().astimezone()
    number = now.year * 12 + now.month - 1
    first_year, first_zero_month = divmod(number - months + 1, 12)
    first = now.replace(year=first_year, month=first_zero_month + 1,
                        day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
    next_year, next_zero_month = divmod(number + 1, 12)
    end = now.replace(year=next_year, month=next_zero_month + 1,
                      day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
    runs, runtime_read_error = _runtime_runs(admin_id)
    for run in runs:
        binding = run.get("binding") or {}
        runtime = binding.get("runtime")
        if runtime not in ("codex", "cursor"):
            continue
        scope = binding.get("service_scope") or {}
        if svc_filter is not None and scope.get("service_id") != svc_filter:
            continue
        used_at = run.get("finished_at") or run.get("updated_at") or run.get("started_at") or run.get("created_at")
        if not isinstance(used_at, (int, float)) or not first <= used_at < end:
            continue
        usage = _runtime_usage(run.get("usage"))
        if usage is None:
            # Legacy Service Codex wrote only the last model response to JSONL.
            # It may undercount a multi-response turn, so flag its coverage as
            # incomplete even though the partial tokens remain in the total.
            if (run.get("started_at")
                    and run.get("status") in ("completed", "failed", "cancelled")):
                unreported[runtime] = unreported.get(runtime, 0) + 1
            continue
        if scope:
            continue
        inp, out = usage
        model = str(binding.get("model") or "")
        rec = {"model": runtime + ":" + model, "service_id": "", "key_id": "",
               "channel": "scheduler" if binding.get("scheduler_scope") else run.get("channel", "web"),
               "billing": "hosted", "ts": datetime.fromtimestamp(used_at).astimezone().isoformat()}
        _count(rec, inp, out, runtime=runtime)

    def _sorted(dim: str, by_name: bool = False) -> List[Dict[str, Any]]:
        items = [{"name": k, **v} for k, v in buckets[dim].items()]
        if by_name:
            items.sort(key=lambda x: x["name"])
        else:
            items.sort(key=lambda x: x["total_tokens"], reverse=True)
        return items

    return {
        "total": total,
        "months_scanned": months,
        "by_model": _sorted("model"),
        "by_service": _sorted("service"),
        "by_key": _sorted("key"),
        "by_provider": _sorted("provider"),
        "by_channel": _sorted("channel"),
        "by_day": _sorted("day", by_name=True),
        "by_billing": _sorted("billing"),
        "by_core": _sorted("core"),
        "coverage": {"unreported_runs": sum(unreported.values()),
                     "unreported_by_core": unreported,
                     "runtime_read_error": runtime_read_error},
    }


# ── LangChain 回调：统一捕获 token 用量 ──────────────────────────────────

def _extract_usage(response: Any) -> tuple[int, int]:
    """从 LLMResult 聚合 (input_tokens, output_tokens)。

    优先 generations[*].message.usage_metadata（LangChain 规范字段，
    ChatAnthropic/ChatOpenAI/我们的 Bedrock 都会带）；
    回退 llm_output['token_usage']（OpenAI 旧式）。
    """
    in_tok = 0
    out_tok = 0
    try:
        for gen_list in getattr(response, "generations", []) or []:
            for gen in gen_list or []:
                msg = getattr(gen, "message", None)
                um = getattr(msg, "usage_metadata", None) if msg is not None else None
                if um:
                    in_tok += int(um.get("input_tokens", 0) or 0)
                    out_tok += int(um.get("output_tokens", 0) or 0)
    except Exception:
        pass
    if in_tok == 0 and out_tok == 0:
        try:
            tu = (getattr(response, "llm_output", None) or {}).get("token_usage") or {}
            in_tok = int(tu.get("prompt_tokens", 0) or 0)
            out_tok = int(tu.get("completion_tokens", 0) or 0)
        except Exception:
            pass
    return in_tok, out_tok


def _extract_model(response: Any, fallback: str) -> str:
    try:
        lo = getattr(response, "llm_output", None) or {}
        m = lo.get("model_name") or lo.get("model")
        if m:
            return str(m)
    except Exception:
        pass
    try:
        for gen_list in getattr(response, "generations", []) or []:
            for gen in gen_list or []:
                msg = getattr(gen, "message", None)
                rm = getattr(msg, "response_metadata", None) if msg is not None else None
                if rm:
                    m = rm.get("model_name") or rm.get("model")
                    if m:
                        return str(m)
    except Exception:
        pass
    return fallback or ""


def _make_token_usage_callback(
    admin_id: str,
    *,
    service_id: str = "",
    channel: str = "",
    conv_id: str = "",
    model_hint: str = "",
    key_id: str = "",
    billing: str = "hosted",
):
    """惰性构建 TokenUsageCallback 实例（避免在无 LLM 调用时导入 langchain）。"""
    from langchain_core.callbacks.base import BaseCallbackHandler

    class TokenUsageCallback(BaseCallbackHandler):
        """在每次 LLM 调用结束时把 token 用量落盘。全程吞异常。"""

        def on_llm_end(self, response: Any, **kwargs: Any) -> None:  # noqa: D401
            try:
                in_tok, out_tok = _extract_usage(response)
                if in_tok == 0 and out_tok == 0:
                    return
                model = _extract_model(response, model_hint)
                record_llm_usage(
                    admin_id,
                    model,
                    in_tok,
                    out_tok,
                    service_id=service_id,
                    channel=channel,
                    conv_id=conv_id,
                    key_id=key_id,
                    billing=billing,
                )
            except Exception:
                pass

    return TokenUsageCallback()


def build_usage_callbacks(
    admin_id: str,
    *,
    service_id: str = "",
    channel: str = "",
    conv_id: str = "",
    model_hint: str = "",
    key_id: str = "",
    billing: str = "hosted",
) -> List[Any]:
    """返回注入 agent ``config["callbacks"]`` 的回调列表：
    Langfuse（若启用）+ token 用量回调（若有 admin_id）。

    各 astream/ainvoke 调用点用它替代裸 ``get_langfuse_callbacks()`` 即可。
    ``key_id`` 仅消费侧（service API key 调用）传入，admin 主链路留空。
    """
    cbs: List[Any] = list(get_langfuse_callbacks())
    if admin_id:
        try:
            cbs.append(
                _make_token_usage_callback(
                    admin_id,
                    service_id=service_id,
                    channel=channel,
                    conv_id=conv_id,
                    model_hint=model_hint,
                    key_id=key_id,
                    billing=billing,
                )
            )
        except Exception:
            pass
    return cbs
