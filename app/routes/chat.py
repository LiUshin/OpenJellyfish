import base64
import json
import asyncio
import logging
import re
import uuid
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from langgraph.types import Command

from app.schemas.requests import ChatRequest, ResumeRequest, StopChatRequest
from app.services.agent import create_user_agent
from app.services.conversations import save_message, save_attachment
from app.services.tools import PLAN_MODE_PROMPT
from app.services.prompt import stamp_message, expand_file_mentions
from app.core.observability import get_langfuse_callbacks, get_langfuse_metadata, flush_langfuse, is_langfuse_enabled
from app.deps import get_current_user

_log = logging.getLogger("chat")
router = APIRouter(tags=["chat"])

_cancel_flags: dict[str, asyncio.Event] = {}
_interrupt_state: dict[str, dict] = {}
_active_streams: dict[str, dict] = {}  # thread_id → {user_id, conv_id}

_MIME_TO_EXT = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
    "image/webp": ".webp", "image/bmp": ".bmp",
}


def _extract_text(message) -> str:
    """Extract plain text from a message (str or multimodal list)."""
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


def _extract_and_save_images(user_id: str, conv_id: str, message) -> list[dict] | None:
    """Extract base64 images from multimodal message and save to query_appendix/.

    Returns list of attachment dicts or None.
    """
    if not isinstance(message, list):
        return None

    attachments = []
    for block in message:
        if not isinstance(block, dict) or block.get("type") != "image_url":
            continue
        url = block.get("image_url", {}).get("url", "")
        if not url.startswith("data:"):
            continue
        try:
            header, b64_data = url.split(",", 1)
            mime = header.split(":")[1].split(";")[0] if ":" in header else "image/png"
            ext = _MIME_TO_EXT.get(mime, ".png")
            raw = base64.b64decode(b64_data)
            filename = f"img_{uuid.uuid4().hex[:8]}{ext}"
            rel = f"images/{filename}"
            save_attachment(user_id, conv_id, rel, raw)
            attachments.append({"type": "image", "filename": filename, "path": rel})
        except Exception:
            _log.warning("Failed to extract/save image attachment")

    return attachments if attachments else None


def _sse_response(generator):
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


