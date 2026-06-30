"""
LangChain ChatModel wrapper for AWS Bedrock Runtime (Bearer Token auth).

Calls the Bedrock InvokeModel API via REST:
  POST https://bedrock-runtime.{region}.amazonaws.com/model/{model_id}/invoke

Authentication uses a Bearer Token (Bedrock API Key, ABSK-prefixed),
NOT standard IAM SigV4. This is the newer authentication method supported
by both bedrock-runtime and bedrock-mantle endpoints.

Anthropic models use the Messages API request format internally.
"""

from __future__ import annotations

import base64
import json
import logging
import struct
from typing import Any, AsyncIterator, Dict, Iterator, List, Optional, Tuple

import httpx
from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models.chat_models import (
    BaseChatModel,
    agenerate_from_stream,
    generate_from_stream,
)
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    AIMessageChunk,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import Field

log = logging.getLogger(__name__)

_BEDROCK_RUNTIME_BASE = "https://bedrock-runtime.{region}.amazonaws.com"

# Model ID → Bedrock Inference Profile ID mapping
# Newer models require the "us." geo-prefix for on-demand invocation.
_INFERENCE_PROFILE_MAP: Dict[str, str] = {
    "claude-opus-4-7": "us.anthropic.claude-opus-4-7",
    "claude-opus-4-6": "us.anthropic.claude-opus-4-6-v1",
    "claude-opus-4-5": "us.anthropic.claude-opus-4-5-20251101-v1:0",
    "claude-opus-4-1": "us.anthropic.claude-opus-4-1-20250805-v1:0",
    "claude-sonnet-4-6": "us.anthropic.claude-sonnet-4-6",
    "claude-sonnet-4-5": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "claude-haiku-4-5": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
}


def _to_bedrock_model_id(short_name: str) -> str:
    """Map a short model name to the full Bedrock inference profile ID."""
    if short_name in _INFERENCE_PROFILE_MAP:
        return _INFERENCE_PROFILE_MAP[short_name]
    # If already a full ID (contains 'anthropic.'), pass through
    if "anthropic." in short_name:
        return short_name
    return short_name


# ──────────────────────────────────────────────────────────────────────
# AWS event-stream binary frame parser
#
# Frame layout (big-endian):
#   total_len(4) | headers_len(4) | prelude_crc(4) | headers... | payload... | msg_crc(4)
#
# Headers: name_len(1) | name | type(1) | [type-dependent body]
#   - type 7 (string) body = value_len(2) | value
#
# 我们只关心：
#   :event-type   = "chunk"      → 正常事件，payload JSON 含 base64 编码的 Anthropic 事件
#   :message-type = "exception"  → 错误事件，payload JSON 是错误描述
# ──────────────────────────────────────────────────────────────────────


def _parse_eventstream_headers(buf: bytes) -> Dict[str, str]:
    """解析 headers 段，仅提取 string 类型 header（够用于判断 :event-type / :message-type）。"""
    headers: Dict[str, str] = {}
    pos = 0
    n = len(buf)
    while pos < n:
        if pos + 1 > n:
            break
        name_len = buf[pos]
        pos += 1
        if pos + name_len > n:
            break
        name = buf[pos:pos + name_len].decode("utf-8", errors="replace")
        pos += name_len
        if pos + 1 > n:
            break
        htype = buf[pos]
        pos += 1
        # type 7 = string，常见 metadata 几乎都是 string
        if htype == 7:
            if pos + 2 > n:
                break
            (val_len,) = struct.unpack(">H", buf[pos:pos + 2])
            pos += 2
            if pos + val_len > n:
                break
            headers[name] = buf[pos:pos + val_len].decode("utf-8", errors="replace")
            pos += val_len
        else:
            # 其它类型直接跳过整个 headers（简化处理；见 AWS docs Type 0~9）。
            break
    return headers


def _try_extract_eventstream_message(
    buffer: bytearray,
) -> Optional[Tuple[Dict[str, str], bytes, int]]:
    """尝试从 buffer 解析一条完整 eventstream 消息。

    返回 (headers, payload_bytes, consumed_bytes)，buffer 不足时返回 None。
    解析失败（坏数据）时返回 (空 headers, 空 payload, 0) 让调用方丢弃 buffer。
    """
    if len(buffer) < 12:
        return None
    (total_len,) = struct.unpack(">I", bytes(buffer[0:4]))
    if total_len < 16 or total_len > 16 * 1024 * 1024:
        # 异常长度（>16MB 或不合法），按坏数据处理 → 让外层抛错
        return ({}, b"", 0)
    if len(buffer) < total_len:
        return None
    (headers_len,) = struct.unpack(">I", bytes(buffer[4:8]))
    headers_end = 12 + headers_len
    payload_end = total_len - 4
    if headers_end > payload_end or payload_end > total_len:
        return ({}, b"", 0)
    headers = _parse_eventstream_headers(bytes(buffer[12:headers_end]))
    payload = bytes(buffer[headers_end:payload_end])
    return headers, payload, total_len


