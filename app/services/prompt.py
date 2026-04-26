import os
import json
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any

from app.core.security import get_user_dir

USER_PROMPT_FILE = "system_prompt.txt"
USER_PROFILE_FILE = "user_profile.json"
PROMPT_VERSIONS_FILE = "system_prompt_versions.json"
PROFILE_VERSIONS_FILE = "user_profile_versions.json"
CAPABILITY_PROMPTS_FILE = "capability_prompts.json"

DEFAULT_USER_PROFILE = {
    "portfolio": "",
    "risk_preference": "",
    "investment_habits": "",
    "user_persona": "",
    "custom_notes": "",
}

DEFAULT_SYSTEM_PROMPT = """现在是 {today}。

你是一个智能助手，可以帮助用户管理文件和执行脚本。

{user_profile_context}

## 文件系统
你的文件系统有两个主要目录：
- `/docs/` - 文档目录，存放各种文档文件
- `/scripts/` - 脚本目录，存放可执行的 Python 脚本

你可以使用以下工具操作文件：
- `ls` - 列出目录内容（使用 `/` 查看根目录）
- `read_file` - 读取文件内容
- `write_file` - 创建新文件
- `edit_file` - 编辑已有文件
- `glob` - 搜索文件
- `grep` - 搜索文件内容

## 脚本执行
- 使用 `run_script` 工具执行 `/scripts/` 目录下的 Python 脚本
- 你也可以在 `/scripts/` 目录下创建新的脚本，然后执行它
- 脚本可以使用 numpy、pandas、scipy、matplotlib、seaborn、plotly 等数据科学库

### ⚠️ 脚本中的路径规则（非常重要）
脚本通过独立子进程执行，工作目录为 scripts/ 所在的真实路径。
**脚本内部不能使用 `/docs/`、`/scripts/` 等虚拟路径**，必须使用相对路径：
- 当前目录（scripts/）下的文件：`open("output.txt", "w")`
- 读写 docs/ 目录：`open("../docs/data.csv", "r")`
- 读写 generated/ 目录：`open("../generated/result.png", "wb")`
- 读写 tasks/ 目录：`open("../tasks/state.json", "r")`
- 总结：脚本的 cwd 是 `scripts/`，其他目录通过 `../目录名/` 访问

## 媒体文件展示
当用户文件系统中有图片、音频、视频或 PDF 文件时，你可以在回复中使用 **<<FILE:路径>>** 标签来直接展示它们。
前端会自动将该标签渲染为对应的媒体播放器（图片预览、音频播放器、视频播放器、PDF 阅读器）。

### 语法
```
<<FILE:/完整路径/文件名.扩展名>>
```

### 示例
- 展示图片：`<<FILE:/docs/chart.png>>`
- 播放音频：`<<FILE:/docs/recording.mp3>>`
- 播放视频：`<<FILE:/scripts/demo.mp4>>`
- 查看 PDF：`<<FILE:/docs/report.pdf>>`

### 支持的格式
- 图片: jpg, jpeg, png, gif, webp, svg, bmp
- 音频: mp3, wav, ogg, m4a, flac, aac
- 视频: mp4, webm, mov, mkv, avi
- 文档: pdf
- 网页: html, htm（在聊天中以 iframe 内嵌展示，适合交互式图表、数据可视化、报告等）

### 重要规则
1. **路径必须完整** — 从根目录 `/` 开始，包含目录和文件名，如 `/scripts/output.png`，不要省略目录
2. **标签独占一行** — `<<FILE:path>>` 前后各空一行，确保渲染正常
3. **先确认文件存在** — 用 `ls` 或 `glob` 确认文件路径后再展示
4. 当用户要求查看、展示、预览某个媒体文件时，请使用此标签
5. 如果需要用脚本生成图表，请将图片保存到用户文件系统（如 `/scripts/output.png`），然后使用 `<<FILE:/scripts/output.png>>` 展示
6. **HTML 交互式内容**：当需要展示交互式图表（如 Plotly、ECharts）、数据可视化或富文本报告时，可以用脚本生成 `.html` 文件（自包含的单文件 HTML，内联 CSS/JS），保存到 `/scripts/` 或 `/generated/` 目录，然后用 `<<FILE:/scripts/chart.html>>` 展示。前端会将其作为可展开的 iframe 嵌入聊天中

## 平台使用指南
用户的 `/docs/README.md` 是平台使用指南，包含了平台的全部功能和你的能力说明。
当用户询问"你能做什么"、"平台有什么功能"、"怎么使用"等问题时，请先阅读 `/docs/README.md` 然后据此回答。

## 注意事项
- 所有路径以 `/` 开头，表示用户文件系统根目录
- 你可以自由读写所有文件
- 请用中文回答用户的问题
"""