async def _stream_agent(agent, agent_input, config, user_id, conv_id, yolo: bool = False):
    """Stream an agent run as SSE events.

    yolo: when True, any HITL interrupt (write_file / edit_file / propose_plan)
    is auto-approved and the agent loop continues without touching the frontend
    ApprovalCard. Mirrors the WeChat admin_bridge auto-approve behaviour.
    """
    thread_id = config["configurable"]["thread_id"]
    prior = _interrupt_state.pop(thread_id, None)
    full_response = prior["full_response"] if prior else ""
    tool_records = prior["tool_records"] if prior else []
    _cur_tool_name = None
    _cur_tool_args = ""
    _is_task_streaming = False
    _sa_id_counter = 0
    _ns_to_sid: dict = {}
    _tcid_to_sid: dict = {}
    _sid_to_blk_idx: dict = {}
    _cur_task_sid = 0
    _cur_task_args_buf = ""

    blocks: list = prior["blocks"] if prior else []

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

    def _blk_find_last_subagent():
        for i in range(len(blocks) - 1, -1, -1):
            b = blocks[i]
            if b["type"] == "subagent" and not b.get("done"):
                return b
        return None

    def _blk_find_subagent(sid=None):
        if sid is not None:
            idx = _sid_to_blk_idx.get(sid)
            if idx is not None and idx < len(blocks):
                return blocks[idx]
        return _blk_find_last_subagent()

    def _sa_tl_append(sa, kind: str, content: str):
        """Append content to the subagent's timeline (chronological ordering)."""
        tl = sa.setdefault("timeline", [])
        if tl and tl[-1]["kind"] == kind:
            tl[-1]["content"] = tl[-1].get("content", "") + content
        else:
            tl.append({"kind": kind, "content": content})

    def _blk_sanitize():
        """Remove internal markers before persisting."""
        for b in blocks:
            b.pop("_closed", None)

    cancel_event = asyncio.Event()
    _cancel_flags[thread_id] = cancel_event
    _active_streams[thread_id] = {"user_id": user_id, "conv_id": conv_id}
    # Notify scheduled-task injection module that this thread is active so any
    # pending L2 injections wait until our stream finishes (drained in `finally`).
    from app.services import scheduled_inject
    await scheduled_inject.mark_thread_active(thread_id)
    _saved = False
    _cancelled = False

    # ── helper: 单轮 agent.astream，作为 SSE generator。被外层 while 循环
    # 反复调用以支持 YOLO 自动批准（每次 interrupt 后 Command(resume=...) 再跑一轮）。
    # 保持原 body 缩进不变（async for 与原 try 同处 8-space 起，body 12-space）。
    async def _drain_one_pass(payload):
        nonlocal full_response, _cur_tool_name, _cur_tool_args, _is_task_streaming
        nonlocal _sa_id_counter, _cur_task_sid, _cur_task_args_buf, _cancelled, _saved
        async for event in agent.astream(payload, config=config, stream_mode="messages", subgraphs=True):
            if cancel_event.is_set():
                _log.info("Agent cancelled by user: %s", thread_id)
                if full_response:
                    _blk_sanitize()
                    save_message(user_id, conv_id, "assistant", full_response + "\n\n⚠️ [用户中止]",
                                 tool_calls=tool_records if tool_records else None,
                                 blocks=blocks if blocks else None)
                _saved = True
                _cancelled = True
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
                return
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
            is_sub = isinstance(ns, tuple) and len(ns) > 0
            agent_name = metadata.get("lc_agent_name", "") if isinstance(metadata, dict) else ""

            cur_sid = None
            if is_sub:
                ns_top = ns[0] if ns else str(ns)
                if ns_top not in _ns_to_sid:
                    matched_sid = None
                    if agent_name:
                        for sid_c in sorted(_sid_to_blk_idx.keys()):
                            blk = blocks[_sid_to_blk_idx[sid_c]]
                            if blk.get("status") == "preparing" and blk.get("name") == agent_name:
                                matched_sid = sid_c
                                break
                    if matched_sid is None:
                        for sid_c in sorted(_sid_to_blk_idx.keys()):
                            blk = blocks[_sid_to_blk_idx[sid_c]]
                            if blk.get("status") == "preparing":
                                matched_sid = sid_c
                                break
                    if matched_sid is not None:
                        blk = blocks[_sid_to_blk_idx[matched_sid]]
                        _ns_to_sid[ns_top] = matched_sid
                        blk["status"] = "running"
                        if agent_name:
                            blk["name"] = agent_name
                        yield f"data: {json.dumps({'type': 'subagent_start', 'name': agent_name, 'subagent_id': matched_sid}, ensure_ascii=False)}\n\n"
                    else:
                        _sa_id_counter += 1
                        dyn_sid = _sa_id_counter
                        _ns_to_sid[ns_top] = dyn_sid
                        blocks.append({"type": "subagent", "name": agent_name, "task": "", "status": "running",
                                       "content": "", "tools": [], "timeline": [], "subagent_id": dyn_sid})
                        _sid_to_blk_idx[dyn_sid] = len(blocks) - 1
                        yield f"data: {json.dumps({'type': 'subagent_call', 'name': agent_name, 'task': '', 'subagent_id': dyn_sid}, ensure_ascii=False)}\n\n"
                        yield f"data: {json.dumps({'type': 'subagent_start', 'name': agent_name, 'subagent_id': dyn_sid}, ensure_ascii=False)}\n\n"
                cur_sid = _ns_to_sid.get(ns_top)

            if msg_type == "AIMessageChunk":
                tool_call_chunks = getattr(msg, "tool_call_chunks", None) or []
                tool_calls = getattr(msg, "tool_calls", None) or []
                for tc in (tool_call_chunks or tool_calls):
                    name = tc.get("name", "")
                    args = tc.get("args", "")
                    if name:
                        _cur_tool_name = name
                        _cur_tool_args = json.dumps(args, ensure_ascii=False) if isinstance(args, dict) else str(args or "")
                        if name == "task" and not is_sub:
                            _is_task_streaming = True
                            _sa_id_counter += 1
                            _cur_task_sid = _sa_id_counter
                            _cur_task_args_buf = ""
                            tc_id = tc.get("id", "")
                            if tc_id:
                                _tcid_to_sid[tc_id] = _cur_task_sid
                            _blk_close_thinking()
                            blocks.append({"type": "subagent", "name": "", "task": "", "status": "preparing",
                                           "content": "", "tools": [], "timeline": [], "subagent_id": _cur_task_sid})
                            _sid_to_blk_idx[_cur_task_sid] = len(blocks) - 1
                            yield f"data: {json.dumps({'type': 'subagent_call', 'name': '', 'task': '', 'subagent_id': _cur_task_sid}, ensure_ascii=False)}\n\n"
                            continue
                        _is_task_streaming = False
                        if not is_sub:
                            _blk_close_thinking()
                            blocks.append({"type": "tool", "name": name, "args": _cur_tool_args, "result": ""})
                        event_type = "subagent_tool_call" if is_sub else "tool_call"
                        sa_extra = {'agent': agent_name, 'subagent_id': cur_sid} if is_sub else {}
                        yield f"data: {json.dumps({'type': event_type, 'name': name, 'args': args if isinstance(args, dict) else {}, **sa_extra}, ensure_ascii=False)}\n\n"
                        if is_sub:
                            sa = _blk_find_subagent(cur_sid)
                            if sa:
                                sa["tools"].append({"name": name, "done": False})
                                sa.setdefault("timeline", []).append({"kind": "tool", "toolName": name, "toolDone": False})
                    elif args:
                        _cur_tool_args += args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
                        if _is_task_streaming and not is_sub:
                            args_str = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
                            _cur_task_args_buf += args_str
                            sa_blk = _blk_find_subagent(_cur_task_sid)
                            if sa_blk and not sa_blk.get("name"):
                                m = re.search(r'"(?:subagent_type|name)"\s*:\s*"([^"]+)"', _cur_task_args_buf)
                                if m:
                                    sa_blk["name"] = m.group(1)
                            yield f"data: {json.dumps({'type': 'subagent_call_chunk', 'args_delta': args}, ensure_ascii=False)}\n\n"
                        else:
                            if not is_sub:
                                tb = _blk_find_last_tool()
                                if tb:
                                    tb["args"] += args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
                            event_type = "subagent_tool_chunk" if is_sub else "tool_call_chunk"
                            yield f"data: {json.dumps({'type': event_type, 'args_delta': args}, ensure_ascii=False)}\n\n"

                content = msg.content
                if isinstance(content, str) and content:
                    if is_sub:
                        yield f"data: {json.dumps({'type': 'subagent_token', 'content': content, 'agent': agent_name, 'subagent_id': cur_sid}, ensure_ascii=False)}\n\n"
                        sa = _blk_find_subagent(cur_sid)
                        if sa:
                            sa["content"] += content
                            _sa_tl_append(sa, "text", content)
                    else:
                        _blk_close_thinking()
                        _blk_ensure_text()
                        blocks[-1]["content"] += content
                        full_response += content
                        yield f"data: {json.dumps({'type': 'token', 'content': content}, ensure_ascii=False)}\n\n"
                elif isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        block_type = block.get("type", "")
                        if block_type == "thinking":
                            thinking_text = block.get("thinking", "")
                            if thinking_text:
                                if not is_sub:
                                    _blk_append_thinking(thinking_text)
                                else:
                                    sa = _blk_find_subagent(cur_sid)
                                    if sa:
                                        sa["content"] += thinking_text
                                        _sa_tl_append(sa, "thinking", thinking_text)
                                event_type = "subagent_thinking" if is_sub else "thinking"
                                sa_extra = {'subagent_id': cur_sid} if is_sub else {}
                                yield f"data: {json.dumps({'type': event_type, 'content': thinking_text, **sa_extra}, ensure_ascii=False)}\n\n"
                        elif block_type == "text":
                            text = block.get("text", "")
                            if text:
                                if is_sub:
                                    yield f"data: {json.dumps({'type': 'subagent_token', 'content': text, 'agent': agent_name, 'subagent_id': cur_sid}, ensure_ascii=False)}\n\n"
                                    sa = _blk_find_subagent(cur_sid)
                                    if sa:
                                        sa["content"] += text
                                        _sa_tl_append(sa, "text", text)
                                else:
                                    _blk_close_thinking()
                                    _blk_ensure_text()
                                    blocks[-1]["content"] += text
                                    full_response += text
                                    yield f"data: {json.dumps({'type': 'token', 'content': text}, ensure_ascii=False)}\n\n"

                additional = getattr(msg, "additional_kwargs", {}) or {}
                reasoning = additional.get("reasoning_content") or additional.get("reasoning", "")
                if isinstance(reasoning, str) and reasoning:
                    if not is_sub:
                        _blk_append_thinking(reasoning)
                    else:
                        sa = _blk_find_subagent(cur_sid)
                        if sa:
                            sa["content"] += reasoning
                            _sa_tl_append(sa, "thinking", reasoning)
                    event_type = "subagent_thinking" if is_sub else "thinking"
                    sa_extra = {'subagent_id': cur_sid} if is_sub else {}
                    yield f"data: {json.dumps({'type': event_type, 'content': reasoning, **sa_extra}, ensure_ascii=False)}\n\n"

            elif msg_type == "ToolMessage":
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                tool_name = getattr(msg, "name", "tool")
                if tool_name == "task" and not is_sub:
                    _is_task_streaming = False
                    tc_id = getattr(msg, "tool_call_id", "")
                    end_sid = _tcid_to_sid.get(tc_id)
                    sa = _blk_find_subagent(end_sid)
                    sa_name = (sa["name"] if sa and sa.get("name") else None) or agent_name or "subagent"
                    yield f"data: {json.dumps({'type': 'subagent_end', 'name': sa_name, 'result': content[:2000], 'subagent_id': end_sid}, ensure_ascii=False)}\n\n"
                    tool_records.append({"name": f"subagent:{sa_name}", "args": _cur_tool_args[:300], "result": content[:500]})
                    if sa:
                        sa["done"] = True
                        sa["status"] = "done"
                        if sa_name:
                            sa["name"] = sa_name
                    _cur_tool_name = None
                    _cur_tool_args = ""
                    continue
                if is_sub:
                    yield f"data: {json.dumps({'type': 'subagent_tool_result', 'name': tool_name, 'content': content, 'agent': agent_name, 'subagent_id': cur_sid}, ensure_ascii=False)}\n\n"
                    sa = _blk_find_subagent(cur_sid)
                    if sa:
                        for t in reversed(sa["tools"]):
                            if t["name"] == tool_name and not t["done"]:
                                t["done"] = True
                                break
                        for e in reversed(sa.get("timeline", [])):
                            if e["kind"] == "tool" and e.get("toolName") == tool_name and not e.get("toolDone"):
                                e["toolDone"] = True
                                break
                else:
                    yield f"data: {json.dumps({'type': 'tool_result', 'name': tool_name, 'content': content}, ensure_ascii=False)}\n\n"
                    tb = _blk_find_last_tool(tool_name)
                    if not tb:
                        tb = _blk_find_last_tool()
                    if tb:
                        tb["done"] = True
                        tb["result"] = content[:500]
                if not is_sub:
                    tool_records.append({"name": tool_name, "args": _cur_tool_args[:300], "result": content[:500]})
                _cur_tool_name = None
                _cur_tool_args = ""
        # ── _drain_one_pass body 结束 ──

    # YOLO 模式：不设硬上限。每达到 YOLO_WARN_EVERY 次循环打一条 warning 日志，
    # 便于在日志里发现 LLM 死循环式不断触发 interrupt 的异常情况。
    YOLO_WARN_EVERY = 50

    try:
        input_payload = agent_input
        yolo_loops = 0
        while True:
            # 1) 流式跑一轮 agent.astream
            async for sse in _drain_one_pass(input_payload):
                yield sse
            if _cancelled:
                _saved = True
                return

            # 2) 检查是否产生 HITL interrupt
            state = await agent.aget_state(config)
            has_interrupt = False
            interrupt_actions_payload: list = []
            interrupt_configs_payload: list = []
            if state and state.tasks:
                for task in state.tasks:
                    if hasattr(task, "interrupts") and task.interrupts:
                        for intr in task.interrupts:
                            val = intr.value if hasattr(intr, "value") else {}
                            if isinstance(val, dict) and "action_requests" in val:
                                has_interrupt = True
                                actions = []
                                for ar in val["action_requests"]:
                                    actions.append({
                                        "name": ar.get("action", {}).get("name", "") if isinstance(ar.get("action"), dict) else ar.get("name", ""),
                                        "args": ar.get("action", {}).get("args", {}) if isinstance(ar.get("action"), dict) else ar.get("args", {}),
                                    })
                                configs = val.get("review_configs", [])
                                interrupt_actions_payload = actions
                                interrupt_configs_payload = [{'action_name': c.get('action_name', ''), 'allowed_decisions': c.get('allowed_decisions', [])} for c in configs]

            # 3) 没有 interrupt → 正常结束
            if not has_interrupt:
                break

            # 4) 有 interrupt 且非 YOLO → 走原 ApprovalCard 流程
            if not yolo:
                yield f"data: {json.dumps({'type': 'interrupt', 'actions': interrupt_actions_payload, 'configs': interrupt_configs_payload}, ensure_ascii=False)}\n\n"
                _interrupt_state[thread_id] = {
                    "full_response": full_response,
                    "blocks": list(blocks),
                    "tool_records": list(tool_records),
                    "interrupt_actions": interrupt_actions_payload,
                    "interrupt_configs": interrupt_configs_payload,
                }
                _saved = True
                return

            # 5) YOLO 模式：自动批准并继续（无硬上限；定期打 warning 便于排查死循环）
            yolo_loops += 1
            if yolo_loops % YOLO_WARN_EVERY == 0:
                _log.warning(
                    "YOLO auto-approve has looped %d times for %s; check for runaway HITL loop",
                    yolo_loops, thread_id,
                )

            decisions = [{"type": "approve"} for _ in interrupt_actions_payload]
            actions_summary = [{"name": a.get("name", ""), "args": a.get("args", {})} for a in interrupt_actions_payload]
            # 注意：不再向 blocks 追加 auto_approve（避免历史消息出现显眼徽章）；
            # 仅通过 SSE 通知前端「本会话发生过 YOLO 自动批准」，由前端在输入区底部显示一个小 tag。
            yield f"data: {json.dumps({'type': 'auto_approve', 'count': len(decisions), 'actions': actions_summary}, ensure_ascii=False)}\n\n"
            _log.info("YOLO auto-approving %d HITL action(s) for %s (loop %d)", len(decisions), thread_id, yolo_loops)
            input_payload = Command(resume={"decisions": decisions})
            # while loop 继续

        # while 正常退出（has_interrupt=False）→ 存盘 + done
        _interrupt_state.pop(thread_id, None)
        if full_response:
            _blk_sanitize()
            save_message(user_id, conv_id, "assistant", full_response,
                         tool_calls=tool_records if tool_records else None,
                         blocks=blocks if blocks else None)
        _saved = True
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    except Exception as e:
        _log.exception("Agent error for %s: %s", thread_id, e)
        _interrupt_state.pop(thread_id, None)
        if full_response:
            _blk_sanitize()
            save_message(user_id, conv_id, "assistant", full_response + f"\n\n❌ 错误: {e}",
                         tool_calls=tool_records if tool_records else None,
                         blocks=blocks if blocks else None)
        _saved = True
        yield f"data: {json.dumps({'type': 'error', 'content': f'Agent 错误: {str(e)}'}, ensure_ascii=False)}\n\n"
    finally:
        if not _saved and full_response:
            _log.info("Connection lost for %s, saving partial response", thread_id)
            _blk_sanitize()
            save_message(user_id, conv_id, "assistant",
                         full_response + "\n\n⚠️ [连接中断 — 已保存已生成内容]",
                         tool_calls=tool_records if tool_records else None,
                         blocks=blocks if blocks else None)
        _cancel_flags.pop(thread_id, None)
        _active_streams.pop(thread_id, None)
        # Release scheduled-injection guard; if any L2 pairs were queued during
        # this stream, the drainer will pick them up now (subject to settle delay).
        await scheduled_inject.mark_thread_inactive(thread_id)
        if is_langfuse_enabled():
            flush_langfuse()