async def _iter_bedrock_stream_events(
    response: httpx.Response,
) -> AsyncIterator[Dict[str, Any]]:
    """对 invoke-with-response-stream 的响应做帧切分 + base64 解包，
    逐条产出 Anthropic 流式事件 JSON（如 message_start / content_block_delta / ...）。
    """
    buf = bytearray()
    async for chunk in response.aiter_bytes():
        if not chunk:
            continue
        buf.extend(chunk)
        while True:
            parsed = _try_extract_eventstream_message(buf)
            if parsed is None:
                break  # 数据不完整，等下一个 chunk
            headers, payload, consumed = parsed
            if consumed == 0:
                # 坏数据：不再尝试解析，直接抛错
                raise RuntimeError("Bedrock event-stream: malformed frame")
            del buf[:consumed]

            msg_type = headers.get(":message-type", "event")
            event_type = headers.get(":event-type", "")

            if msg_type == "exception" or event_type in ("internalServerException",
                                                         "modelStreamErrorException",
                                                         "validationException",
                                                         "throttlingException",
                                                         "modelTimeoutException",
                                                         "serviceUnavailableException"):
                try:
                    err = json.loads(payload.decode("utf-8", errors="replace"))
                except Exception:
                    err = {"message": payload[:500].decode("utf-8", errors="replace")}
                raise RuntimeError(
                    f"Bedrock stream error ({event_type or msg_type}): {err.get('message', err)}"
                )

            # 正常事件帧：payload 是 {"bytes": "<base64>"} 形式
            try:
                wrapper = json.loads(payload.decode("utf-8", errors="replace"))
            except Exception:
                continue
            inner_b64 = wrapper.get("bytes")
            if not inner_b64:
                # 也可能直接就是 Anthropic 事件 JSON
                if "type" in wrapper:
                    yield wrapper
                continue
            try:
                inner_bytes = base64.b64decode(inner_b64)
                event = json.loads(inner_bytes.decode("utf-8", errors="replace"))
            except Exception as e:
                log.warning("Failed to decode Bedrock stream payload: %s", e)
                continue
            yield event