# ==================== User Profile ====================

def get_user_profile(user_id: str) -> Dict[str, Any]:
    profile_path = os.path.join(get_user_dir(user_id), USER_PROFILE_FILE)
    if os.path.exists(profile_path):
        with open(profile_path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        return {**DEFAULT_USER_PROFILE, **saved}
    return dict(DEFAULT_USER_PROFILE)


def set_user_profile(user_id: str, profile: Dict[str, Any], auto_version: bool = True):
    from app.services.agent import clear_agent_cache
    from app.services.consumer_agent import clear_consumer_cache
    user_dir = get_user_dir(user_id)
    os.makedirs(user_dir, exist_ok=True)
    from app.core.fileutil import atomic_json_save
    atomic_json_save(os.path.join(user_dir, USER_PROFILE_FILE), profile, ensure_ascii=False, indent=2)
    clear_agent_cache(user_id)
    clear_consumer_cache(admin_id=user_id)
    if auto_version:
        notes = profile.get("custom_notes", "")
        if notes.strip():
            save_profile_version(user_id, notes)


def build_user_profile_prompt(user_id: str) -> str:
    profile = get_user_profile(user_id)
    has_content = any(v.strip() for v in profile.values() if isinstance(v, str))
    if not has_content:
        return ""
    sections = [
        "## 个性规则",
        "以下是当前用户定义的个性化规则。你必须根据这些规则定制你的回复风格、内容深度、用语习惯，"
        "以及生成的语音、文字、视频、图像等所有输出内容。",
    ]
    label_map = {
        "portfolio": ("投资组合 / Portfolio", "用户当前的投资组合和资产配置情况"),
        "risk_preference": ("风险偏好 / Risk Preference", "用户的风险承受能力和偏好"),
        "investment_habits": ("投资习惯 / Investment Habits", "用户的投资风格、频率、关注点"),
        "user_persona": ("用户画像 / User Persona", "用户的身份背景、专业程度、沟通偏好"),
        "custom_notes": ("其他备注 / Custom Notes", "其他需要注意的个性化信息"),
    }
    for key, (label, _) in label_map.items():
        value = profile.get(key, "").strip()
        if value:
            sections.append(f"\n### {label}\n{value}")
    return "\n".join(sections)


# ==================== Profile Versions ====================

def _get_profile_versions_path(user_id: str) -> str:
    return os.path.join(get_user_dir(user_id), PROFILE_VERSIONS_FILE)


def _load_profile_versions(user_id: str) -> list:
    path = _get_profile_versions_path(user_id)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("versions", [])
    return []


def _save_profile_versions(user_id: str, versions: list):
    from app.core.fileutil import atomic_json_save
    atomic_json_save(_get_profile_versions_path(user_id), {"versions": versions}, ensure_ascii=False, indent=2)


def list_profile_versions(user_id: str) -> list:
    versions = _load_profile_versions(user_id)
    return [
        {"id": v["id"], "label": v.get("label", ""), "note": v.get("note", ""),
         "timestamp": v["timestamp"], "char_count": len(v.get("content", ""))}
        for v in versions
    ]


def save_profile_version(user_id: str, content: str, label: str = "", note: str = "") -> dict:
    versions = _load_profile_versions(user_id)
    version = {
        "id": uuid.uuid4().hex[:8],
        "content": content,
        "label": label or f"v{len(versions) + 1}",
        "note": note,
        "timestamp": datetime.now().isoformat(),
    }
    versions.append(version)
    _save_profile_versions(user_id, versions)
    return {"id": version["id"], "label": version["label"], "timestamp": version["timestamp"]}


def get_profile_version(user_id: str, version_id: str) -> Optional[dict]:
    for v in _load_profile_versions(user_id):
        if v["id"] == version_id:
            return v
    return None


def delete_profile_version(user_id: str, version_id: str) -> bool:
    versions = _load_profile_versions(user_id)
    new_versions = [v for v in versions if v["id"] != version_id]
    if len(new_versions) == len(versions):
        return False
    _save_profile_versions(user_id, new_versions)
    return True


def rollback_profile_version(user_id: str, version_id: str) -> Optional[str]:
    version = get_profile_version(user_id, version_id)
    if not version:
        return None
    profile = get_user_profile(user_id)
    profile["custom_notes"] = version["content"]
    set_user_profile(user_id, profile, auto_version=False)
    return version["content"]


# ==================== System Prompt ====================

def get_user_system_prompt(user_id: str) -> str:
    prompt_path = os.path.join(get_user_dir(user_id), USER_PROMPT_FILE)
    if os.path.exists(prompt_path):
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()
    return DEFAULT_SYSTEM_PROMPT


def set_user_system_prompt(user_id: str, prompt: str, auto_version: bool = True):
    from app.services.agent import clear_agent_cache
    from app.services.consumer_agent import clear_consumer_cache
    user_dir = get_user_dir(user_id)
    os.makedirs(user_dir, exist_ok=True)
    with open(os.path.join(user_dir, USER_PROMPT_FILE), "w", encoding="utf-8") as f:
        f.write(prompt)
    clear_agent_cache(user_id)
    clear_consumer_cache(admin_id=user_id)
    if auto_version:
        save_prompt_version(user_id, prompt)


def reset_user_system_prompt(user_id: str):
    from app.services.agent import clear_agent_cache
    from app.services.consumer_agent import clear_consumer_cache
    prompt_path = os.path.join(get_user_dir(user_id), USER_PROMPT_FILE)
    if os.path.exists(prompt_path):
        os.remove(prompt_path)
    clear_agent_cache(user_id)
    clear_consumer_cache(admin_id=user_id)


# ==================== Prompt Versions ====================

def _get_versions_path(user_id: str) -> str:
    return os.path.join(get_user_dir(user_id), PROMPT_VERSIONS_FILE)


def _load_versions(user_id: str) -> list:
    path = _get_versions_path(user_id)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("versions", [])
    return []


def _save_versions(user_id: str, versions: list):
    from app.core.fileutil import atomic_json_save
    atomic_json_save(_get_versions_path(user_id), {"versions": versions}, ensure_ascii=False, indent=2)


def list_prompt_versions(user_id: str) -> list:
    versions = _load_versions(user_id)
    return [
        {"id": v["id"], "label": v.get("label", ""), "note": v.get("note", ""),
         "timestamp": v["timestamp"], "char_count": len(v.get("content", ""))}
        for v in versions
    ]


def save_prompt_version(user_id: str, content: str, label: str = "", note: str = "") -> dict:
    versions = _load_versions(user_id)
    version = {
        "id": uuid.uuid4().hex[:8],
        "content": content,
        "label": label or f"v{len(versions) + 1}",
        "note": note,
        "timestamp": datetime.now().isoformat(),
    }
    versions.append(version)
    _save_versions(user_id, versions)
    return {"id": version["id"], "label": version["label"], "timestamp": version["timestamp"]}


def get_prompt_version(user_id: str, version_id: str) -> Optional[dict]:
    for v in _load_versions(user_id):
        if v["id"] == version_id:
            return v
    return None


def update_prompt_version_meta(user_id: str, version_id: str, label: str = None, note: str = None) -> bool:
    versions = _load_versions(user_id)
    for v in versions:
        if v["id"] == version_id:
            if label is not None:
                v["label"] = label
            if note is not None:
                v["note"] = note
            _save_versions(user_id, versions)
            return True
    return False


def delete_prompt_version(user_id: str, version_id: str) -> bool:
    versions = _load_versions(user_id)
    new_versions = [v for v in versions if v["id"] != version_id]
    if len(new_versions) == len(versions):
        return False
    _save_versions(user_id, new_versions)
    return True


def rollback_prompt_version(user_id: str, version_id: str) -> Optional[str]:
    version = get_prompt_version(user_id, version_id)
    if not version:
        return None
    set_user_system_prompt(user_id, version["content"])
    return version["content"]


# ==================== Capability Prompts (per-user overrides) ====================

def get_capability_prompts(user_id: str) -> Dict[str, str]:
    """Return user-customized capability prompts (only the overrides, not defaults)."""
    path = os.path.join(get_user_dir(user_id), CAPABILITY_PROMPTS_FILE)
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_capability_prompts(user_id: str, overrides: Dict[str, str]):
    """Save user-customized capability prompts."""
    from app.services.agent import clear_agent_cache
    from app.services.consumer_agent import clear_consumer_cache
    user_dir = get_user_dir(user_id)
    os.makedirs(user_dir, exist_ok=True)
    from app.core.fileutil import atomic_json_save
    atomic_json_save(os.path.join(user_dir, CAPABILITY_PROMPTS_FILE), overrides, ensure_ascii=False, indent=2)
    clear_agent_cache(user_id)
    clear_consumer_cache(admin_id=user_id)


def get_resolved_capability_prompt(user_id: str, key: str, defaults: Dict[str, str]) -> str:
    """Return user override for a capability prompt, falling back to default."""
    overrides = get_capability_prompts(user_id)
    if key in overrides:
        return overrides[key]
    return defaults.get(key, "")


# ==================== File-mention expansion ====================

# Used by the chat input @-mention picker. Frontend serializes a chip into
# `[[FILE:/abs/path]]` (deliberately distinct from the agent-output `<<FILE:>>`
# so accidental nesting / round-tripping never collides). Backend rewrites
# them to `<<FILE:>>` before they enter the LLM message so:
#   - the agent sees the same notation it itself emits (symmetry),
#   - the frontend renders the user bubble via the existing markdown.ts
#     `<<FILE:>>` pipeline (media inline preview / non-media file pill).
import re as _re_filemention
_FILE_MENTION_RE = _re_filemention.compile(r"\[\[FILE:(/[^\[\]]+?)\]\]")


def expand_file_mentions(content):
    """Rewrite `[[FILE:/path]]` markers to `<<FILE:/path>>` in-place.

    Accepts the same str / multimodal-list shapes as `stamp_message`.
    Safe to call multiple times (idempotent — second pass finds no matches).
    """
    if isinstance(content, str):
        return _FILE_MENTION_RE.sub(r"<<FILE:\1>>", content)
    if isinstance(content, list):
        out = []
        changed = False
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                txt = block.get("text", "")
                new_txt = _FILE_MENTION_RE.sub(r"<<FILE:\1>>", txt)
                if new_txt is not txt:
                    changed = True
                out.append({**block, "text": new_txt})
            else:
                out.append(block)
        return out if changed else content
    return content


# ==================== Message Timestamp Injection ====================

def stamp_message(content, user_id: str):
    """Prepend a timestamp to user message content.

    Works with both str and multimodal list content.
    The timestamp uses the user's configured timezone.
    """
    from app.services.preferences import get_tz_offset
    tz_hours = get_tz_offset(user_id)
    user_tz = timezone(timedelta(hours=tz_hours))
    now_str = datetime.now(user_tz).strftime("%Y-%m-%d %H:%M:%S")

    if isinstance(content, str):
        return f"[{now_str}] {content}"

    if isinstance(content, list):
        for i, block in enumerate(content):
            if isinstance(block, dict) and block.get("type") == "text":
                stamped = list(content)
                stamped[i] = {**block, "text": f"[{now_str}] {block.get('text', '')}"}
                return stamped
        return [{"type": "text", "text": f"[{now_str}]"}] + list(content)

    return content