@router.post("/api/chat")
async def api_chat(req: ChatRequest, user=Depends(get_current_user)):
    user_id = user["user_id"]
    username = user.get("username", user_id)
    conv_id = req.conversation_id

    # Expand `[[FILE:/path]]` chips inserted by the @-mention picker into the
    # agent-facing `<<FILE:/path>>` notation BEFORE persistence and stamping,
    # so the conversation log, the agent input, and any future re-render of
    # historical user bubbles all see the same canonical form (which the
    # markdown.ts pipeline already knows how to render as a clickable file
    # pill or inline media preview).
    canonical_message = expand_file_mentions(req.message)

    save_text = _extract_text(canonical_message)
    attachments = _extract_and_save_images(user_id, conv_id, canonical_message)
    save_message(user_id, conv_id, "user", save_text, attachments=attachments)

    agent = create_user_agent(user_id, model=req.model, capabilities=req.capabilities, username=username)
    thread_id = f"{user_id}-{conv_id}"
    config = {
        "configurable": {"thread_id": thread_id},
        "callbacks": get_langfuse_callbacks(),
        "metadata": get_langfuse_metadata(session_id=thread_id, user_id=username),
    }
    user_content = canonical_message
    if req.plan_mode:
        if isinstance(user_content, str):
            user_content = PLAN_MODE_PROMPT + "\n\n" + user_content
        elif isinstance(user_content, list):
            user_content = [{"type": "text", "text": PLAN_MODE_PROMPT + "\n\n"}] + user_content
    user_content = stamp_message(user_content, user_id)
    return _sse_response(_stream_agent(agent, {"messages": [{"role": "user", "content": user_content}]}, config, user_id, conv_id, yolo=bool(req.yolo)))


