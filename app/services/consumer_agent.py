"""
Consumer agent factory — creates per-conversation agents for published services.

Key differences from admin agent:
- Filesystem: admin's docs (read-only), conversation-specific generated/ (writable)
- Tools: filtered by service config (allowed_scripts, capabilities)
- No HITL interrupts on writes
- Memory subagent: read-only access to own conversation history only
"""

import os
import json
import logging
from collections import OrderedDict
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Any, Dict

from langchain_core.tools import tool

from app.core.security import get_user_filesystem_dir
from app.core.path_security import safe_join
from app.services.published import (
    get_service, get_consumer_generated_dir,
)


def _build_consumer_system_prompt(
    admin_id: str,
    service_config: Dict[str, Any],
) -> str:
    """Build system prompt for consumer agent from admin's prompt config."""
    from app.services.prompt import (
        get_user_system_prompt, get_prompt_version,
        get_profile_version, build_user_profile_prompt,
    )

    version_id = service_config.get("system_prompt_version_id")
    if version_id:
        version = get_prompt_version(admin_id, version_id)
        if version:
            base_prompt = version["content"]
        else:
            base_prompt = get_user_system_prompt(admin_id)
    else:
        base_prompt = get_user_system_prompt(admin_id)

    profile_version_id = service_config.get("user_profile_version_id")
    if profile_version_id:
        pv = get_profile_version(admin_id, profile_version_id)
        if pv and pv.get("content", "").strip():
            profile_context = (
                "\n## 个性规则\n"
                "以下是当前用户定义的个性化规则。你必须根据这些规则定制你的回复风格、"
                "内容深度、用语习惯，以及生成的语音、文字、视频、图像等所有输出内容。\n\n"
                f"{pv['content']}"
            )
        else:
            profile_context = build_user_profile_prompt(admin_id)
    else:
        profile_context = build_user_profile_prompt(admin_id)

    from app.services.preferences import get_tz_offset
    tz_hours = get_tz_offset(admin_id)
    user_tz = timezone(timedelta(hours=tz_hours))
    user_now = datetime.now(user_tz)
    today_str = user_now.strftime("%Y年%m月%d日")
    base_prompt = base_prompt.replace("{today}", today_str)
    base_prompt = base_prompt.replace("{user_profile_context}", profile_context)

    # ── 可用资源范围 ──
    # 发布 Service 时设定的 allowed_docs / allowed_scripts 白名单必须显式告知 agent，
    # 否则它不知道有哪些文档/脚本可用，会"迷茫不知道去哪看东西"。
    # 这里只写白名单 pattern（不扫描真实文件）：'*' 表示全部，空脚本列表表示禁用脚本。
    allowed_docs = service_config.get("allowed_docs") or []
    allowed_scripts = service_config.get("allowed_scripts") or []

    scope_lines = ["\n\n## 可用资源范围"]

    # 文档范围（/docs/，只读）——必须明确告知"只读"，避免 agent 尝试修改 docs。
    if "*" in allowed_docs:
        scope_lines.append(
            "- 文档（只读）：你可以访问 /docs/ 目录下的全部文件，仅可用 read_file / "
            "read_document 读取，**不可修改、写入或删除**。请先用 ls 浏览目录结构再读取。"
        )
    elif allowed_docs:
        scope_lines.append(
            "- 文档（只读）：你只能读取 /docs/ 下以下范围内的文件（其余文件不可见，无需也无法读取）；"
            "这些文件**只读**，仅可用 read_file / read_document 读取，不可修改、写入或删除："
        )
        for p in allowed_docs:
            scope_lines.append(f"  - /docs/{str(p).lstrip('/')}")
    else:
        scope_lines.append("- 文档：本服务未开放任何 /docs/ 文档。")

    # 脚本范围（/scripts/，仅可通过 run_script 执行）——必须明确"execute only"：
    # 脚本只能被执行，不能读取源码、不能修改/写入。
    if not allowed_scripts:
        scope_lines.append("- 脚本：本服务不提供脚本执行能力，请勿尝试运行脚本。")
    elif "*" in allowed_scripts:
        scope_lines.append(
            "- 脚本（仅执行）：你可以通过 run_script 执行 /scripts/ 目录下的全部脚本。"
            "脚本**不能修改或写入**；调用方式：run_script(script_path=脚本路径)，"
            "可选传 script_args / input_data / timeout。"
        )
    else:
        scope_lines.append(
            "- 脚本（仅执行）：你只能通过 run_script 执行以下脚本（其余脚本不可用）。"
            "脚本**不能修改或写入**；调用方式：run_script(script_path=脚本路径)，"
            "可选传 script_args / input_data / timeout："
        )
        for p in allowed_scripts:
            scope_lines.append(f"  - /scripts/{str(p).lstrip('/')}")

    scope_notice = "\n".join(scope_lines)

    consumer_notice = (
        "\n\n## 重要约束\n"
        "- /docs/ 目录中的文件是只读的，请勿尝试修改\n"
        "- 你生成的内容（图片、音频、视频等）保存在 /generated/ 目录\n"
    )
    return base_prompt + scope_notice + consumer_notice


