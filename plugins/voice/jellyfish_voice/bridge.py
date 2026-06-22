"""桥接:Worker → JellyfishBot Core 的任务委派(远程 SSE,路1 解耦)。

Worker 不内嵌 JellyfishBot agent,而是通过 Core 的
``POST /api/voice/live/delegate`` 远程驱动它,消费与 ``/api/chat`` 完全相同的
SSE 事件流。这样:
- 任务跑在 Core 进程里,与语音对话进程互不干扰;
- 语音插件可独立部署/独立演进,只依赖这套窄事件契约;
- 同一套契约将来可同时服务 admin 与 service(只换委派目标)。

事件词汇(摘要,与 app/routes/chat.py 一致):
  token / thinking / tool_call / tool_call_chunk / tool_result /
  subagent_call / subagent_start / subagent_end / interrupt /
  auto_approve / done / error
"""

from __future__ import annotations

import json
from typing import AsyncIterator, Dict

import httpx

from .config import api_base


async def stream_delegate(
    bridge_token: str,
    message: str,
    *,
    connect_timeout: float = 15.0,
    read_timeout: float = 600.0,
) -> AsyncIterator[Dict]:
    """委派一段指令,异步产出解析后的 SSE 事件 dict。

    Cloudflare/反代可能在 ~100s 切断;Core 的 finally 会持久化已生成内容。
    这里把网络中断也作为一个 ``{type: 'error'}`` 事件抛出,由上层决定话术。
    """
    url = f"{api_base()}/api/voice/live/delegate"
    headers = {"X-Bridge-Token": bridge_token, "Accept": "text/event-stream"}
    payload = {"message": message}
    timeout = httpx.Timeout(read_timeout, connect=connect_timeout)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    yield {"type": "error", "content": f"delegate HTTP {resp.status_code}: {body[:200]!r}"}
                    return
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    raw = line[len("data:"):].strip()
                    if not raw:
                        continue
                    try:
                        yield json.loads(raw)
                    except json.JSONDecodeError:
                        continue
    except (httpx.HTTPError, httpx.StreamError) as e:
        yield {"type": "error", "content": f"网络中断: {type(e).__name__}: {e}"}


def collect_final_text(buffer: str, event: Dict) -> str:
    """把一个 token 事件累加到最终答案缓冲(供工具返回给前台 LLM 朗读)。"""
    if event.get("type") == "token":
        return buffer + (event.get("content") or "")
    return buffer
