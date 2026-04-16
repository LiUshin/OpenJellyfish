"""
Consumer-facing API routes.

Two chat interfaces:
  POST /api/v1/chat              — custom SSE (same event format as admin /api/chat)
  POST /api/v1/chat/completions  — OpenAI-compatible streaming / non-streaming

Plus conversation + file management.
"""

import json
import os
import time
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse, FileResponse

from app.core.path_security import ensure_within
from app.services.prompt import stamp_message


def _extract_text(message) -> str:
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        parts = []
        for block in message:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "image_url":
                    parts.append("[图片]")
        return "\n".join(parts) if parts else "[多模态消息]"
    return str(message)

from app.deps import get_service_context
from app.schemas.service import (
    ConsumerChatRequest,
    ConsumerCompletionsRequest,
    CreateConsumerConversationRequest,
)
from app.services.published import (
    create_consumer_conversation,
    get_consumer_conversation,
    save_consumer_message,
    get_consumer_generated_dir,
)
from app.services.consumer_agent import create_consumer_agent

router = APIRouter(prefix="/api/v1", tags=["consumer"])


# ── helpers ──────────────────────────────────────────────────────────

def _sse(gen):
    return StreamingResponse(
        gen,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


async def _stream_consumer(agent, agent_input, config, ctx, conv_id):
    """Yield SSE events identical to the admin _stream_agent format."""
    admin_id = ctx["admin_id"]
    service_id = ctx["service_id"]
    full_response = ""
    tool_records = []
    _cur_tool_name = None
    _cur_tool_args = ""

    blocks: list = []
    _saved = False

    def _blk_last():
        return blocks[-1] if blocks else None

    def _blk_close_thinking():
        last = _blk_last()
        if last and last["type"] == "thinking":
            last["_closed"] = True

    def _blk_ensure_text():
        last = _blk_last()
        if not last or last["type"] != "text":
            blocks.append({"type": "text", "content": ""})

    def _blk_append_thinking(text):
        last = _blk_last()
        if last and last["type"] == "thinking" and not last.get("_closed"):
            last["content"] += text
        else:
            blocks.append({"type": "thinking", "content": text})

    def _blk_find_last_tool(name=None):
        for i in range(len(blocks) - 1, -1, -1):
            b = blocks[i]
            if b["type"] == "tool" and not b.get("done"):
                if name is None or b["name"] == name:
                    return b
        return None

    def _blk_sanitize():
        for b in blocks:
            b.pop("_closed", None)

    try:
        async for event in agent.astream(agent_input, config=config, stream_mode="messages", subgraphs=True):
            if not isinstance(event, tuple) or len(event) != 2:
                continue
            ns, chunk = event
            if isinstance(chunk, tuple) and len(chunk) == 2:
                msg, metadata = chunk
            elif hasattr(ns, '__class__') and 'Message' in ns.__class__.__name__:
                msg, metadata = ns, chunk
                ns = ()
            else:
                continue

            msg_type = msg.__class__.__name__

            if msg_type == "AIMessageChunk":
                tool_call_chunks = getattr(msg, "tool_call_chunks", None) or []
                tool_calls = getattr(msg, "tool_calls", None) or []
                for tc in (tool_call_chunks or tool_calls):
                    name = tc.get("name", "")
                    args = tc.get("args", "")
                    if name:
                        _cur_tool_name = name
                        _cur_tool_args = json.dumps(args, ensure_ascii=False) if isinstance(args, dict) else str(args or "")
                        _blk_close_thinking()
                        blocks.append({"type": "tool", "name": name, "args": _cur_tool_args, "result": ""})
                        yield f"data: {json.dumps({'type': 'tool_call', 'name': name, 'args': args if isinstance(args, dict) else {}}, ensure_ascii=False)}\n\n"
                    elif args:
                        _cur_tool_args += args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
                        tb = _blk_find_last_tool()
                        if tb:
                            tb["args"] += args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
                        yield f"data: {json.dumps({'type': 'tool_call_chunk', 'args_delta': args}, ensure_ascii=False)}\n\n"

                content = msg.content
                if isinstance(content, str) and content:
                    _blk_close_thinking()
                    _blk_ensure_text()
                    blocks[-1]["content"] += content
                    full_response += content
                    yield f"data: {json.dumps({'type': 'token', 'content': content}, ensure_ascii=False)}\n\n"
                elif isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        btype = block.get("type", "")
                        if btype == "thinking":
                            text = block.get("thinking", "")
                            if text:
                                _blk_append_thinking(text)
                                yield f"data: {json.dumps({'type': 'thinking', 'content': text}, ensure_ascii=False)}\n\n"
                        elif btype == "text":
                            text = block.get("text", "")
                            if text:
                                _blk_close_thinking()
                                _blk_ensure_text()
                                blocks[-1]["content"] += text
                                full_response += text
                                yield f"data: {json.dumps({'type': 'token', 'content': text}, ensure_ascii=False)}\n\n"

                additional = getattr(msg, "additional_kwargs", {}) or {}
                reasoning = additional.get("reasoning_content") or additional.get("reasoning", "")
                if isinstance(reasoning, str) and reasoning:
                    _blk_append_thinking(reasoning)
                    yield f"data: {json.dumps({'type': 'thinking', 'content': reasoning}, ensure_ascii=False)}\n\n"

            elif msg_type == "ToolMessage":
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                tool_name = getattr(msg, "name", "tool")
                yield f"data: {json.dumps({'type': 'tool_result', 'name': tool_name, 'content': content}, ensure_ascii=False)}\n\n"
                tb = _blk_find_last_tool(tool_name)
                if not tb:
                    tb = _blk_find_last_tool()
                if tb:
                    tb["done"] = True
                    tb["result"] = content[:500]
                tool_records.append({"name": tool_name, "args": _cur_tool_args[:300], "result": content[:500]})
                _cur_tool_name = None
                _cur_tool_args = ""

        if full_response:
            _blk_sanitize()
            save_consumer_message(admin_id, service_id, conv_id, "assistant", full_response,
                                  tool_calls=tool_records if tool_records else None,
                                  blocks=blocks if blocks else None)
        _saved = True
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    except Exception as e:
        if full_response:
            _blk_sanitize()
            save_consumer_message(admin_id, service_id, conv_id, "assistant",
                                  full_response + f"\n\n❌ 错误: {e}",
                                  tool_calls=tool_records if tool_records else None,
                                  blocks=blocks if blocks else None)
        _saved = True
        yield f"data: {json.dumps({'type': 'error', 'content': str(e)}, ensure_ascii=False)}\n\n"
    finally:
        if not _saved and full_response:
            _blk_sanitize()
            save_consumer_message(admin_id, service_id, conv_id, "assistant",
                                  full_response + "\n\n⚠️ [连接中断 — 已保存已生成内容]",
                                  tool_calls=tool_records if tool_records else None,
                                  blocks=blocks if blocks else None)


async def _stream_openai_compat(agent, agent_input, config, ctx, conv_id, model_name):
    """Yield SSE in OpenAI chat.completions.chunk format."""
    admin_id = ctx["admin_id"]
    service_id = ctx["service_id"]
    completion_id = "chatcmpl-" + uuid.uuid4().hex[:12]
    created = int(time.time())
    full_response = ""
    _saved = False

    def _chunk(delta, finish_reason=None):
        return "data: " + json.dumps({
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model_name,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }, ensure_ascii=False) + "\n\n"

    try:
        yield _chunk({"role": "assistant"})

        async for event in agent.astream(agent_input, config=config, stream_mode="messages", subgraphs=True):
            if not isinstance(event, tuple) or len(event) != 2:
                continue
            ns, chunk = event
            if isinstance(chunk, tuple) and len(chunk) == 2:
                msg, metadata = chunk
            elif hasattr(ns, '__class__') and 'Message' in ns.__class__.__name__:
                msg, metadata = ns, chunk
                ns = ()
            else:
                continue

            msg_type = msg.__class__.__name__
            if msg_type == "AIMessageChunk":
                content = msg.content
                if isinstance(content, str) and content:
                    full_response += content
                    yield _chunk({"content": content})
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = block.get("text", "")
                            if text:
                                full_response += text
                                yield _chunk({"content": text})

        if full_response:
            save_consumer_message(admin_id, service_id, conv_id, "assistant", full_response)
        _saved = True
        yield _chunk({}, finish_reason="stop")
        yield "data: [DONE]\n\n"

    except Exception as e:
        if full_response:
            save_consumer_message(admin_id, service_id, conv_id, "assistant",
                                  full_response + f"\n\n❌ 错误: {e}")
        _saved = True
        yield _chunk({"content": f"\n\n[Error: {e}]"})
        yield _chunk({}, finish_reason="stop")
        yield "data: [DONE]\n\n"
    finally:
        if not _saved and full_response:
            save_consumer_message(admin_id, service_id, conv_id, "assistant",
                                  full_response + "\n\n⚠️ [连接中断 — 已保存已生成内容]")


# ── routes ───────────────────────────────────────────────────────────

@router.post("/conversations")
async def api_create_conversation(
    req: CreateConsumerConversationRequest,
    ctx=Depends(get_service_context),
):
    conv = create_consumer_conversation(ctx["admin_id"], ctx["service_id"], req.title)
    return conv


@router.get("/conversations/{conv_id}")
async def api_get_conversation(conv_id: str, ctx=Depends(get_service_context)):
    conv = get_consumer_conversation(ctx["admin_id"], ctx["service_id"], conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


@router.post("/chat")
async def api_consumer_chat(req: ConsumerChatRequest, ctx=Depends(get_service_context)):
    admin_id = ctx["admin_id"]
    service_id = ctx["service_id"]
    conv_id = req.conversation_id

    conv = get_consumer_conversation(admin_id, service_id, conv_id)
    if not conv:
        conv = create_consumer_conversation(admin_id, service_id)
        conv_id = conv["id"]

    save_text = _extract_text(req.message)
    save_consumer_message(admin_id, service_id, conv_id, "user", save_text)
    agent = create_consumer_agent(admin_id, service_id, conv_id)
    thread_id = f"svc-{service_id}-{conv_id}"
    config = {"configurable": {"thread_id": thread_id}}

    stamped = stamp_message(req.message, admin_id)
    return _sse(_stream_consumer(
        agent,
        {"messages": [{"role": "user", "content": stamped}]},
        config, ctx, conv_id,
    ))


@router.post("/chat/completions")
async def api_consumer_completions(req: ConsumerCompletionsRequest, ctx=Depends(get_service_context)):
    admin_id = ctx["admin_id"]
    service_id = ctx["service_id"]
    svc_config = ctx.get("service_config", {})
    model_name = svc_config.get("model", "unknown")

    conv_id = req.conversation_id
    if not conv_id:
        conv = create_consumer_conversation(admin_id, service_id)
        conv_id = conv["id"]
    else:
        conv = get_consumer_conversation(admin_id, service_id, conv_id)
        if not conv:
            conv = create_consumer_conversation(admin_id, service_id)
            conv_id = conv["id"]

    last_user_msg = ""
    if req.messages:
        for m in reversed(req.messages):
            if m.get("role") == "user":
                last_user_msg = m.get("content", "")
                break

    if not last_user_msg:
        raise HTTPException(status_code=400, detail="No user message found")

    save_consumer_message(admin_id, service_id, conv_id, "user", last_user_msg)
    agent = create_consumer_agent(admin_id, service_id, conv_id)
    thread_id = f"svc-{service_id}-{conv_id}"
    config = {"configurable": {"thread_id": thread_id}}

    stamped = stamp_message(last_user_msg, admin_id)
    if req.stream:
        return _sse(_stream_openai_compat(
            agent,
            {"messages": [{"role": "user", "content": stamped}]},
            config, ctx, conv_id, model_name,
        ))

    # Non-streaming: collect full response
    full_response = ""
    async for event in agent.astream(
        {"messages": [{"role": "user", "content": stamped}]},
        config=config, stream_mode="messages", subgraphs=True,
    ):
        if not isinstance(event, tuple) or len(event) != 2:
            continue
        ns, chunk = event
        if isinstance(chunk, tuple) and len(chunk) == 2:
            msg, _ = chunk
        elif hasattr(ns, '__class__') and 'Message' in ns.__class__.__name__:
            msg = ns
        else:
            continue
        if msg.__class__.__name__ == "AIMessageChunk":
            content = msg.content
            if isinstance(content, str):
                full_response += content
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        full_response += block.get("text", "")

    if full_response:
        save_consumer_message(admin_id, service_id, conv_id, "assistant", full_response)

    return {
        "id": "chatcmpl-" + uuid.uuid4().hex[:12],
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_name,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": full_response},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "conversation_id": conv_id,
    }


@router.get("/conversations/{conv_id}/files")
async def api_list_generated_files(conv_id: str, ctx=Depends(get_service_context)):
    from app.storage import get_storage_service
    storage = get_storage_service()
    return storage.list_consumer_files(ctx["admin_id"], ctx["service_id"], conv_id)


@router.get("/conversations/{conv_id}/files/{file_path:path}")
async def api_get_generated_file(conv_id: str, file_path: str, ctx=Depends(get_service_context)):
    from app.storage import get_storage_service
    storage = get_storage_service()
    return storage.consumer_file_response(
        ctx["admin_id"], ctx["service_id"], conv_id, file_path,
    )


@router.get("/conversations/{conv_id}/attachments/{file_path:path}")
async def api_get_consumer_attachment(conv_id: str, file_path: str,
                                      ctx=Depends(get_service_context)):
    import os
    from fastapi.responses import FileResponse
    from app.services.published import get_consumer_attachment_dir
    from app.core.path_security import safe_join

    att_dir = get_consumer_attachment_dir(ctx["admin_id"], ctx["service_id"], conv_id)
    try:
        full = safe_join(att_dir, file_path)
    except (ValueError, FileNotFoundError):
        raise HTTPException(status_code=404, detail="文件不存在")
    if not os.path.isfile(full):
        raise HTTPException(status_code=404, detail="文件不存在")
    ext = os.path.splitext(file_path)[1].lower()
    mime_map = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    }
    media_type = mime_map.get(ext, "application/octet-stream")
    return FileResponse(full, media_type=media_type,
                        headers={"Content-Disposition": "inline"})
