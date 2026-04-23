"""
S2S Voice — OpenAI Realtime API WebSocket proxy with tool execution.
"""

import os
import json
import asyncio
import traceback
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from app.core.security import get_user_voice_transcripts_dir, get_user_filesystem_dir
from app.storage import create_agent_backend

router = APIRouter(prefix="/api/voice", tags=["voice"])

TRANSCRIPT_MAX_TURNS = 20
TRANSCRIPT_MAX_CHARS = 2000


# ── Transcript persistence ──────────────────────────────────────────

def _save_transcript(user_id: str, session_id: str, started_at: str, turns: List[dict]):
    if not turns:
        return
    transcript_dir = get_user_voice_transcripts_dir(user_id)
    os.makedirs(transcript_dir, exist_ok=True)
    doc = {
        "session_id": session_id,
        "user_id": user_id,
        "started_at": started_at,
        "ended_at": datetime.now().isoformat(),
        "turns": turns,
    }
    filepath = os.path.join(transcript_dir, f"{session_id}.json")
    from app.core.fileutil import atomic_json_save
    atomic_json_save(filepath, doc, ensure_ascii=False, indent=2)
    print(f"[S2S] Saved transcript ({len(turns)} turns) → {filepath}")


def _load_latest_transcript(user_id: str) -> Optional[List[dict]]:
    transcript_dir = get_user_voice_transcripts_dir(user_id)
    if not os.path.isdir(transcript_dir):
        return None
    files = [f for f in os.listdir(transcript_dir) if f.endswith(".json")]
    if not files:
        return None
    files.sort(key=lambda f: os.path.getmtime(os.path.join(transcript_dir, f)), reverse=True)
    try:
        with open(os.path.join(transcript_dir, files[0]), "r", encoding="utf-8") as f:
            doc = json.load(f)
        return doc.get("turns", [])
    except Exception as e:
        print(f"[S2S] Failed to load transcript: {e}")
        return None


def _build_transcript_context(turns: List[dict]) -> str:
    recent = turns[-TRANSCRIPT_MAX_TURNS:]
    lines = []
    total_chars = 0
    for turn in reversed(recent):
        role = turn.get("role", "?")
        if role == "tool":
            line = f"[Tool: {turn.get('tool_name', '?')}] {turn.get('result', '')[:200]}"
        else:
            line = f"{role.capitalize()}: {turn.get('content', '')}"
        if total_chars + len(line) > TRANSCRIPT_MAX_CHARS:
            break
        lines.insert(0, line)
        total_chars += len(line)
    if not lines:
        return ""
    return "\n--- Previous voice session ---\n" + "\n".join(lines) + "\n---\n"


# ── Tool definitions ────────────────────────────────────────────────