def _create_consumer_read_tools(
    admin_id: str,
    allowed_docs: List[str],
    allowed_scripts: Optional[List[str]] = None,
):
    """Create read-only file tools scoped to admin's docs (+ whitelisted scripts).

    docs/ 受 allowed_docs 控制；scripts/ 受 allowed_scripts 控制，**只读**（读源码
    用于了解脚本用途/参数，实际执行仍走 run_script）。两套白名单互不影响。
    """
    fs_dir = get_user_filesystem_dir(admin_id)
    docs_dir = os.path.join(fs_dir, "docs")
    scripts_dir = os.path.join(fs_dir, "scripts")
    allowed_scripts = allowed_scripts or []

    def _norm_docs_path(path: str) -> str:
        """归一化 docs 路径。consumer 读工具的根目录已经是 docs/，但 agent 习惯
        按 /docs/xxx 的心智模型传路径（system prompt 里也是这么写的）。这里容忍
        并剥掉开头多余的 docs/ 前缀，避免拼成 docs_dir/docs/xxx 而"目录不存在"。

        字面优先、剥前缀兜底：若 docs 根下真实存在一个字面叫 docs 的子目录
        （docs_dir/docs/...），优先按字面解析，避免误把真实嵌套目录当成命名空间前缀
        剥掉。仅当字面路径不存在时才剥掉开头多余的 docs/ 前缀。
        `/docs`（命名空间根 token）始终视为 docs 根。
        """
        clean = (path or "").lstrip("/").replace("\\", "/")
        if not clean or clean == "docs":
            return ""
        if clean.startswith("docs/"):
            stripped = clean[len("docs/"):]
            try:
                if os.path.exists(safe_join(docs_dir, clean)):
                    return clean  # 字面路径真实存在（嵌套 docs/ 目录）→ 按字面
            except (PermissionError, ValueError):
                pass
            return stripped
        return clean

    def _is_allowed(path: str) -> bool:
        if not allowed_docs or allowed_docs == ["*"]:
            return True
        norm = path.lstrip("/").replace("\\", "/").rstrip("/")
        for pattern in allowed_docs:
            if pattern == "*":
                return True
            pat = pattern.lstrip("/").rstrip("/")
            if not pat:
                return True
            # 精确命中，或在白名单目录之下
            if norm == pat or norm.startswith(pat + "/"):
                return True
            # path 是白名单条目的祖先目录 → 允许列出（这样 ls 能逐级走到白名单）
            if pat.startswith(norm + "/"):
                return True
        return False

    # ── scripts 命名空间（只读）──────────────────────────────────────
    # 允许 agent 读取 allowed_scripts 白名单内的脚本源码，用于了解脚本用途/参数；
    # 实际执行仍走 run_script。scripts/ 与 docs/ 是两套独立根目录与独立白名单。
    def _norm_scripts_path(path: str) -> str:
        """归一化 scripts 路径（相对 scripts_dir）。字面优先、剥前缀兜底，
        与 _norm_docs_path 同理处理嵌套同名 scripts/ 目录的歧义。"""
        clean = (path or "").lstrip("/").replace("\\", "/")
        if not clean or clean == "scripts":
            return ""
        if clean.startswith("scripts/"):
            stripped = clean[len("scripts/"):]
            try:
                if os.path.exists(safe_join(scripts_dir, clean)):
                    return clean
            except (PermissionError, ValueError):
                pass
            return stripped
        return clean

    def _is_script_allowed(path: str) -> bool:
        """scripts 读权限：受 allowed_scripts 白名单约束（空 = 不可读任何脚本）。
        与 docs 的 _is_allowed 同样允许祖先目录被列出，便于 ls 逐级走到白名单。"""
        if not allowed_scripts:
            return False
        norm = path.lstrip("/").replace("\\", "/").rstrip("/")
        if norm == "":
            return True  # scripts 根可列（条目逐个再过滤）
        for pattern in allowed_scripts:
            if pattern == "*":
                return True
            pat = pattern.lstrip("/").rstrip("/")
            if not pat:
                return True
            if norm == pat or norm.startswith(pat + "/"):
                return True
            if pat.startswith(norm + "/"):
                return True
        return False

    def _is_scripts_ns(path: str) -> bool:
        raw = (path or "").lstrip("/").replace("\\", "/")
        return raw == "scripts" or raw.startswith("scripts/")

    def _resolve(path: str):
        """把 agent 传入路径路由到 docs/ 或 scripts/ 根。

        返回 (full_path, rel, allowed, ns, err)：
          - full_path: 物理绝对路径（err 非空时为 None）
          - rel:       相对所属根的路径（用于 ls 逐项过滤）
          - allowed:   该路径是否在对应白名单内
          - ns:        'docs' | 'scripts'
          - err:       错误消息（如越界 / generated 不可访问），否则 None
        """
        if _is_scripts_ns(path):
            rel = _norm_scripts_path(path)
            try:
                full = safe_join(scripts_dir, rel) if rel else scripts_dir
            except (PermissionError, ValueError):
                return (None, rel, False, "scripts", "路径超出允许范围")
            return (full, rel, _is_script_allowed(rel), "scripts", None)

        clean = _norm_docs_path(path)
        if clean.startswith("generated"):
            return (None, clean, False, "docs", "generated/ 目录不可通过此工具浏览")
        try:
            full = safe_join(docs_dir, clean) if clean else docs_dir
        except (PermissionError, ValueError):
            return (None, clean, False, "docs", "路径超出允许范围")
        return (full, clean, _is_allowed(clean), "docs", None)

    def _entry_allowed(rel: str, ns: str) -> bool:
        return _is_script_allowed(rel) if ns == "scripts" else _is_allowed(rel)

    @tool
    def ls(path: str = "/") -> str:
        """列出目录内容（只读文件系统）。

        Args:
            path: 目录路径。/ 或 /docs 为文档根；/scripts 列出可读脚本。
        """
        full, rel, _allowed, ns, err = _resolve(path)
        if err:
            return err
        if not os.path.isdir(full):
            return f"目录不存在: {path}"
        entries = []
        for name in sorted(os.listdir(full)):
            child = os.path.join(full, name)
            child_rel = os.path.join(rel, name).replace("\\", "/") if rel else name
            if not _entry_allowed(child_rel, ns):
                continue
            suffix = "/" if os.path.isdir(child) else ""
            entries.append(f"{name}{suffix}")
        return "\n".join(entries) if entries else "(空目录)"

    @tool
    def read_file(path: str) -> str:
        """读取文件内容（只读）。

        可读 /docs/ 下文档，也可读 /scripts/ 下白名单脚本的源码（用于了解脚本用途/参数）。

        Args:
            path: 文件路径，如 /welcome.md 或 /scripts/analyze.py
        """
        full, rel, allowed, ns, err = _resolve(path)
        if err:
            return err
        if not allowed:
            return "无权限访问该文件"
        if not os.path.isfile(full):
            return f"文件不存在: {path}"
        try:
            with open(full, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            return f"读取失败: {e}"

    @tool
    def list_files_sorted(
        path: str = "/",
        order_by: str = "modified",
        desc: bool = True,
        limit: int = 50,
    ) -> str:
        """列出 docs/ 目录文件并按指定字段排序，返回带大小和修改时间的明细。

        Args:
            path: 目录路径，/ 为根目录
            order_by: 排序字段，可选 "name" / "modified" / "size"，默认 "modified"
            desc: 是否倒序（True=新→旧 / 大→小 / Z→A），默认 True
            limit: 最多返回多少条，默认 50，上限 500
        """
        from app.services.tools import (
            _SORT_KEYS, _format_size, _format_mtime_short,
        )
        from datetime import datetime as _dt

        order_by = (order_by or "modified").lower().strip()
        if order_by not in _SORT_KEYS:
            return f"order_by 只能是 name / modified / size，收到: {order_by}"
        limit = max(1, min(int(limit or 50), 500))

        target, base_rel, _allowed, ns, err = _resolve(path)
        if err:
            return err
        if not os.path.isdir(target):
            return f"目录不存在: {path}"

        rows = []
        for name in os.listdir(target):
            full = os.path.join(target, name)
            rel = os.path.join(base_rel, name).replace("\\", "/") if base_rel else name
            if not _entry_allowed(rel, ns):
                continue
            try:
                st = os.stat(full)
                is_dir = os.path.isdir(full)
                rows.append({
                    "name": name,
                    "is_dir": is_dir,
                    "size": 0 if is_dir else st.st_size,
                    "modified_at": _dt.fromtimestamp(st.st_mtime).isoformat(),
                })
            except OSError:
                continue
        if not rows:
            return f"(空目录或全部被权限过滤: {path})"

        key_map = {
            "name": lambda r: r["name"].lower(),
            "modified": lambda r: r["modified_at"],
            "mtime": lambda r: r["modified_at"],
            "size": lambda r: r["size"],
        }
        rows.sort(key=key_map[order_by], reverse=bool(desc))
        truncated = len(rows) > limit
        rows = rows[:limit]

        lines = [f"{'类型':4} {'大小':>8}  {'修改时间':16}  名称"]
        for r in rows:
            kind = "DIR " if r["is_dir"] else "FILE"
            size = "-" if r["is_dir"] else _format_size(r["size"])
            mt = _format_mtime_short(r["modified_at"])
            lines.append(f"{kind:4} {size:>8}  {mt:16}  {r['name']}{'/' if r['is_dir'] else ''}")
        if truncated:
            lines.append(f"... (仅显示前 {limit} 条)")
        return "\n".join(lines)

    # ── 文档读取工具（read_document + view_pdf_page_or_image）──────────
    # Consumer 视角下 path 是相对 docs_dir（与 read_file 一致）；权限走
    # _is_allowed 过滤，不允许越界到 generated/ 等目录。复用 document_tools
    # 的纯提取函数，避免代码重复。
    from app.services.document_tools import (
        _extract_pdf_text,
        _extract_docx_text,
        _extract_xlsx_text,
        _render_pdf_page_to_png_b64,
        _read_image_to_b64,
        _bump_and_check_view_count,
        _PLAIN_TEXT_EXTS,
        _IMAGE_EXTS,
    )

    @tool
    def read_document(path: str) -> str:
        """读取结构化文档（PDF / Word / Excel）并返回纯文本内容。

        支持 .pdf / .docx / .xlsx；纯文本格式（.md / .txt / .csv 等）请用 read_file。
        超过 200KB 自动截断。

        Args:
            path: 文件路径，如 /report.pdf
        """
        full, _rel, allowed, _ns, err = _resolve(path)
        if err:
            return err
        if not allowed:
            return "无权限访问该文件"
        if not os.path.isfile(full):
            return f"文件不存在: {path}"

        ext = os.path.splitext(full)[1].lower()
        if ext in _PLAIN_TEXT_EXTS:
            return f"{path} 是纯文本格式，请改用 read_file。"
        if ext in _IMAGE_EXTS:
            return f"{path} 是图片，请改用 view_pdf_page_or_image。"
        if ext == ".pdf":
            return _extract_pdf_text(full)
        if ext == ".docx":
            return _extract_docx_text(full)
        if ext == ".xlsx":
            return _extract_xlsx_text(full)
        if ext in {".doc", ".xls"}:
            return f"[错误] 旧版 Office 格式 {ext} 暂不支持，请另存为 {ext}x。"
        if ext == ".pptx":
            return "[错误] .pptx 暂不支持。"
        return f"[错误] 不支持的文件类型: {ext}。支持: .pdf / .docx / .xlsx"

    @tool
    def view_pdf_page_or_image(path: str, page: int = 1):
        """**多模态视觉读取** — 把单页 PDF 或图片转成多模态消息让你"看到"。

        仅作为弥补：(a) 扫描件 PDF 文本提取失败、(b) 需要看图表/插图细节、
        (c) 图片内容理解。**单页粒度**，同一文件单次对话最多 5 次。
        想读全文请用 read_document。

        Args:
            path: 文件路径（PDF / png / jpg / jpeg / webp / gif / bmp）
            page: PDF 时为 1-based 页码，默认 1（图片忽略）
        """
        full, _rel, allowed, _ns, err = _resolve(path)
        if err:
            return err
        if not allowed:
            return "无权限访问该文件"
        if not os.path.isfile(full):
            return f"文件不存在: {path}"

        block_msg = _bump_and_check_view_count(path)
        if block_msg:
            return block_msg

        ext = os.path.splitext(full)[1].lower()
        if ext in _IMAGE_EXTS:
            b64, mime, err = _read_image_to_b64(full)
            if err:
                return err
            assert b64 and mime
            return [
                {"type": "text", "text": f"图片 {path}（{mime}）的内容如下："},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ]
        if ext == ".pdf":
            page_idx = max(1, int(page or 1)) - 1
            b64, err = _render_pdf_page_to_png_b64(full, page_idx)
            if err:
                return err
            assert b64
            return [
                {"type": "text", "text": f"PDF {path} 第 {page_idx + 1} 页内容如下："},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]
        if ext in _PLAIN_TEXT_EXTS:
            return f"{path} 是文本文件，请用 read_file。"
        if ext in {".docx", ".xlsx"}:
            return f"{path} 是 Word/Excel，请用 read_document。"
        return f"[错误] 不支持的文件类型: {ext}。支持: .pdf / .png / .jpg / .jpeg / .webp / .gif / .bmp"

    return [ls, read_file, list_files_sorted, read_document, view_pdf_page_or_image]


def _create_consumer_gen_tools(admin_id: str, service_id: str, conv_id: str, capabilities: List[str]):
    """Create AI generation tools that write to the conversation's generated/ dir."""
    from app.storage import get_storage_service

    gen_dir = get_consumer_generated_dir(admin_id, service_id, conv_id)
    conv_dir = os.path.dirname(gen_dir)

    def _consumer_write(rel_path: str, data: bytes):
        """Write generated content to consumer conversation directory."""
        clean = rel_path.lstrip("/").replace("\\", "/")
        if clean.startswith("generated/"):
            clean = clean[len("generated/"):]
        get_storage_service().write_consumer_bytes(admin_id, service_id, conv_id, clean, data)

    tools = []

    if "image" in capabilities:
        from app.services.ai_tools import generate_image as _gen_image_impl

        @tool
        def generate_image(
            prompt: str,
            size: str = "1024x1024",
            quality: str = "auto",
            filename: Optional[str] = None,
        ) -> str:
            """使用 AI 生成图片。

            Args:
                prompt: 图片描述
                size: 尺寸 1024x1024/1536x1024/1024x1536/auto
                quality: 质量 low/medium/high/auto
                filename: 自定义文件名
            """
            result = _gen_image_impl(prompt, conv_dir, size=size, quality=quality,
                                     filename=filename, user_id=admin_id,
                                     write_func=_consumer_write)
            if result["success"]:
                return f"图片已生成：{result['path']}"
            return result["message"]

        tools.append(generate_image)

    if "speech" in capabilities:
        from app.services.ai_tools import generate_speech as _gen_speech_impl

        @tool
        def generate_speech(
            text: str,
            voice: str = "alloy",
            speed: float = 1.0,
            filename: Optional[str] = None,
        ) -> str:
            """将文本转换为语音。

            Args:
                text: 文本内容（最大 4096 字符）
                voice: 声音 alloy/echo/fable/onyx/nova/shimmer
                speed: 语速 0.25-4.0
                filename: 自定义文件名
            """
            result = _gen_speech_impl(text, conv_dir, voice=voice, speed=speed, filename=filename,
                                      user_id=admin_id, write_func=_consumer_write)
            if result["success"]:
                return f"语音已生成：{result['path']}"
            return result["message"]

        tools.append(generate_speech)

    if "video" in capabilities:
        from app.services.ai_tools import generate_video as _gen_video_impl

        @tool
        def generate_video(
            prompt: str,
            seconds: int = 4,
            size: str = "1280x720",
            filename: Optional[str] = None,
        ) -> str:
            """使用 AI 生成视频。

            Args:
                prompt: 视频描述
                seconds: 时长 4/8/12 秒
                size: 分辨率 1280x720/720x1280
                filename: 自定义文件名
            """
            result = _gen_video_impl(prompt, conv_dir, seconds=seconds, size=size, filename=filename,
                                     user_id=admin_id, write_func=_consumer_write)
            if result["success"]:
                return f"视频已生成：{result['path']}"
            return result["message"]

        tools.append(generate_video)

    return tools


def _create_consumer_script_tools(
    admin_id: str,
    service_id: str,
    conv_id: str,
    allowed_scripts: List[str],
):
    """Create script execution tools filtered by allowed_scripts."""
    if not allowed_scripts:
        return []

    from app.services.script_runner import run_script as _run_script_impl
    from app.storage import get_storage_service

    # scripts/ 物理根（与 consumer_script_execution 的 scripts_dir 一致），
    # 用于"字面优先"存在性判断。
    scripts_dir = os.path.join(get_user_filesystem_dir(admin_id), "scripts")

    def _norm_script_path(script_path: str) -> str:
        """归一化脚本路径。run_script 的 script_path 相对 scripts/ 根，但 agent 习惯
        按 /scripts/xxx.py 传（system prompt 也这么写），容忍并剥掉开头 scripts/ 前缀，
        否则既过不了白名单校验、又会拼成 scripts_dir/scripts/xxx 找不到文件。

        字面优先、剥前缀兜底：若 scripts 根下真实存在字面 scripts/xxx.py（即真有嵌套
        的 scripts/ 目录），按字面解析；否则剥掉开头多余的 scripts/ 前缀。
        """
        norm = (script_path or "").replace("\\", "/").lstrip("/")
        if norm.startswith("scripts/"):
            stripped = norm[len("scripts/"):]
            try:
                literal_full = os.path.realpath(os.path.join(scripts_dir, norm))
                _sd = os.path.realpath(scripts_dir)
                # 字面路径真实存在且未越界 → 按字面（嵌套 scripts/ 目录）
                if os.path.isfile(literal_full) and (
                    literal_full == _sd or literal_full.startswith(_sd + os.sep)
                ):
                    return norm
            except (OSError, ValueError):
                pass
            return stripped
        return norm

    def _script_allowed(script_path: str) -> bool:
        norm = _norm_script_path(script_path)
        for pattern in allowed_scripts:
            if pattern == "*":
                return True
            if norm == pattern.lstrip("/") or norm.startswith(pattern.rstrip("/") + "/"):
                return True
        return False

    @tool
    def run_script(
        script_path: str,
        script_args: Optional[List[str]] = None,
        input_data: Optional[str] = None,
        timeout: int = 30,
    ) -> str:
        """执行预配置的 Python 脚本。

        Args:
            script_path: 脚本路径
            script_args: 命令行参数
            input_data: stdin 输入
            timeout: 超时秒数
        """
        if not _script_allowed(script_path):
            return f"无权执行此脚本: {script_path}"

        # 剥掉 agent 可能带的 /scripts/ 前缀，得到相对 scripts/ 根的路径再执行
        norm_script_path = _norm_script_path(script_path)

        from app.services.venv_manager import get_user_python
        storage = get_storage_service()
        with storage.consumer_script_execution(admin_id, service_id, conv_id, norm_script_path) as ctx:
            if "error" in ctx:
                return f"执行失败: {ctx['error']}"
            # service/消费者侧脚本执行**始终保持完整限制**：
            # 即使部署级超管开关 SUPERADMIN_SCRIPT_UNRESTRICTED 开启，这里也绝不
            # 传 unrestricted=True（保持 AST/env/资源/超时/路径全部限制）。
            result = _run_script_impl(
                script_path=norm_script_path,
                scripts_dir=ctx["scripts_dir"],
                input_data=input_data,
                args=script_args,
                timeout=timeout,
                allowed_read_dirs=[ctx["docs_dir"]],
                allowed_write_dirs=ctx["write_dirs"],
                python_executable=get_user_python(admin_id),
                unrestricted=False,
            )
        if result["error"]:
            return f"执行失败: {result['error']}"
        parts = []
        if result["stdout"]:
            parts.append(f"输出:\n{result['stdout']}")
        if result["stderr"]:
            parts.append(f"错误输出:\n{result['stderr']}")
        parts.append(f"退出码: {result['exit_code']}")
        return "\n".join(parts) if parts else "脚本执行完成（无输出）"

    return [run_script]


log_consumer = logging.getLogger(__name__)

_DEFAULT_CONSUMER_AGENT_CACHE_MAX = 48
_CONSUMER_AGENT_CACHE_CAP = 512


def consumer_agent_cache_max() -> int:
    raw = os.environ.get("CONSUMER_AGENT_CACHE_MAX", "").strip()
    if not raw:
        return _DEFAULT_CONSUMER_AGENT_CACHE_MAX
    try:
        n = int(raw, 10)
    except ValueError:
        return _DEFAULT_CONSUMER_AGENT_CACHE_MAX
    return max(4, min(_CONSUMER_AGENT_CACHE_CAP, n))


_consumer_agent_cache: OrderedDict[str, Any] = OrderedDict()


def _touch_consumer_agent_cache(key: str) -> None:
    _consumer_agent_cache.move_to_end(key)


def _put_consumer_agent_cache(key: str, agent: Any) -> None:
    _consumer_agent_cache[key] = agent
    _touch_consumer_agent_cache(key)
    cap = consumer_agent_cache_max()
    while len(_consumer_agent_cache) > cap:
        evicted, _ = _consumer_agent_cache.popitem(last=False)
        log_consumer.debug(
            "Evicted consumer agent cache entry (max=%d): %s",
            cap, evicted[:120],
        )


def create_consumer_agent(
    admin_id: str,
    service_id: str,
    conv_id: str,
    wechat_session_id: Optional[str] = None,
    extra_capabilities: Optional[List[str]] = None,
    channel: str = "web",
) -> Any:
    """Create (or return cached) agent for a consumer conversation.

    extra_capabilities: additional capabilities to inject (e.g. ["humanchat"]
    for scheduled tasks that need send_message).
    channel: invocation context. One of:
        - "web"       — consumer 直连 SSE（/api/v1/chat），agent 输出直接流给浏览器，
                        send_message 工具在该上下文下**不**注入（即便 humanchat capability
                        启用），避免 ghost 调用与无意义的工具事件泄露给消费者。
        - "wechat"    — 通过 iLink 反向投递到微信用户，需要 send_message。
        - "scheduler" — 定时任务推送，也需要 send_message。
    """
    from langchain.chat_models import init_chat_model
    from deepagents import create_deep_agent
    from app.services.agent import _resolve_model, _checkpointer
    from app.storage import create_consumer_backend

    svc_config = get_service(admin_id, service_id)
    if not svc_config:
        raise ValueError(f"Service {service_id} not found")

    extra_suffix = f"::+{','.join(sorted(extra_capabilities))}" if extra_capabilities else ""
    ws_suffix = f"::{wechat_session_id}" if wechat_session_id else ""
    ch_suffix = f"::ch={channel}" if channel and channel != "web" else ""
    cache_key = f"consumer::{admin_id}::{service_id}::{conv_id}{ws_suffix}{extra_suffix}{ch_suffix}"
    if cache_key in _consumer_agent_cache:
        _touch_consumer_agent_cache(cache_key)
        return _consumer_agent_cache[cache_key]

    gen_dir = get_consumer_generated_dir(admin_id, service_id, conv_id)
    os.makedirs(gen_dir, exist_ok=True)

    backend = create_consumer_backend(admin_id, service_id, conv_id, gen_dir)

    system_prompt = _build_consumer_system_prompt(admin_id, svc_config)

    capabilities = list(svc_config.get("capabilities", []))
    if extra_capabilities:
        for cap in extra_capabilities:
            if cap not in capabilities:
                capabilities.append(cap)
    allowed_docs = svc_config.get("allowed_docs", ["*"])
    allowed_scripts = svc_config.get("allowed_scripts", ["*"])
    research_tools = svc_config.get("research_tools", False)

    tools = []
    tools.extend(_create_consumer_read_tools(admin_id, allowed_docs, allowed_scripts))
    tools.extend(_create_consumer_gen_tools(admin_id, service_id, conv_id, capabilities))
    tools.extend(_create_consumer_script_tools(admin_id, service_id, conv_id, allowed_scripts))

    # documents capability prompt — read_document / view_pdf_page_or_image are
    # always injected via _create_consumer_read_tools above (no flag needed),
    # so unconditionally append the usage prompt.
    from app.services.tools import CAPABILITY_PROMPTS as _CP_DOC
    system_prompt += "\n" + _CP_DOC["documents"]

    if research_tools or "web" in capabilities:
        from app.services.tools import create_web_tools, CAPABILITY_PROMPTS as _CP
        tools.extend(create_web_tools(user_id=admin_id))
        system_prompt += "\n" + _CP["web"]

    if "scheduler" in capabilities:
        from app.services.tools import (
            create_service_schedule_tool, create_service_manage_tasks_tool,
            create_spawn_child_task_tool,
            CAPABILITY_PROMPTS as _CP2,
        )
        tools.append(create_service_schedule_tool(
            admin_id, service_id, conv_id,
            wechat_session_id=wechat_session_id,
        ))
        tools.append(create_service_manage_tasks_tool(
            admin_id, service_id, conv_id,
        ))
        # v2: spawn — only fires inside scheduled-task execution, otherwise
        # the tool returns an error string.  Always co-injected with the
        # scheduler capability since spawn is meaningless without the parent
        # task lineage.
        tools.append(create_spawn_child_task_tool())
        system_prompt += "\n" + _CP2["service_scheduler"]
        system_prompt += "\n" + _CP2["spawn"]

    # send_message 仅对反向投递渠道（wechat / scheduler）有意义；
    # web 直连 SSE 时 agent 的 token 已经流给浏览器，再调 send_message 既无投递目标
    # 也会产生让消费者困惑的工具事件。
    if "humanchat" in capabilities and channel != "web":
        from app.services.tools import create_send_message_tool, CAPABILITY_PROMPTS as _CP3
        system_prompt += "\n" + _CP3["humanchat"]
        tools.append(create_send_message_tool())

    # contact_admin is always available for consumer agents
    from app.services.tools import create_contact_admin_tool, CAPABILITY_PROMPTS as _CP4
    tools.append(create_contact_admin_tool(
        admin_id, service_id, conv_id,
        wechat_session_id=wechat_session_id,
    ))
    system_prompt += "\n" + _CP4["contact_admin"]

    # Memory subagent — consumer can only read its own conversation
    from app.services.memory_tools import create_consumer_memory_tools
    consumer_memory_tools = create_consumer_memory_tools(admin_id, service_id, conv_id)
    consumer_subagents = [
        {
            "name": "memory",
            "description": (
                "对话记忆助手：查询当前对话的历史消息。"
                "当你需要回忆之前和用户聊了什么时，委托给它。"
            ),
            "system_prompt": (
                "你是对话记忆助手。你的职责是从当前对话历史中检索信息并提供简洁摘要。\n\n"
                "工作原则：\n"
                "1. 用 read_my_conversation 查看历史消息\n"
                "2. 输出简洁的摘要，突出关键信息和时间线\n"
                "3. 标注消息来源（用户/助手/工具调用/管理员指令）\n\n"
                "保持回复在 500 字以内。"
            ),
            "tools": consumer_memory_tools,
        },
    ]

    model_id = svc_config.get("model", "anthropic:claude-sonnet-4-5-20250929")
    resolved_model = _resolve_model(model_id, user_id=admin_id)

    agent = create_deep_agent(
        model=resolved_model,
        system_prompt=system_prompt,
        backend=backend,
        tools=tools,
        subagents=consumer_subagents,
        checkpointer=_checkpointer,
        name=f"svc-{service_id}-{conv_id}",
    )

    _put_consumer_agent_cache(cache_key, agent)
    return agent


def clear_consumer_cache(admin_id: str = None, service_id: str = None):
    prefix = "consumer::"
    if admin_id:
        prefix += f"{admin_id}::"
        if service_id:
            prefix += f"{service_id}::"
    keys = [k for k in _consumer_agent_cache if k.startswith(prefix)]
    for k in keys:
        del _consumer_agent_cache[k]