@router.post("/api/chat/stop")
async def api_chat_stop(req: StopChatRequest, user=Depends(get_current_user)):
    user_id = user["user_id"]
    thread_id = f"{user_id}-{req.conversation_id}"
    cancel_event = _cancel_flags.get(thread_id)
    if cancel_event:
        cancel_event.set()
        _log.info("Stop requested for %s", thread_id)
        return {"status": "stopping"}
    return {"status": "not_running"}


@router.get("/api/chat/streaming-status")
async def api_chat_streaming_status(user=Depends(get_current_user)):
    """Return list of conversation IDs that are currently streaming or have pending HITL."""
    user_id = user["user_id"]
    prefix = f"{user_id}-"
    active = []
    for tid, info in _active_streams.items():
        if info["user_id"] == user_id:
            active.append(info["conv_id"])
    interrupted = []
    for tid in _interrupt_state:
        if tid.startswith(prefix):
            interrupted.append(tid[len(prefix):])
    return {"streaming": active, "interrupted": interrupted}


@router.get("/api/chat/interrupt/{conv_id}")
async def api_chat_interrupt(conv_id: str, user=Depends(get_current_user)):
    """Return pending HITL interrupt state for a conversation (if any)."""
    user_id = user["user_id"]
    thread_id = f"{user_id}-{conv_id}"
    state = _interrupt_state.get(thread_id)
    if not state:
        return {"has_interrupt": False}
    return {
        "has_interrupt": True,
        "actions": state.get("interrupt_actions", []),
        "configs": state.get("interrupt_configs", []),
    }


@router.post("/api/chat/resume")
async def api_chat_resume(req: ResumeRequest, user=Depends(get_current_user)):
    user_id = user["user_id"]
    username = user.get("username", user_id)
    conv_id = req.conversation_id

    agent = create_user_agent(user_id, model=req.model, capabilities=req.capabilities, username=username)
    thread_id = f"{user_id}-{conv_id}"
    config = {
        "configurable": {"thread_id": thread_id},
        "callbacks": get_langfuse_callbacks(),
        "metadata": get_langfuse_metadata(session_id=thread_id, user_id=username),
    }
    return _sse_response(_stream_agent(agent, Command(resume={"decisions": req.decisions}), config, user_id, conv_id, yolo=bool(req.yolo)))