def _build_fs_tool_defs() -> List[dict]:
    return [
        {"type": "function", "name": "ls", "description": "List directory contents. Use '/' for root.",
         "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
        {"type": "function", "name": "read_file", "description": "Read file contents.",
         "parameters": {"type": "object", "properties": {
             "file_path": {"type": "string"},
             "offset": {"type": "integer"},
             "limit": {"type": "integer"},
         }, "required": ["file_path"]}},
        {"type": "function", "name": "write_file", "description": "Create or overwrite a file.",
         "parameters": {"type": "object", "properties": {
             "file_path": {"type": "string"},
             "content": {"type": "string"},
         }, "required": ["file_path", "content"]}},
        {"type": "function", "name": "edit_file", "description": "Edit a file by replacing old_string with new_string.",
         "parameters": {"type": "object", "properties": {
             "file_path": {"type": "string"},
             "old_string": {"type": "string"},
             "new_string": {"type": "string"},
         }, "required": ["file_path", "old_string", "new_string"]}},
        {"type": "function", "name": "glob", "description": "Search for files by glob pattern.",
         "parameters": {"type": "object", "properties": {
             "pattern": {"type": "string"},
             "path": {"type": "string"},
         }, "required": ["pattern"]}},
        {"type": "function", "name": "grep", "description": "Search file contents for a text pattern.",
         "parameters": {"type": "object", "properties": {
             "pattern": {"type": "string"},
             "path": {"type": "string"},
             "file_glob": {"type": "string"},
         }, "required": ["pattern"]}},
    ]


def _build_custom_tool_defs(langchain_tools: list) -> List[dict]:
    defs = []
    for t in langchain_tools:
        schema = t.args_schema.schema() if t.args_schema else {"type": "object", "properties": {}}
        params: Dict[str, Any] = {"type": "object", "properties": {}, "required": schema.get("required", [])}
        for prop_name, prop_def in schema.get("properties", {}).items():
            clean = {k: v for k, v in prop_def.items() if k in ("type", "description", "default", "enum", "items")}
            if "anyOf" in prop_def:
                for variant in prop_def["anyOf"]:
                    if variant.get("type") != "null":
                        clean = {k: v for k, v in variant.items() if k in ("type", "description", "default", "enum", "items")}
                        break
            if "type" not in clean:
                clean["type"] = "string"
            params["properties"][prop_name] = clean
        defs.append({"type": "function", "name": t.name, "description": t.description or t.name, "parameters": params})
    return defs


class S2SToolExecutor:
    def __init__(self, user_id: str, langchain_tools: list):
        fs_dir = get_user_filesystem_dir(user_id)
        os.makedirs(os.path.join(fs_dir, "docs"), exist_ok=True)
        os.makedirs(os.path.join(fs_dir, "scripts"), exist_ok=True)
        self._backend = create_agent_backend(root_dir=fs_dir, user_id=user_id)
        self._custom_tools = langchain_tools
        self._custom_tool_map = {t.name: t for t in langchain_tools}

    def get_tool_defs(self) -> List[dict]:
        return _build_fs_tool_defs() + _build_custom_tool_defs(self._custom_tools)

    def execute(self, tool_name: str, arguments_json: str) -> str:
        try:
            args = json.loads(arguments_json) if arguments_json else {}
        except json.JSONDecodeError:
            return f"Invalid arguments JSON: {arguments_json[:200]}"
        try:
            if tool_name == "ls":
                entries = self._backend.ls_info(args.get("path", "/"))
                lines = []
                for e in entries:
                    suffix = "/" if e.get("is_dir") else ""
                    size_str = f"  ({e.get('size', 0)} bytes)" if not e.get("is_dir") else ""
                    lines.append(f"  {e.get('path', '?')}{suffix}{size_str}")
                return "\n".join(lines) if lines else "(empty directory)"
            elif tool_name == "read_file":
                return self._backend.read(args["file_path"], offset=args.get("offset", 0), limit=args.get("limit", 2000))
            elif tool_name == "write_file":
                result = self._backend.write(args["file_path"], args["content"])
                return f"Write failed: {result.error}" if result.error else f"Written to {result.path}"
            elif tool_name == "edit_file":
                result = self._backend.edit(args["file_path"], args["old_string"], args["new_string"], replace_all=args.get("replace_all", False))
                return f"Edit failed: {result.error}" if result.error else f"Edited {result.path} ({result.occurrences} occurrence(s))"
            elif tool_name == "glob":
                entries = self._backend.glob_info(args["pattern"], path=args.get("path", "/"))
                return "\n".join(e.get("path", "?") for e in entries) if entries else "(no matches)"
            elif tool_name == "grep":
                matches = self._backend.grep_raw(args["pattern"], path=args.get("path"), glob=args.get("file_glob"))
                if isinstance(matches, str):
                    return matches
                lines = [f"{m.get('path')}:{m.get('line')}: {m.get('text')}" for m in matches[:50]]
                return "\n".join(lines) if lines else "(no matches)"
            elif tool_name in self._custom_tool_map:
                return self._custom_tool_map[tool_name].invoke(args)
            else:
                return f"Unknown tool: {tool_name}"
        except Exception as e:
            return f"Tool '{tool_name}' error: {type(e).__name__}: {e}"


@router.websocket("/realtime")
async def voice_realtime(websocket: WebSocket, token: str = Query("")):
    import websockets
    from app.core.security import verify_token
    from app.services.tools import create_s2s_tools

    user = verify_token(token)
    if not user:
        await websocket.close(code=4001, reason="Invalid token")
        return

    user_id = user.get("user_id", user.get("username", "unknown"))

    from app.core.api_config import get_api_config
    try:
        openai_key, s2s_base = get_api_config("s2s", user_id=user_id)
    except RuntimeError as e:
        await websocket.close(code=4002, reason=str(e))
        return
    await websocket.accept()
    print(f"[S2S] WebSocket accepted for user={user_id}")

    langchain_tools = create_s2s_tools(user_id)
    executor = S2SToolExecutor(user_id, langchain_tools)
    tool_defs = executor.get_tool_defs()
    print(f"[S2S] Registered {len(tool_defs)} tools: {[t['name'] for t in tool_defs]}")

    _session_id = str(uuid.uuid4())
    _session_started = datetime.now().isoformat()
    _transcript_turns: List[dict] = []
    _prev_context = _load_latest_transcript(user_id)
    _prev_context_text = _build_transcript_context(_prev_context) if _prev_context else ""

    # Realtime 模型从 catalog 读取，不再硬编码；保持 provider:model 形式。
    from app.services.model_catalog import resolve_model
    s2s_model_id = resolve_model("s2s", user_id=user_id) or "openai:gpt-4o-realtime-preview"
    s2s_model = s2s_model_id.split(":", 1)[-1]

    ws_base = s2s_base.replace("https://", "wss://").replace("http://", "ws://")
    openai_url = f"{ws_base}/realtime?model={s2s_model}"

    try:
        async with websockets.connect(
            openai_url,
            additional_headers={"Authorization": f"Bearer {openai_key}", "OpenAI-Beta": "realtime=v1"},
            open_timeout=15,
            ping_interval=20,
            ping_timeout=300,
        ) as openai_ws:
            print("[S2S] Connected to OpenAI Realtime API")
            _audio_chunks_sent = 0

            async def forward_client_to_openai():
                nonlocal _audio_chunks_sent
                try:
                    while True:
                        raw = await websocket.receive_text()
                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            await openai_ws.send(raw)
                            continue
                        msg_type = msg.get("type", "")
                        if msg_type == "session.update":
                            session = msg.setdefault("session", {})
                            session["tools"] = tool_defs
                            session["tool_choice"] = "auto"
                            mods = session.get("modalities", [])
                            if "text" not in mods:
                                session["modalities"] = ["text", "audio"]
                            if _prev_context_text:
                                session["instructions"] = _prev_context_text + session.get("instructions", "")
                            await openai_ws.send(json.dumps(msg))
                        elif msg_type == "input_audio_buffer.append":
                            _audio_chunks_sent += 1
                            await openai_ws.send(raw)
                        else:
                            await openai_ws.send(raw)
                except WebSocketDisconnect:
                    print("[S2S] Browser disconnected")
                except Exception as exc:
                    print(f"[S2S] Client→OpenAI error: {type(exc).__name__}: {exc}")

            _audio_deltas_recv = 0
            _transcript_buf = ""

            async def forward_openai_to_client():
                nonlocal _audio_deltas_recv, _transcript_buf, _transcript_turns
                try:
                    async for raw_message in openai_ws:
                        if not isinstance(raw_message, str):
                            try:
                                await websocket.send_bytes(raw_message)
                            except WebSocketDisconnect:
                                break
                            continue

                        if '"response.audio.delta"' in raw_message:
                            _audio_deltas_recv += 1
                            try:
                                await websocket.send_text(raw_message)
                            except WebSocketDisconnect:
                                break
                            continue

                        try:
                            msg = json.loads(raw_message)
                        except json.JSONDecodeError:
                            await websocket.send_text(raw_message)
                            continue

                        msg_type = msg.get("type", "")

                        if msg_type == "response.audio_transcript.delta":
                            _transcript_buf += msg.get("delta", "")
                        elif msg_type == "response.audio_transcript.done":
                            if _transcript_buf.strip():
                                _transcript_turns.append({"role": "assistant", "content": _transcript_buf, "timestamp": datetime.now().isoformat()})
                            _transcript_buf = ""
                        elif msg_type == "conversation.item.input_audio_transcription.completed":
                            user_text = msg.get("transcript", "").strip()
                            if user_text:
                                _transcript_turns.append({"role": "user", "content": user_text, "timestamp": datetime.now().isoformat()})

                        if msg_type == "response.output_item.done":
                            item = msg.get("item", {})
                            if item.get("type") == "function_call":
                                call_id = item.get("call_id", "")
                                fn_name = item.get("name", "")
                                fn_args = item.get("arguments", "{}")
                                print(f"[S2S] Function call: {fn_name}({fn_args[:100]})")
                                try:
                                    await websocket.send_text(json.dumps({"type": "s2s.tool_call", "tool_name": fn_name, "status": "running"}))
                                except WebSocketDisconnect:
                                    break
                                loop = asyncio.get_event_loop()
                                tool_future = loop.run_in_executor(None, executor.execute, fn_name, fn_args)

                                async def _keepalive():
                                    elapsed = 0
                                    while not tool_future.done():
                                        await asyncio.sleep(5)
                                        elapsed += 5
                                        try:
                                            await websocket.send_text(json.dumps({"type": "s2s.tool_call", "tool_name": fn_name, "status": "running", "elapsed": elapsed}))
                                        except Exception:
                                            break

                                keepalive_task = asyncio.create_task(_keepalive())
                                try:
                                    result = await tool_future
                                finally:
                                    keepalive_task.cancel()
                                    try:
                                        await keepalive_task
                                    except asyncio.CancelledError:
                                        pass

                                if len(result) > 4000:
                                    result = result[:4000] + "\n... (truncated)"
                                _transcript_turns.append({"role": "tool", "tool_name": fn_name, "result": result[:500], "timestamp": datetime.now().isoformat()})
                                await openai_ws.send(json.dumps({"type": "conversation.item.create", "item": {"type": "function_call_output", "call_id": call_id, "output": result}}))
                                await openai_ws.send(json.dumps({"type": "response.create"}))
                                try:
                                    await websocket.send_text(json.dumps({"type": "s2s.tool_call", "tool_name": fn_name, "status": "done", "result_preview": result[:2000]}))
                                except WebSocketDisconnect:
                                    break
                                continue

                        try:
                            await websocket.send_text(raw_message)
                        except WebSocketDisconnect:
                            break
                except Exception as exc:
                    print(f"[S2S] OpenAI→Client error: {type(exc).__name__}: {exc}")

            done, pending = await asyncio.wait(
                [asyncio.create_task(forward_client_to_openai()), asyncio.create_task(forward_openai_to_client())],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    except WebSocketDisconnect:
        print("[S2S] Browser disconnected before proxy started")
    except Exception as e:
        err_msg = f"Realtime connection failed: {type(e).__name__}: {e}"
        print(f"[S2S] ERROR: {err_msg}")
        traceback.print_exc()
        try:
            await websocket.send_text(json.dumps({"type": "error", "error": {"message": err_msg}}))
        except Exception:
            pass
    finally:
        if _transcript_turns:
            _save_transcript(user_id, _session_id, _session_started, _transcript_turns)
        try:
            await websocket.close()
        except Exception:
            pass
        print("[S2S] WebSocket closed")