def _convert_image_block(block: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """OpenAI 风格 image 块 → Anthropic image 块。

    支持两种输入：
    - OpenAI 风格：{"type": "image_url", "image_url": {"url": "data:image/png;base64,XXX"}}
                   或 {"type": "image_url", "image_url": "data:..."}（url 直接是字符串）
    - LangChain v1 标准块：{"type": "image", "source_type": "base64", "data": ..., "mime_type": ...}
                           {"type": "image", "source_type": "url", "url": ...}

    输出 Anthropic 格式：
    - {"type": "image", "source": {"type": "base64", "media_type": ..., "data": ...}}
    - {"type": "image", "source": {"type": "url", "url": ...}}
    """
    btype = block.get("type", "")

    # 已是 Anthropic 原生格式（含 source.type）→ 原样返回
    if btype == "image" and isinstance(block.get("source"), dict) and block["source"].get("type"):
        return {"type": "image", "source": block["source"]}

    # 取出 URL（可能是 dict 或 str）
    url = ""
    if btype == "image_url":
        iu = block.get("image_url")
        if isinstance(iu, dict):
            url = iu.get("url", "") or ""
        elif isinstance(iu, str):
            url = iu
    elif btype == "image":
        # LangChain v1 标准块
        source_type = block.get("source_type", "")
        if source_type == "base64":
            data = block.get("data", "")
            media_type = block.get("mime_type") or block.get("media_type") or "image/png"
            if data:
                return {"type": "image", "source": {
                    "type": "base64", "media_type": media_type, "data": data,
                }}
            return None
        if source_type == "url":
            url = block.get("url", "") or ""

    if not url:
        return None

    if url.startswith("data:"):
        # data:image/png;base64,XXXX
        try:
            header, b64 = url.split(",", 1)
            media_type = header.split(":", 1)[1].split(";", 1)[0] if ":" in header else "image/png"
        except (ValueError, IndexError):
            return None
        if not b64:
            return None
        return {"type": "image", "source": {
            "type": "base64", "media_type": media_type, "data": b64,
        }}

    # 远程 URL
    return {"type": "image", "source": {"type": "url", "url": url}}


def _convert_file_block(block: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """file 块（OpenAI / LangChain v1）→ Anthropic document 块。

    Anthropic document 仅支持 PDF(base64/url) 与纯文本(text)。其它二进制类型
    无法直接发送，降级为一段 text 说明，避免 400。

    支持输入：
    - LangChain v1：{"type": "file", "source_type": "base64", "data": ..., "mime_type": ...}
                    {"type": "file", "source_type": "url", "url": ...}
                    {"type": "file", "source_type": "text", "text": ...}
    - OpenAI 风格：{"type": "file", "file": {"file_data": "data:...;base64,...", "filename": ...}}
    """
    filename = block.get("filename") or ""
    mime = block.get("mime_type") or block.get("media_type") or ""
    data = ""
    url = ""
    text = ""

    source_type = block.get("source_type", "")
    if source_type == "base64":
        data = block.get("data", "") or ""
    elif source_type == "url":
        url = block.get("url", "") or ""
    elif source_type == "text":
        text = block.get("text", "") or ""
    else:
        # OpenAI 风格 {"file": {"file_data": "data:...;base64,...", "filename": ...}}
        f = block.get("file")
        if isinstance(f, dict):
            filename = filename or f.get("filename", "")
            file_data = f.get("file_data", "") or f.get("url", "") or ""
            if file_data.startswith("data:"):
                try:
                    header, b64 = file_data.split(",", 1)
                    mime = mime or (header.split(":", 1)[1].split(";", 1)[0] if ":" in header else "")
                    data = b64
                except (ValueError, IndexError):
                    data = ""
            elif file_data.startswith("http"):
                url = file_data
        # 也可能直接平铺 data 字段
        if not data and not url and not text:
            data = block.get("data", "") or ""

    is_pdf = "pdf" in (mime or "").lower() or filename.lower().endswith(".pdf")

    # PDF base64 → document
    if data and is_pdf:
        return {"type": "document", "source": {
            "type": "base64", "media_type": "application/pdf", "data": data,
        }}

    # PDF URL → document
    if url and (is_pdf or not mime):
        return {"type": "document", "source": {"type": "url", "url": url}}

    # 纯文本 → document(text)
    if text:
        return {"type": "document", "source": {
            "type": "text", "media_type": "text/plain", "data": text,
        }}

    # 无法表达的二进制（非 PDF）→ 降级为 text 说明
    label = filename or mime or "文件"
    return {"type": "text", "text": f"[附件：{label}，当前模型无法直接读取该格式，请用文档/文件工具处理]"}


def _sanitize_block_for_anthropic(block: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """清洗 content block，使其符合 Anthropic API 要求。

    - 去掉 LangChain 流式 merge 用的 ``index`` 元字段（Anthropic 不识别会 400）。
    - thinking block 必须含 ``signature``，缺则丢弃整个 block（避免下一轮 400）。
    - 空 text block 丢弃（Anthropic 不允许空 text）。
    - image_url（OpenAI 风格）/ LangChain v1 image 块 → Anthropic image 块。
    """
    btype = block.get("type", "")
    cleaned = {k: v for k, v in block.items() if k != "index"}

    if btype == "thinking":
        if not cleaned.get("signature") or not cleaned.get("thinking"):
            return None
        return cleaned

    if btype == "text":
        if not cleaned.get("text"):
            return None
        return cleaned

    if btype == "tool_use":
        if not cleaned.get("id") or not cleaned.get("name"):
            return None
        cleaned.setdefault("input", {})
        return cleaned

    if btype == "tool_result":
        if not cleaned.get("tool_use_id"):
            return None
        # tool_result.content 也可能是含 image_url 的列表（视觉工具返回）
        inner = cleaned.get("content")
        if isinstance(inner, list):
            cleaned["content"] = _sanitize_content_list(inner)
        return cleaned

    if btype in ("image_url", "image"):
        return _convert_image_block(block)

    if btype == "file":
        return _convert_file_block(block)

    # 其它类型（document / search_result 等）原样透传
    return cleaned


def _sanitize_content_list(content: List[Any]) -> List[Dict[str, Any]]:
    """清洗一个 content block 列表（用于 HumanMessage / tool_result.content）。"""
    out: List[Dict[str, Any]] = []
    for item in content:
        if isinstance(item, dict):
            cleaned = _sanitize_block_for_anthropic(item)
            if cleaned is not None:
                out.append(cleaned)
        elif isinstance(item, str):
            if item:
                out.append({"type": "text", "text": item})
    return out


def _ai_content_to_blocks(content: Any) -> List[Dict[str, Any]]:
    """把 LangChain AIMessage.content 规范化为 Anthropic content blocks 列表。

    - str → [{"type": "text", "text": ...}]（空串 → 空列表）
    - list[dict|str] → 透传 dict（含 thinking / text / tool_use 等），str 包成 text
    - 其它 → str() 转字符串再包 text
    所有 block 都过 ``_sanitize_block_for_anthropic`` 清洗。
    """
    blocks: List[Dict[str, Any]] = []
    if isinstance(content, str):
        if content:
            blocks.append({"type": "text", "text": content})
        return blocks
    if isinstance(content, list):
        return _sanitize_content_list(content)
    s = str(content)
    if s:
        blocks.append({"type": "text", "text": s})
    return blocks


def _tool_result_block(msg: "ToolMessage") -> Dict[str, Any]:
    """ToolMessage → Anthropic tool_result block。content 支持 str/list（多模态）。

    视觉工具（view_pdf_page_or_image 等）会返回含 image_url 的 list，
    需转成 Anthropic image 块，否则 400。
    """
    raw = msg.content
    if isinstance(raw, list):
        result_content: Any = _sanitize_content_list(raw)
    elif isinstance(raw, str):
        result_content = raw
    else:
        result_content = str(raw)
    return {
        "type": "tool_result",
        "tool_use_id": msg.tool_call_id,
        "content": result_content,
    }


def _convert_messages(messages: List[BaseMessage]) -> tuple[Optional[str], List[Dict[str, Any]]]:
    """LangChain messages → Anthropic Messages API 格式。

    关键点：
    - AIMessage.tool_calls 会被合并到 content 末尾的 tool_use blocks，
      否则下一轮的 ToolMessage(tool_result) 会找不到对应 tool_use → 400。
    - 连续多个 ToolMessage 必须合并到同一个 user 消息的多个 tool_result block。
    - AIMessage.content 为 list 时透传（保留 thinking/text/tool_use 子结构）。
    """
    system: Optional[str] = None
    api_messages: List[Dict[str, Any]] = []

    for msg in messages:
        if isinstance(msg, SystemMessage):
            system = msg.content if isinstance(msg.content, str) else str(msg.content)
            continue

        if isinstance(msg, HumanMessage):
            content = msg.content
            if isinstance(content, list):
                # 清洗 list content：image_url → Anthropic image，过滤空 text 等
                api_messages.append({"role": "user", "content": _sanitize_content_list(content)})
            else:
                api_messages.append({"role": "user", "content": content if isinstance(content, str) else str(content)})
            continue

        if isinstance(msg, AIMessage):
            blocks = _ai_content_to_blocks(msg.content)

            # 把 LangChain tool_calls 转成 tool_use block；若 content list 内已含相同 id 则跳过
            existing_ids = {b.get("id") for b in blocks if isinstance(b, dict) and b.get("type") == "tool_use"}
            for tc in (getattr(msg, "tool_calls", None) or []):
                tc_id = tc.get("id") or ""
                if tc_id and tc_id in existing_ids:
                    continue
                blocks.append({
                    "type": "tool_use",
                    "id": tc_id,
                    "name": tc.get("name", ""),
                    "input": tc.get("args") or {},
                })

            # Anthropic 要求 assistant content 非空且 text block 不可为空字符串。
            # 既无文字又无工具调用的空 assistant turn(如模型返回空补全时我们兜底产出的
            # 空 turn)直接跳过——若塞 {"type":"text","text":""} 反而会让下一轮 400。
            if not blocks:
                continue
            api_messages.append({"role": "assistant", "content": blocks})
            continue

        if isinstance(msg, ToolMessage):
            block = _tool_result_block(msg)
            # 紧邻前一条 user 消息且全部是 tool_result 时，并入同一条
            if api_messages and api_messages[-1]["role"] == "user":
                prev = api_messages[-1]["content"]
                if isinstance(prev, list) and prev and all(
                    isinstance(b, dict) and b.get("type") == "tool_result" for b in prev
                ):
                    prev.append(block)
                    continue
            api_messages.append({"role": "user", "content": [block]})
            continue

    return system, api_messages


class ChatBedrockInvoke(BaseChatModel):
    """LangChain ChatModel that calls Bedrock InvokeModel with Bearer Token."""

    model_name: str = Field(description="Short model name (e.g. 'claude-sonnet-4-6')")
    api_key: str = Field(description="Bedrock API Key (Bearer Token)")
    region: str = Field(default="us-east-1", description="AWS Region")
    max_tokens: int = Field(default=8192, description="Max output tokens")
    temperature: Optional[float] = Field(default=None, description="Sampling temperature")
    top_p: Optional[float] = Field(default=None, description="Top-p sampling")
    timeout: float = Field(default=300.0, description="HTTP request timeout in seconds")

    # Extended thinking support
    thinking: Optional[Dict[str, Any]] = Field(default=None, description="Extended thinking config")

    # Tools bound via bind_tools()，运行时合并到 InvokeModel 请求体的 tools 字段
    bound_tools: Optional[List[Dict[str, Any]]] = Field(default=None, description="Tools bound via bind_tools()")

    _client: Optional[httpx.Client] = None
    _aclient: Optional[httpx.AsyncClient] = None

    class Config:
        arbitrary_types_allowed = True

    @property
    def _llm_type(self) -> str:
        return "bedrock-invoke"

    @property
    def _identifying_params(self) -> Dict[str, Any]:
        return {
            "model_name": self.model_name,
            "region": self.region,
            "max_tokens": self.max_tokens,
        }

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            object.__setattr__(self, "_client", httpx.Client(timeout=self.timeout))
        return self._client

    def _get_aclient(self) -> httpx.AsyncClient:
        if self._aclient is None:
            object.__setattr__(self, "_aclient", httpx.AsyncClient(timeout=self.timeout))
        return self._aclient

    def close(self) -> None:
        """关闭同步 httpx client（GC 前主动调用避免 ResourceWarning）。"""
        c = self._client
        if c is not None:
            try:
                c.close()
            except Exception:
                pass
            object.__setattr__(self, "_client", None)

    async def aclose(self) -> None:
        """关闭异步 httpx client。Agent cache 淘汰时建议显式调用。"""
        ac = self._aclient
        if ac is not None:
            try:
                await ac.aclose()
            except Exception:
                pass
            object.__setattr__(self, "_aclient", None)

    def __del__(self) -> None:
        # GC 兜底：仅同步 client 能在 __del__ 关闭。AsyncClient 需要 event loop
        # 才能 aclose，这里做不到，依赖 httpx 自身 __del__ 警告 + agent.aclose()。
        try:
            self.close()
        except Exception:
            pass

    def _build_url(self, *, stream: bool = False) -> str:
        base = _BEDROCK_RUNTIME_BASE.format(region=self.region)
        model_id = _to_bedrock_model_id(self.model_name)
        path = "invoke-with-response-stream" if stream else "invoke"
        return f"{base}/model/{model_id}/{path}"

    def _build_headers(self, *, stream: bool = False) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/vnd.amazon.eventstream" if stream else "application/json",
        }

    def _build_body(
        self, system: Optional[str], messages: List[Dict[str, Any]], **kwargs
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "messages": messages,
        }

        if system:
            body["system"] = system

        # Opus 4.7 does NOT accept temperature/top_p/top_k
        is_opus_47 = "opus-4-7" in self.model_name
        if not is_opus_47:
            if self.temperature is not None:
                body["temperature"] = self.temperature
            if self.top_p is not None:
                body["top_p"] = self.top_p

        # Extended thinking
        thinking = kwargs.get("thinking", self.thinking)
        if thinking:
            body["thinking"] = thinking

        # Tools: 优先取调用时 kwargs，否则用 bind_tools 记录的 self.bound_tools
        tools = kwargs.get("tools") or self.bound_tools
        if tools:
            # Fine-grained tool streaming：默认 Claude 会缓冲整个工具入参 JSON 做校验后
            # 才一次性吐出（input_json_delta 全憋到最后）。这会导致：
            #  1) 前端 write_file/edit_file 卡片整个过程拿不到增量内容，只显示"正在写入…"
            #  2) 缓冲期间 SSE 长时间无事件 → Cloudflare 100s 超时切断 → 纯工具调用时
            #     full_response 为空，连接中断后已生成内容丢失。
            # 开启 fine-grained streaming 后，工具入参不缓冲、逐段 partial_json 实时流出。
            # 见 AWS Bedrock 文档 model-parameters-anthropic-claude-messages-tool-use。
            body["anthropic_beta"] = ["fine-grained-tool-streaming-2025-05-14"]
            body["tools"] = [
                {**t, "eager_input_streaming": True} if isinstance(t, dict) else t
                for t in tools
            ]

        return body

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        """非流式生成：内部走 InvokeModelWithResponseStream 并聚合 chunk。

        历史上这里直接 POST 非流式 InvokeModel 端点，但 thinking 模型（尤其
        Claude Opus 4.7 的 adaptive thinking）在非流式端点上会被 Bedrock 以
        503「unable to process your request」拒绝（deepagents 的摘要中间件用
        ainvoke 调用模型时必现）。统一路由到流式端点 + ``generate_from_stream``
        聚合，既绕开会失败的非流式端点，又复用 ``_stream`` 的空流兜底/重试。
        """
        stream_iter = self._stream(
            messages, stop=stop, run_manager=run_manager, **kwargs
        )
        return generate_from_stream(stream_iter)

    async def _agenerate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        """异步非流式生成：内部走流式端点聚合（理由同 ``_generate``）。"""
        stream_iter = self._astream(
            messages, stop=stop, run_manager=run_manager, **kwargs
        )
        return await agenerate_from_stream(stream_iter)

    def _anthropic_event_to_chunk(
        self, event: Dict[str, Any], state: Dict[str, Any]
    ) -> Optional[ChatGenerationChunk]:
        """把单个 Anthropic 流式事件转成 LangChain ChatGenerationChunk。

        state 在多次调用间保留：跨事件累积每个 content block 的元信息
        （工具调用 id/name/index、是否在 thinking 块）。
        """
        ev_type = event.get("type", "")

        if ev_type == "message_start":
            usage = event.get("message", {}).get("usage")
            if usage:
                state["usage_in"] = usage.get("input_tokens", 0)
            return None

        if ev_type == "content_block_start":
            block = event.get("content_block", {}) or {}
            idx = event.get("index", 0)
            btype = block.get("type", "")
            state.setdefault("blocks", {})[idx] = {"type": btype}
            if btype == "tool_use":
                # 记录工具调用的 id 与 name；args 等 input_json_delta 累积
                state["blocks"][idx]["id"] = block.get("id", "")
                state["blocks"][idx]["name"] = block.get("name", "")
                # 发出一个空 chunk 让外层先注册工具调用名（含 id），后续增量都靠 input_json_delta
                tc_chunk = {
                    "name": block.get("name", ""),
                    "args": "",
                    "id": block.get("id", ""),
                    "index": idx,
                }
                return ChatGenerationChunk(
                    message=AIMessageChunk(content="", tool_call_chunks=[tc_chunk])
                )
            return None

        if ev_type == "content_block_delta":
            delta = event.get("delta", {}) or {}
            dtype = delta.get("type", "")
            idx = event.get("index", 0)

            if dtype == "text_delta":
                text = delta.get("text", "") or ""
                if not text:
                    return None
                return ChatGenerationChunk(message=AIMessageChunk(content=text))

            if dtype == "thinking_delta":
                # 把 thinking 增量包成 list 形式的 content block（chat.py 已支持）。
                # 关键：必须带 "index" 字段，LangChain merge_content 会按 index 合并
                # 多个相同 index 的 dict（否则会变成 N 个独立 dict）。
                t = delta.get("thinking", "") or ""
                if not t:
                    return None
                return ChatGenerationChunk(
                    message=AIMessageChunk(
                        content=[{"type": "thinking", "thinking": t, "index": idx}]
                    )
                )

            if dtype == "signature_delta":
                # ⚠️ 必须保留：Anthropic 要求多轮对话历史里 thinking block 含 signature，
                # 否则下一轮 400 "Provided thinking block does not contain a signature"。
                # 通过 index 让 LangChain merge 到同一 thinking dict 上。
                sig = delta.get("signature", "") or ""
                if not sig:
                    return None
                return ChatGenerationChunk(
                    message=AIMessageChunk(
                        content=[{"type": "thinking", "signature": sig, "index": idx}]
                    )
                )

            if dtype == "input_json_delta":
                partial = delta.get("partial_json", "") or ""
                if not partial:
                    return None
                # 注意：args delta 阶段必须 name="" 且 id=""，否则
                # chat.py 会把每个 delta 当成"新工具调用"事件而不是参数累积。
                tc_chunk = {
                    "name": "",
                    "args": partial,
                    "id": "",
                    "index": idx,
                }
                return ChatGenerationChunk(
                    message=AIMessageChunk(content="", tool_call_chunks=[tc_chunk])
                )

            return None

        if ev_type == "message_delta":
            usage = event.get("usage")
            if usage:
                state["usage_out"] = usage.get("output_tokens", 0)
                state["stop_reason"] = event.get("delta", {}).get("stop_reason", "")
                # 发射 usage_metadata chunk：让 token 用量沿 LangChain 标准管线流出
                # （AIMessageChunk.__add__ 会累加，回调 on_llm_end 即可读到），
                # 从而被 token 统计回调统一捕获——与原生 ChatAnthropic/ChatOpenAI 一致。
                in_tok = int(state.get("usage_in", 0) or 0)
                out_tok = int(state.get("usage_out", 0) or 0)
                if in_tok or out_tok:
                    return ChatGenerationChunk(
                        message=AIMessageChunk(
                            content="",
                            usage_metadata={
                                "input_tokens": in_tok,
                                "output_tokens": out_tok,
                                "total_tokens": in_tok + out_tok,
                            },
                        )
                    )
            return None

        return None

    def _stream_once(
        self, body: Dict[str, Any], state: Dict[str, Any]
    ) -> Iterator[ChatGenerationChunk]:
        """单次同步流式调用：调 invoke-with-response-stream，逐帧解码 Anthropic 事件。

        把每次 HTTP 调用抽成独立生成器，便于上层在「零 chunk 空流」时安全重试。
        """
        url = self._build_url(stream=True)
        headers = self._build_headers(stream=True)
        client = httpx.Client(timeout=self.timeout)
        try:
            with client.stream("POST", url, headers=headers, json=body) as resp:
                if resp.status_code != 200:
                    err_body = resp.read()[:500].decode("utf-8", errors="replace")
                    raise RuntimeError(
                        f"Bedrock InvokeModelWithResponseStream failed ({resp.status_code}): {err_body}"
                    )
                buf = bytearray()
                for chunk in resp.iter_bytes():
                    if not chunk:
                        continue
                    buf.extend(chunk)
                    while True:
                        parsed = _try_extract_eventstream_message(buf)
                        if parsed is None:
                            break
                        h, payload, consumed = parsed
                        if consumed == 0:
                            raise RuntimeError("Bedrock event-stream: malformed frame")
                        del buf[:consumed]
                        msg_type = h.get(":message-type", "event")
                        if msg_type == "exception":
                            try:
                                err = json.loads(payload.decode("utf-8", errors="replace"))
                            except Exception:
                                err = {"message": payload[:500].decode("utf-8", errors="replace")}
                            raise RuntimeError(
                                f"Bedrock stream error: {err.get('message', err)}"
                            )
                        try:
                            wrapper = json.loads(payload.decode("utf-8", errors="replace"))
                        except Exception:
                            continue
                        inner_b64 = wrapper.get("bytes")
                        if inner_b64:
                            try:
                                event = json.loads(base64.b64decode(inner_b64).decode("utf-8", errors="replace"))
                            except Exception:
                                continue
                        elif "type" in wrapper:
                            event = wrapper
                        else:
                            continue
                        out = self._anthropic_event_to_chunk(event, state)
                        if out is not None:
                            yield out
        finally:
            client.close()

    def _stream(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        """同步真流式 + 空流兜底。

        Bedrock(尤其 thinking 模型 + 长上下文截断)偶发返回「只有 message_start/stop、
        无任何 content block」的空补全 → 0 chunk → LangChain ``generate_from_stream`` 抛
        ``No generations found in stream`` 把整轮 abort。这里：零 chunk 自动重试 1 次，
        仍空则产出一个空 AIMessage 让 agent 优雅收尾(不崩、不抛 traceback)。
        """
        system, api_messages = _convert_messages(messages)
        body = self._build_body(system, api_messages, **kwargs)
        if stop:
            body["stop_sequences"] = stop

        log.debug("Bedrock stream (sync): model=%s", self.model_name)

        yielded = 0
        state: Dict[str, Any] = {}
        for out in self._stream_once(body, state):
            yielded += 1
            yield out

        if yielded == 0:
            log.warning(
                "Bedrock 空流(sync, model=%s, stop_reason=%s)，重试 1 次",
                self.model_name, state.get("stop_reason"),
            )
            state = {}
            for out in self._stream_once(body, state):
                yielded += 1
                yield out

        if yielded == 0:
            log.warning(
                "Bedrock 重试后仍空流(sync, model=%s)，产出空 turn 优雅收尾",
                self.model_name,
            )
            yield ChatGenerationChunk(message=AIMessageChunk(content=""))

    async def _astream_once(
        self, body: Dict[str, Any], state: Dict[str, Any]
    ) -> AsyncIterator[ChatGenerationChunk]:
        """单次异步流式调用(抽出便于零 chunk 时安全重试)。"""
        url = self._build_url(stream=True)
        headers = self._build_headers(stream=True)
        client = self._get_aclient()
        async with client.stream("POST", url, headers=headers, json=body) as resp:
            if resp.status_code != 200:
                err_body = (await resp.aread())[:500].decode("utf-8", errors="replace")
                raise RuntimeError(
                    f"Bedrock InvokeModelWithResponseStream failed ({resp.status_code}): {err_body}"
                )
            async for event in _iter_bedrock_stream_events(resp):
                out = self._anthropic_event_to_chunk(event, state)
                if out is not None:
                    yield out

    async def _astream(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        """异步真流式 + 空流兜底(语义同 ``_stream``：空流重试 1 次→仍空产出空 turn)。"""
        system, api_messages = _convert_messages(messages)
        body = self._build_body(system, api_messages, **kwargs)
        if stop:
            body["stop_sequences"] = stop

        log.debug("Bedrock stream (async): model=%s", self.model_name)

        yielded = 0
        state: Dict[str, Any] = {}
        async for out in self._astream_once(body, state):
            yielded += 1
            yield out

        if yielded == 0:
            log.warning(
                "Bedrock 空流(async, model=%s, stop_reason=%s)，重试 1 次",
                self.model_name, state.get("stop_reason"),
            )
            state = {}
            async for out in self._astream_once(body, state):
                yielded += 1
                yield out

        if yielded == 0:
            log.warning(
                "Bedrock 重试后仍空流(async, model=%s)，产出空 turn 优雅收尾",
                self.model_name,
            )
            yield ChatGenerationChunk(message=AIMessageChunk(content=""))

    def _parse_response(self, data: Dict[str, Any]) -> ChatResult:
        """Parse Anthropic Messages API response into ChatResult.

        保留 thinking blocks（含 signature）以支持 thinking 多轮，避免历史回写
        时 Anthropic 因缺 signature 报错。当响应只含 text（无 thinking、无 tool）
        时退化为 plain string content 让常规消费方更易处理。
        """
        content_blocks = data.get("content", []) or []
        usage = data.get("usage", {})

        has_thinking = any(b.get("type") == "thinking" for b in content_blocks if isinstance(b, dict))
        has_tool_use = any(b.get("type") == "tool_use" for b in content_blocks if isinstance(b, dict))

        tool_calls: List[Dict[str, Any]] = []
        for block in content_blocks:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tool_calls.append({
                    "id": block.get("id", ""),
                    "name": block.get("name", ""),
                    "args": block.get("input", {}) or {},
                })

        if has_thinking:
            # 含 thinking → 必须用 list content 完整保留 thinking block（含 signature）
            content: Any = [b for b in content_blocks if isinstance(b, dict)]
        else:
            # 纯 text 或 text+tool_use：拼字符串便于常规消费（tool_calls 单独存）
            text_parts = [
                b.get("text", "") for b in content_blocks
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            content = "\n".join(text_parts) if text_parts else ""

        additional_kwargs: Dict[str, Any] = {}
        if usage:
            additional_kwargs["usage"] = usage

        if tool_calls:
            message = AIMessage(
                content=content,
                tool_calls=tool_calls,
                additional_kwargs=additional_kwargs,
            )
        else:
            message = AIMessage(content=content, additional_kwargs=additional_kwargs)

        return ChatResult(
            generations=[ChatGeneration(message=message)],
            llm_output={
                "model": data.get("model", self.model_name),
                "stop_reason": data.get("stop_reason", ""),
                "usage": usage,
            },
        )

    def bind_tools(self, tools: list, **kwargs) -> "ChatBedrockInvoke":
        """Convert LangChain tools to Anthropic tool format and bind them.

        kwargs 中其它非已知字段会被忽略（pydantic 默认行为），常见的如
        ``tool_choice`` 暂未支持。
        """
        from langchain_core.utils.function_calling import convert_to_openai_tool

        anthropic_tools: List[Dict[str, Any]] = []
        for tool in tools:
            if isinstance(tool, dict):
                # 已是 Anthropic 形态？直接收下；否则按 OpenAI tool schema 转换
                if "input_schema" in tool and "name" in tool:
                    anthropic_tools.append(tool)
                    continue
                if tool.get("type") == "function" and isinstance(tool.get("function"), dict):
                    func = tool["function"]
                    anthropic_tools.append({
                        "name": func["name"],
                        "description": func.get("description", ""),
                        "input_schema": func.get("parameters", {"type": "object", "properties": {}}),
                    })
                    continue
                anthropic_tools.append(tool)
            else:
                oai_tool = convert_to_openai_tool(tool)
                func = oai_tool.get("function", oai_tool)
                anthropic_tools.append({
                    "name": func["name"],
                    "description": func.get("description", ""),
                    "input_schema": func.get("parameters", {"type": "object", "properties": {}}),
                })

        return self.__class__(
            model_name=self.model_name,
            api_key=self.api_key,
            region=self.region,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            top_p=self.top_p,
            timeout=self.timeout,
            thinking=self.thinking,
            bound_tools=anthropic_tools,
        )
