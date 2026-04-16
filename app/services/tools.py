"""
Tool factories for agent and voice sessions.

All LangChain @tool objects are created here and injected into agents/subagents.
"""

import json
import os
from typing import Optional, List, Dict

from langchain_core.tools import tool

from app.core.security import get_user_filesystem_dir
from app.services.ai_tools import (
    generate_image as _gen_image_impl,
    generate_speech as _gen_speech_impl,
    generate_video as _gen_video_impl,
)
from app.services.script_runner import run_script as _run_script_impl

PLAN_MODE_PROMPT = """[Plan Mode 已开启]
你必须先分析用户的任务需求，而不是直接执行：
1. 如果有不明确的地方，先向用户提问澄清（用自然语言，不要调用工具）
2. 当你充分理解需求后，调用 propose_plan 工具提出你的执行计划
3. 等待用户审批后再开始执行
4. 如果用户修改了你的计划，按修改后的版本执行

绝对不要跳过规划阶段直接执行任务。在用户批准计划之前，不要调用除 propose_plan 以外的任何工具。"""

CAPABILITY_PROMPTS = {
    "web": """
## 联网工具
你拥有 `web_search` 和 `web_fetch` 两个联网工具。

**web_search** — 搜索互联网，返回多条结果摘要
- 适合：查找最新资讯、寻找参考资料、不确定某件事时先搜再答
- 参数：query（搜索关键词）、count（结果数量，默认10）

**web_fetch** — 读取指定网页的完整正文
- 适合：需要阅读某篇文章、文档页或网页详情
- 参数：url（目标网址）

使用建议：
1. 遇到时效性问题（新闻/价格/天气等）时主动联网查询
2. 搜索后若需要更多细节，用 web_fetch 读取对应链接
3. 引用联网内容时注明来源 URL
""",
    "image": """
## AI 图片生成
你拥有 `generate_image` 工具，可以根据文字描述生成图片（使用 gpt-image-1 模型）。
- 当用户要求画图、生成图片、设计图等时，调用此工具
- prompt 要用英文，尽量详细描述画面内容、风格、色调、构图
- 生成的图片保存在 /generated/images/ 目录
- 工具会返回文件路径，请用 <<FILE:路径>> 展示给用户
- 可选尺寸: 1024x1024(方形)、1536x1024(横向)、1024x1536(纵向)
""",
    "speech": """
## AI 语音生成
你拥有 `generate_speech` 工具，可以将文本转换为自然语音（使用 OpenAI TTS）。
- 当用户要求朗读、播放语音、TTS 等时，调用此工具
- 可选声音: alloy(中性)、echo(男声)、nova(女声)、shimmer(温柔女声)、onyx(低沉男声)、fable(英式)
- 生成的音频保存在 /generated/audio/ 目录
- 工具会返回音频文件路径，请用 <<FILE:路径>> 展示给用户
- 最大支持 4096 字符
""",
    "video": """
## AI 视频生成
你拥有 `generate_video` 工具，可以根据文字描述生成视频（使用 Sora 2 模型）。
- 当用户要求制作视频、动画、短片等时，调用此工具
- prompt 要用英文，详细描述画面运动、镜头语言、光线、环境
- **注意：视频生成需要 1-5 分钟，请提前告知用户等待**
- 生成的视频保存在 /generated/videos/ 目录
- 可选时长: 4秒、8秒、12秒
- 工具会返回视频文件路径，请用 <<FILE:路径>> 展示给用户
""",
    "scheduler": """
## 定时任务
你拥有 `schedule_task` 工具，可以帮用户创建定时执行的任务。

### 调度方式
- **一次性**（once）：指定具体时间，**必须带时区后缀**，如 "2026-04-01T09:00:00+08:00"。用当前时区（见系统时间）生成后缀。
- **Cron 表达式**（cron）：如 "0 9 * * 1" 表示每周一早 9 点。Cron 按用户当前时区解释，无需额外处理。
- **固定间隔**（interval）：单位秒，如 "3600" 表示每小时

### ⚠️ 时区规则（极其重要）
- once 类型的 schedule 值**必须**包含时区偏移后缀（如 +08:00、+00:00）
- cron 表达式中的时间是用户当地时间，系统会自动按用户时区解释
- 用户说"十分钟后"时，基于当前时间（见系统 prompt 中的 {time}）加上偏移量，并带上时区后缀

### 任务类型
- **script**：执行 scripts/ 目录中的 Python 脚本
- **agent**：让 Agent 根据文档/技能说明书 + prompt 完成完整任务链

### Agent 任务的文档驱动模式
当 task_type="agent" 时，可以通过 agent_doc_path（单文档）或 agent_doc_paths（多文档）指定 docs/ 下的技能/任务说明书。
Agent 会读取文档内容，按照其中描述的步骤逐步执行。
还可以通过 agent_capabilities 指定 Agent 需要的工具能力（如 ["image", "speech"]）。

### ⚠️ 脚本路径规则（极其重要）
脚本通过独立子进程执行，工作目录（cwd）是 scripts/ 的真实路径。
**脚本内部绝对不能使用 `/docs/`、`/scripts/` 等虚拟路径**，必须用相对路径：
- scripts/ 下的文件：`open("data.txt", "w")`
- docs/ 下的文件：`open("../docs/data.csv", "r")`
- generated/ 下的文件：`open("../generated/result.png", "wb")`
- tasks/ 下的文件：`open("../tasks/state.json", "r")`

### 微信推送
当用户通过微信对话要求"每天XX点给我一份XX"时，设置 reply_wechat=True，任务完成后结果会自动推送回微信。

### 示例场景
- "每天早上9点，按照 daily_report.md 里的步骤搜索新闻并生成语音摘要"
  → task_type="agent", agent_doc_path="daily_report.md", agent_capabilities=["speech"], schedule_type="cron", schedule="0 9 * * *"
- "每周一执行 data_sync.py 同步数据"
  → task_type="script", script_path="data_sync.py", schedule_type="cron", schedule="0 0 * * 1"
- "每天早上给我发一份新闻摘要"（微信中）
  → reply_wechat=True, task_type="agent", agent_prompt="搜索今日新闻并整理摘要", schedule_type="cron", schedule="0 9 * * *"

创建任务后，可在「定时任务控制台」查看运行历史和管理任务。

### 管理已有任务
你还拥有 `manage_scheduled_tasks` 工具，可以查看、修改和删除已有的定时任务。
- **list** — 列出所有定时任务（含 ID、名称、调度、启用状态、下次运行时间）
- **update** — 修改任务（改名、改调度、改 prompt、启用/禁用等）
- **delete** — 删除任务

用户说"取消/删除/关掉那个XXX任务"时，先 list 找到对应任务 ID，再 delete。
用户说"把那个任务改成每周一执行"时，先 list 找到 ID，再 update 修改 schedule。
""",
    "service_scheduler": """
## 定时任务
你拥有 `schedule_task` 工具，可以帮用户创建定时执行的任务。任务完成后结果会自动推送给用户。

### 调度方式
- **一次性**（once）：指定具体时间，**必须带时区后缀**，如 "2026-04-01T09:00:00+08:00"。用当前时区（见系统时间）生成后缀。
- **Cron 表达式**（cron）：如 "0 9 * * 1" 表示每周一早 9 点。Cron 按用户当前时区解释。
- **固定间隔**（interval）：单位秒，如 "3600" 表示每小时

### ⚠️ 时区规则（极其重要）
- once 类型的 schedule 值**必须**包含时区偏移后缀（如 +08:00、+00:00）
- cron 表达式中的时间是用户当地时间，系统会自动按用户时区解释
- 用户说"十分钟后"时，基于当前时间加上偏移量，并带上时区后缀

### 文档驱动
可以通过 doc_path（单文档）或 doc_paths（多文档）指定任务说明书。
Agent 会读取文档内容，按照其中描述的步骤逐步执行。

### 示例
- "每天早上9点给我一份新闻摘要"
  → prompt="搜索今日新闻并整理摘要", schedule_type="cron", schedule="0 9 * * *"
- "每周一给我发一份市场分析"
  → doc_path="weekly_analysis.md", schedule_type="cron", schedule="0 9 * * 1"

### 管理已有任务
你还拥有 `manage_scheduled_tasks` 工具，可以查看、修改和删除已有的定时任务。
- **list** — 列出所有定时任务
- **update** — 修改任务的调度、prompt、启用/禁用等
- **delete** — 删除任务
""",
    "service_broadcast": """
## Service 广播
你拥有 `publish_service_task` 工具，可以向你的 Service 用户群发或定向发送消息。

### 工作机制
- 为每个目标 Service 的活跃微信用户创建一个定时任务
- Service Agent 收到你的指令后，会结合对话上下文自主判断是否推送给用户
- Service Agent 知道这是管理员指令（不是用户消息），不会混淆
- 默认即时触发，也可以设置定时调度

### 使用场景
- "通知所有用户新功能上线"
- "向产品咨询服务的用户推送促销信息"
- "给特定 Service 的用户发送重要通知"
- "给指定会话的用户发送定向消息"

### 参数说明
- service_ids: 指定目标 Service ID 或名称列表（支持名称匹配），不填则广播到全部
- session_ids: 指定目标微信会话 ID 列表，可精确到单个用户
- message: 要传达的信息/指令
- run_now: 是否立即执行（默认 True）
- schedule_type / schedule: 可设置定时广播
""",
    "contact_admin": """
## 联系管理员
你拥有 `contact_admin` 工具，可以向管理员发送通知消息。

### 使用场景
- 用户反馈了严重问题或 bug
- 遇到你无法处理的异常情况
- 用户提出了需要管理员决策的请求
- 有重要的用户行为数据需要汇报

### 注意事项
- 消息会记录到管理员的收件箱
- 如果管理员有微信连接，系统会自动评估是否即时通知
- 只发送有价值的信息，避免过于频繁地打扰管理员
- 消息应简洁明了，包含关键上下文（如用户问题摘要、时间等）
""",
    "humanchat": """
## HumanChat 模式已启用 — 你必须遵守以下规则

**从第一条消息开始，你对用户的回复都必须通过 `send_message` 工具发送。**
直接输出的文本用户完全看不到，只有 `send_message` 的内容会显示在用户的聊天界面中。

### 绝对规则（不可违反）
1. 至少调用一次 `send_message` — 原则是不要ghost用户
2. 任何普通文本输出对用户都是不可见的，你可以用来当作你的思考和草稿，然后用send_message发送简洁的回复给用户
3. 可以多次调用 `send_message` 发送多条消息，模拟真人分段打字的节奏

### send_message 参数
- `message`: 文本内容（必填）
- `media_path`: 媒体文件路径（可选），用于发送图片、语音、视频等

### 对话风格
- 像真人聊天一样自然、简洁
- 适当分段，不要一次发送过长的消息
- 可以用多条消息来组织回复（先发关键信息，再补充细节）
- 语音消息：先用 generate_speech 生成，再通过 send_message 的 media_path 发送
- 不要使用markdown格式，直接发送纯文本
""",
    "memory_subagent": """
## Memory Subagent（记忆助手）
你拥有一个 memory subagent（记忆助手），可以委托它完成以下任务：

### 查询能力
- 列出和读取历史对话记录
- 查看收件箱中 Service Agent 的反馈

### Soul 笔记（已启用写入）
- memory subagent 可以在 `soul/` 目录下创建、编辑笔记文件
- `soul/` 是你的「灵魂笔记本」，用来记录长期记忆、洞察、重要决策
- 建议使用子目录组织：`notes/`（笔记）、`summaries/`（总结）、`insights/`（洞察）

### 使用时机
- 用户提到之前聊过的内容时，委托 memory subagent 检索
- 发现重要信息、用户偏好变化时，让它写入 soul 笔记
- 需要回顾历史决策或上下文时使用
- 对话结束时，如有值得记录的要点，让它保存到 soul
""",
    "soul_edit": """
## Soul 文件系统（已启用）
你的文件系统中有一个 `/soul/` 目录，这是你的「灵魂空间」。

### 目录结构
- `/soul/` 下可自由创建文件和子目录
- `/soul/config.json` 是系统配置，请勿修改

### 用途
- 记录长期记忆、重要洞察、用户偏好
- 存放自定义的知识库、参考资料
- 管理个人笔记和工作日志

### 使用建议
- 重要信息随时写入，不要等到对话结束
- 使用结构化格式（Markdown/JSON），方便后续检索
- 定期整理和更新，保持信息新鲜
""",
}


@tool
def propose_plan(steps: List[str], questions: Optional[List[str]] = None) -> str:
    """向用户提出执行计划，等待用户审批后才能开始执行。

    Args:
        steps: 计划步骤列表，每一步是一个简要描述字符串
        questions: 可选，需要用户回答的补充问题列表
    """
    return json.dumps({"steps": steps, "status": "approved"}, ensure_ascii=False)


def create_run_script_tool(user_id: str):
    """Create a run_script tool bound to the user's scripts/ directory."""
    from app.storage import get_storage_service
    from app.services.venv_manager import get_user_python

    @tool
    def run_script(
        script_path: str,
        script_args: Optional[List[str]] = None,
        input_data: Optional[str] = None,
        timeout: int = 30,
    ) -> str:
        """执行 scripts/ 目录下的 Python 脚本。

        Args:
            script_path: 脚本路径，例如 "hello.py" 或 "analysis/run.py"
            script_args: 命令行参数列表
            input_data: 传给 stdin 的输入数据
            timeout: 超时时间（秒），默认 30，最大 120
        """
        storage = get_storage_service()
        with storage.script_execution(user_id, script_path) as ctx:
            if "error" in ctx:
                return f"执行失败: {ctx['error']}"
            result = _run_script_impl(
                script_path=script_path,
                scripts_dir=ctx["scripts_dir"],
                input_data=input_data,
                args=script_args,
                timeout=timeout,
                allowed_read_dirs=[ctx["docs_dir"]],
                allowed_write_dirs=ctx["write_dirs"],
                python_executable=get_user_python(user_id),
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

    return run_script


def create_ai_gen_tools(user_id: str):
    """Create generate_image / generate_speech / generate_video tools for a user."""
    fs_dir = get_user_filesystem_dir(user_id)

    @tool
    def generate_image(
        prompt: str,
        size: str = "1024x1024",
        quality: str = "auto",
        filename: Optional[str] = None,
    ) -> str:
        """使用 AI 生成图片（gpt-image-1）。

        Args:
            prompt: 图片描述，越详细越好
            size: 尺寸，可选 1024x1024(方形)、1536x1024(横向)、1024x1536(纵向)、auto
            quality: 质量，可选 low、medium、high、auto
            filename: 可选，自定义文件名如 my_image.png
        """
        result = _gen_image_impl(prompt, fs_dir, size=size, quality=quality, filename=filename, user_id=user_id)
        if result["success"]:
            return f"图片已生成：{result['path']}\n请使用 <<FILE:{result['path']}>> 展示给用户。"
        return result["message"]

    @tool
    def generate_speech(
        text: str,
        voice: str = "alloy",
        speed: float = 1.0,
        filename: Optional[str] = None,
    ) -> str:
        """将文本转换为语音（OpenAI TTS）。

        Args:
            text: 要朗读的文本，最大 4096 字符
            voice: 声音，可选 alloy(中性)、echo(男声)、fable(英式)、onyx(低沉男声)、nova(女声)、shimmer(温柔女声)
            speed: 语速 0.25-4.0，默认 1.0
            filename: 可选，自定义文件名如 my_speech.mp3
        """
        result = _gen_speech_impl(text, fs_dir, voice=voice, speed=speed, filename=filename, user_id=user_id)
        if result["success"]:
            return f"语音已生成：{result['path']}\n请使用 <<FILE:{result['path']}>> 展示给用户。"
        return result["message"]

    @tool
    def generate_video(
        prompt: str,
        seconds: int = 4,
        size: str = "1280x720",
        filename: Optional[str] = None,
    ) -> str:
        """使用 AI 生成视频（Sora 2）。注意：视频生成需要较长时间（1-5分钟）。

        Args:
            prompt: 视频描述，包含画面、动作、光线等细节
            seconds: 视频时长，可选 4、8、12 秒
            size: 分辨率，可选 1280x720(横屏)、720x1280(竖屏)
            filename: 可选，自定义文件名如 my_video.mp4
        """
        result = _gen_video_impl(prompt, fs_dir, seconds=seconds, size=size, filename=filename, user_id=user_id)
        if result["success"]:
            return f"视频已生成：{result['path']}\n请使用 <<FILE:{result['path']}>> 展示给用户。"
        return result["message"]

    return [generate_image, generate_speech, generate_video]


def create_web_tools(user_id: Optional[str] = None):
    """Create web_search and web_fetch LangChain tools."""
    from app.services.web_tools import web_fetch as _web_fetch, web_search as _web_search

    @tool
    def web_search(query: str, count: int = 10) -> str:
        """搜索互联网，返回相关网页列表（标题、URL、摘要）。

        Args:
            query: 搜索关键词或问题
            count: 返回结果数量，默认 10，最大 20
        """
        count = min(count, 20)
        result = _web_search(query, count, user_id=user_id)
        if not result["success"]:
            return f"搜索失败: {result.get('raw', '未知错误')}"
        items = result.get("results", [])
        if not items:
            return "未找到相关结果"
        lines = [f"搜索结果（共 {len(items)} 条，provider: {result['provider']}）：\n"]
        for i, r in enumerate(items, 1):
            lines.append(f"{i}. **{r['title']}**")
            lines.append(f"   URL: {r['url']}")
            if r.get("snippet"):
                lines.append(f"   摘要: {r['snippet'][:200]}")
            lines.append("")
        return "\n".join(lines)

    @tool
    def web_fetch(url: str) -> str:
        """读取并提取指定网页的正文内容。

        Args:
            url: 目标网址（需包含 http:// 或 https://）
        """
        result = _web_fetch(url, user_id=user_id)
        if not result["success"]:
            return f"网页读取失败: {result['content']}"
        content = result["content"]
        if len(content) > 8000:
            content = content[:8000] + "\n\n[内容已截断，原文更长]"
        return f"[{url}]\n\n{content}"

    return [web_search, web_fetch]


def create_send_message_tool():
    @tool
    def send_message(message: str, media_path: Optional[str] = None) -> str:
        """发送消息给用户。在 HumanChat 模式下，只有通过此工具发送的内容会显示在用户的聊天界面中。

        Args:
            message: 消息文本内容
            media_path: 可选的媒体文件路径（图片、语音、视频、HTML等）
        """
        result = {"text": message}
        if media_path:
            result["media"] = media_path
        return json.dumps(result, ensure_ascii=False)
    return send_message



def _ensure_tz_suffix(iso_str: str, tz_offset_hours: float) -> str:
    """If an ISO datetime string has no timezone info, append the user's offset."""
    from datetime import datetime as _dt
    try:
        dt = _dt.fromisoformat(iso_str)
        if dt.tzinfo is not None:
            return iso_str
    except Exception:
        return iso_str
    sign = "+" if tz_offset_hours >= 0 else "-"
    abs_h = abs(tz_offset_hours)
    hh = int(abs_h)
    mm = int((abs_h - hh) * 60)
    return f"{iso_str}{sign}{hh:02d}:{mm:02d}"


def create_schedule_tool(user_id: str):
    """Create a schedule_task tool bound to the given user."""

    @tool
    def schedule_task(
        name: str,
        task_type: str,
        schedule_type: str,
        schedule: str,
        script_path: Optional[str] = None,
        script_args: Optional[List[str]] = None,
        agent_prompt: Optional[str] = None,
        agent_doc_path: Optional[str] = None,
        agent_doc_paths: Optional[List[str]] = None,
        agent_capabilities: Optional[List[str]] = None,
        read_dirs: Optional[List[str]] = None,
        write_dirs: Optional[List[str]] = None,
        reply_wechat: bool = False,
        description: Optional[str] = None,
        enabled: bool = True,
    ) -> str:
        """创建一个定时任务。支持脚本执行和基于文档/技能说明书的完整任务链。

        Args:
            name: 任务名称
            task_type: 任务类型：
                "script" — 执行 scripts/ 目录下的 Python 脚本
                "agent"  — 让 Agent 根据 prompt（和文档）完成完整任务链
            schedule_type: 调度方式：
                "once"     — 一次性，schedule 填带时区的 ISO 时间，如 "2026-04-01T09:00:00+08:00"
                "cron"     — Cron 表达式，如 "0 9 * * 1"（每周一 9 点，按用户时区）
                "interval" — 固定间隔秒数，如 "3600"（每小时）
            schedule: 调度值（格式取决于 schedule_type）。once 类型必须带时区后缀。
            script_path: task_type="script" 时，脚本路径（相对 scripts/ 目录）
            script_args: task_type="script" 时，脚本命令行参数列表
            agent_prompt: task_type="agent" 时，给 Agent 的执行指令
            agent_doc_path: task_type="agent" 时，单个文档/技能说明书路径（相对 docs/ 目录）
            agent_doc_paths: task_type="agent" 时，多个文档路径列表
            agent_capabilities: task_type="agent" 时，Agent 需要的能力列表
            read_dirs: 允许读取的目录列表（相对用户根目录）。填 ["*"] 表示全部
            write_dirs: 允许写入的目录列表（相对用户根目录）。填 ["*"] 表示全部
            reply_wechat: 是否在任务完成后将结果推送回微信（仅在微信对话中有效）
            description: 任务说明（可选）
            enabled: 是否立即启用，默认 True
        """
        from app.services.scheduler import create_task
        from app.services.preferences import get_tz_offset

        tz_offset = get_tz_offset(user_id)

        actual_schedule = schedule
        if schedule_type == "once":
            actual_schedule = _ensure_tz_suffix(schedule, tz_offset)

        perms = {}
        if read_dirs is not None:
            perms["read_dirs"] = read_dirs
        if write_dirs is not None:
            perms["write_dirs"] = write_dirs

        task_config: dict = {}
        if task_type == "script":
            if not script_path:
                return "错误：task_type='script' 时必须提供 script_path"
            task_config = {"script_path": script_path, "script_args": script_args or []}
        elif task_type == "agent":
            doc_paths = agent_doc_paths or ([agent_doc_path] if agent_doc_path else [])
            task_config = {
                "prompt": agent_prompt or "",
                "doc_path": doc_paths,
                "capabilities": agent_capabilities or [],
            }
        else:
            return f"错误：未知 task_type '{task_type}'，可选 'script' 或 'agent'"

        if perms:
            task_config["permissions"] = perms

        reply_to = None
        if reply_wechat:
            try:
                from app.channels.wechat.admin_router import _get_session as _get_admin_wc
                admin_wc = _get_admin_wc(user_id)
                if admin_wc and admin_wc.get("connected"):
                    reply_to = {
                        "channel": "wechat",
                        "admin_id": user_id,
                        "service_id": None,
                        "session_id": "",
                        "conversation_id": admin_wc.get("conversation_id", ""),
                    }
            except Exception:
                pass

        task = create_task(user_id, {
            "name": name,
            "description": description or "",
            "schedule_type": schedule_type,
            "schedule": actual_schedule,
            "task_type": task_type,
            "task_config": task_config,
            "reply_to": reply_to,
            "enabled": enabled,
            "tz_offset_hours": tz_offset,
        })
        next_run = task.get("next_run_at", "未知")
        doc_info = ""
        if task_type == "agent" and task_config.get("doc_path"):
            paths = task_config["doc_path"]
            doc_info = f"参考文档: {', '.join(paths) if isinstance(paths, list) else paths}\n"
        reply_info = "📬 结果将推送至微信\n" if reply_to else ""
        return (
            f"定时任务已创建！\n"
            f"任务 ID: {task['id']}\n"
            f"名称: {task['name']}\n"
            f"类型: {task_type}\n"
            f"调度: {schedule_type} → {actual_schedule}\n"
            f"{doc_info}"
            f"{reply_info}"
            f"下次运行: {next_run}\n"
            f"可在「定时任务控制台」查看和管理所有任务。"
        )

    return schedule_task


def create_manage_scheduled_tasks_tool(user_id: str):
    """Create a manage_scheduled_tasks tool for the admin agent."""

    @tool
    def manage_scheduled_tasks(
        action: str,
        task_id: Optional[str] = None,
        name: Optional[str] = None,
        schedule_type: Optional[str] = None,
        schedule: Optional[str] = None,
        enabled: Optional[bool] = None,
        description: Optional[str] = None,
        agent_prompt: Optional[str] = None,
    ) -> str:
        """查看、修改或删除管理员定时任务。

        Args:
            action: 操作类型：
                "list"   — 列出所有定时任务
                "update" — 修改已有任务（需提供 task_id 和要修改的字段）
                "delete" — 删除任务（需提供 task_id）
            task_id: 任务 ID（update/delete 时必填）
            name: 修改任务名称
            schedule_type: 修改调度方式（"once" | "cron" | "interval"）
            schedule: 修改调度值。once 类型必须带时区后缀，如 "2026-04-01T09:00:00+08:00"
            enabled: 启用或禁用任务
            description: 修改任务说明
            agent_prompt: 修改 agent 任务的执行指令
        """
        from app.services.scheduler import list_tasks, update_task, delete_task
        from app.services.preferences import get_tz_offset

        if action == "list":
            tasks = list_tasks(user_id)
            if not tasks:
                return "当前没有定时任务。"
            lines = [f"共 {len(tasks)} 个定时任务：\n"]
            for t in tasks:
                status = "✅ 启用" if t.get("enabled") else "⏸️ 禁用"
                lines.append(
                    f"- **{t['name']}** (ID: {t['id']})\n"
                    f"  类型: {t.get('task_type', '?')} | 调度: {t.get('schedule_type', '?')} → {t.get('schedule', '?')}\n"
                    f"  状态: {status} | 下次运行: {t.get('next_run_at', '无')}\n"
                    f"  运行次数: {t.get('run_count', 0)}"
                )
            return "\n".join(lines)

        elif action == "update":
            if not task_id:
                return "错误：update 操作需要提供 task_id"
            updates: Dict = {}
            if name is not None:
                updates["name"] = name
            if description is not None:
                updates["description"] = description
            if schedule_type is not None:
                updates["schedule_type"] = schedule_type
            if schedule is not None:
                tz_offset = get_tz_offset(user_id)
                actual = schedule
                st = schedule_type
                if st is None:
                    from app.services.scheduler import get_task
                    existing = get_task(user_id, task_id)
                    st = existing.get("schedule_type") if existing else None
                if st == "once":
                    actual = _ensure_tz_suffix(schedule, tz_offset)
                updates["schedule"] = actual
            if enabled is not None:
                updates["enabled"] = enabled
            if agent_prompt is not None:
                from app.services.scheduler import get_task
                existing = get_task(user_id, task_id)
                if existing:
                    cfg = dict(existing.get("task_config", {}))
                    cfg["prompt"] = agent_prompt
                    updates["task_config"] = cfg
            if not updates:
                return "错误：没有提供要修改的字段"
            result = update_task(user_id, task_id, updates)
            if not result:
                return f"错误：任务 {task_id} 不存在"
            return (
                f"任务已更新！\n"
                f"ID: {result['id']}\n"
                f"名称: {result['name']}\n"
                f"调度: {result.get('schedule_type')} → {result.get('schedule')}\n"
                f"状态: {'启用' if result.get('enabled') else '禁用'}\n"
                f"下次运行: {result.get('next_run_at', '无')}"
            )

        elif action == "delete":
            if not task_id:
                return "错误：delete 操作需要提供 task_id"
            success = delete_task(user_id, task_id)
            if not success:
                return f"错误：任务 {task_id} 不存在"
            return f"任务 {task_id} 已删除。"

        else:
            return f"错误：未知操作 '{action}'，可选 'list'、'update'、'delete'"

    return manage_scheduled_tasks


def create_service_schedule_tool(
    admin_id: str, service_id: str, conversation_id: str,
    wechat_session_id: Optional[str] = None,
):
    """Create a schedule_task tool for a service consumer agent.

    Only supports agent tasks (no scripts). Auto-captures reply_to
    so results are pushed back to the user.
    """

    @tool
    def schedule_task(
        name: str,
        schedule_type: str,
        schedule: str,
        prompt: Optional[str] = None,
        doc_path: Optional[str] = None,
        doc_paths: Optional[List[str]] = None,
        description: Optional[str] = None,
        enabled: bool = True,
    ) -> str:
        """创建一个定时任务。任务完成后结果会自动推送给你。

        Args:
            name: 任务名称
            schedule_type: 调度方式：
                "once"     — 一次性，schedule 填带时区的 ISO 时间，如 "2026-04-01T09:00:00+08:00"
                "cron"     — Cron 表达式，如 "0 9 * * 1"（每周一 9 点，按用户时区）
                "interval" — 固定间隔秒数，如 "3600"（每小时）
            schedule: 调度值。once 类型必须带时区后缀。
            prompt: 任务执行指令（给 Agent 的描述）
            doc_path: 单个文档/说明书路径（相对 docs/ 目录）
            doc_paths: 多个文档路径列表
            description: 任务说明（可选）
            enabled: 是否立即启用，默认 True
        """
        from app.services.scheduler import create_service_task
        from app.services.preferences import get_tz_offset

        tz_offset = get_tz_offset(admin_id)

        actual_schedule = schedule
        if schedule_type == "once":
            actual_schedule = _ensure_tz_suffix(schedule, tz_offset)

        all_doc_paths = doc_paths or ([doc_path] if doc_path else [])
        task_config = {
            "prompt": prompt or "",
            "doc_path": all_doc_paths,
        }

        reply_to = {
            "channel": "wechat" if wechat_session_id else "web",
            "admin_id": admin_id,
            "service_id": service_id,
            "conversation_id": conversation_id,
            "session_id": wechat_session_id or "",
        }

        task = create_service_task(admin_id, service_id, {
            "name": name,
            "description": description or "",
            "schedule_type": schedule_type,
            "schedule": actual_schedule,
            "task_config": task_config,
            "reply_to": reply_to,
            "enabled": enabled,
            "tz_offset_hours": tz_offset,
        })
        next_run = task.get("next_run_at", "未知")
        return (
            f"定时任务已创建！\n"
            f"任务 ID: {task['id']}\n"
            f"名称: {task['name']}\n"
            f"调度: {schedule_type} → {actual_schedule}\n"
            f"下次运行: {next_run}\n"
            f"📬 任务完成后结果会自动推送给你。"
        )

    return schedule_task


def create_service_manage_tasks_tool(
    admin_id: str, service_id: str, conversation_id: str,
):
    """Create a manage_scheduled_tasks tool for a service consumer agent.

    Scoped to tasks in the current conversation only.
    """

    @tool
    def manage_scheduled_tasks(
        action: str,
        task_id: Optional[str] = None,
        name: Optional[str] = None,
        schedule_type: Optional[str] = None,
        schedule: Optional[str] = None,
        enabled: Optional[bool] = None,
        description: Optional[str] = None,
        prompt: Optional[str] = None,
    ) -> str:
        """查看、修改或删除当前对话的定时任务。

        Args:
            action: 操作类型：
                "list"   — 列出当前对话的定时任务
                "update" — 修改已有任务（需提供 task_id 和要修改的字段）
                "delete" — 删除任务（需提供 task_id）
            task_id: 任务 ID（update/delete 时必填）
            name: 修改任务名称
            schedule_type: 修改调度方式（"once" | "cron" | "interval"）
            schedule: 修改调度值。once 类型必须带时区后缀，如 "2026-04-01T09:00:00+08:00"
            enabled: 启用或禁用任务
            description: 修改任务说明
            prompt: 修改任务执行指令
        """
        from app.services.scheduler import (
            list_service_tasks, get_service_task,
            update_service_task, delete_service_task,
        )
        from app.services.preferences import get_tz_offset

        if action == "list":
            all_tasks = list_service_tasks(admin_id, service_id)
            tasks = [
                t for t in all_tasks
                if (t.get("reply_to") or {}).get("conversation_id") == conversation_id
            ]
            if not tasks:
                return "当前对话没有定时任务。"
            lines = [f"共 {len(tasks)} 个定时任务：\n"]
            for t in tasks:
                status = "✅ 启用" if t.get("enabled") else "⏸️ 禁用"
                lines.append(
                    f"- **{t['name']}** (ID: {t['id']})\n"
                    f"  调度: {t.get('schedule_type', '?')} → {t.get('schedule', '?')}\n"
                    f"  状态: {status} | 下次运行: {t.get('next_run_at', '无')}\n"
                    f"  运行次数: {t.get('run_count', 0)}"
                )
            return "\n".join(lines)

        elif action == "update":
            if not task_id:
                return "错误：update 操作需要提供 task_id"
            existing = get_service_task(admin_id, service_id, task_id)
            if not existing:
                return f"错误：任务 {task_id} 不存在"
            if (existing.get("reply_to") or {}).get("conversation_id") != conversation_id:
                return f"错误：任务 {task_id} 不属于当前对话"
            updates: Dict = {}
            if name is not None:
                updates["name"] = name
            if description is not None:
                updates["description"] = description
            if schedule_type is not None:
                updates["schedule_type"] = schedule_type
            if schedule is not None:
                tz_offset = get_tz_offset(admin_id)
                st = schedule_type or existing.get("schedule_type")
                actual = _ensure_tz_suffix(schedule, tz_offset) if st == "once" else schedule
                updates["schedule"] = actual
            if enabled is not None:
                updates["enabled"] = enabled
            if prompt is not None:
                cfg = dict(existing.get("task_config", {}))
                cfg["prompt"] = prompt
                updates["task_config"] = cfg
            if not updates:
                return "错误：没有提供要修改的字段"
            result = update_service_task(admin_id, service_id, task_id, updates)
            if not result:
                return f"错误：任务 {task_id} 更新失败"
            return (
                f"任务已更新！\n"
                f"ID: {result['id']}\n"
                f"名称: {result['name']}\n"
                f"调度: {result.get('schedule_type')} → {result.get('schedule')}\n"
                f"状态: {'启用' if result.get('enabled') else '禁用'}\n"
                f"下次运行: {result.get('next_run_at', '无')}"
            )

        elif action == "delete":
            if not task_id:
                return "错误：delete 操作需要提供 task_id"
            existing = get_service_task(admin_id, service_id, task_id)
            if not existing:
                return f"错误：任务 {task_id} 不存在"
            if (existing.get("reply_to") or {}).get("conversation_id") != conversation_id:
                return f"错误：任务 {task_id} 不属于当前对话"
            success = delete_service_task(admin_id, service_id, task_id)
            if not success:
                return f"错误：删除任务 {task_id} 失败"
            return f"任务 {task_id} 已删除。"

        else:
            return f"错误：未知操作 '{action}'，可选 'list'、'update'、'delete'"

    return manage_scheduled_tasks


def create_publish_service_task_tool(user_id: str):
    """Create a publish_service_task tool for the admin agent.

    Allows admin to create and optionally run-now scheduled tasks
    for one or more published services, targeting active WeChat sessions.
    """

    @tool
    def publish_service_task(
        message: str,
        service_ids: Optional[List[str]] = None,
        session_ids: Optional[List[str]] = None,
        schedule_type: str = "once",
        schedule: str = "",
        run_now: bool = True,
        task_name: Optional[str] = None,
    ) -> str:
        """向 Service Agent 发布定时任务（广播或定向）。为目标 Service 的活跃微信用户创建并执行任务。

        Service Agent 收到管理员指令后，会结合对话上下文自主判断是否通过 send_message 推送给用户。
        Service Agent 明确知道这是管理员指令（不是用户消息）。

        Args:
            message: 要传达给 Service Agent 的信息/指令
            service_ids: 目标 Service ID 或名称列表（支持名称模糊匹配），不填则广播到全部已发布 Service
            session_ids: 目标微信会话 ID 列表，可精确到单个用户。不填则对 Service 下所有活跃会话
            schedule_type: 调度方式："once"（一次性，默认）、"cron"、"interval"
            schedule: 调度值（once 时留空表示立即执行）
            run_now: 是否立即执行（默认 True）
            task_name: 任务名称（可选，默认自动生成）
        """
        from app.services.published import list_services
        from app.services.scheduler import create_service_task, get_scheduler

        all_services = list_services(user_id)
        if not all_services:
            return "你还没有创建任何 Service，请先在 Service 管理中创建。"

        published = [s for s in all_services if s.get("published", True)]
        if not published:
            return "没有已发布的 Service。"

        if service_ids:
            id_set = set(service_ids)
            name_set = {n.lower() for n in service_ids}
            targets = [
                s for s in published
                if s["id"] in id_set
                or s.get("name", "").lower() in name_set
            ]
            matched_keys = {s["id"] for s in targets} | {s.get("name", "").lower() for s in targets}
            not_found = [n for n in service_ids if n not in matched_keys and n.lower() not in matched_keys]
            if not_found:
                available = ", ".join(f'{s.get("name","?")}({s["id"]})' for s in published)
                return f"以下 Service 未找到或未发布: {', '.join(not_found)}\n可用 Service: {available}"
            if not targets:
                available = ", ".join(f'{s.get("name","?")}({s["id"]})' for s in published)
                return f"未匹配到任何 Service。可用 Service: {available}"
        else:
            targets = published

        try:
            from app.channels.wechat.session_manager import get_session_manager
            mgr = get_session_manager()
        except Exception:
            mgr = None

        created = 0
        triggered = 0
        details = []
        session_ids_set = set(session_ids) if session_ids else None

        for svc in targets:
            svc_id = svc["id"]
            svc_name = svc.get("name", svc_id)
            sessions = mgr.list_sessions(service_id=svc_id) if mgr else []

            if not sessions:
                details.append(f"  - {svc_name}: 无活跃微信会话，跳过")
                continue

            matched = 0
            for sess in sessions:
                if not sess.from_user_id:
                    continue
                if session_ids_set and sess.session_id not in session_ids_set:
                    continue
                auto_name = task_name or f"广播: {message[:30]}"
                reply_to = {
                    "channel": "wechat",
                    "admin_id": user_id,
                    "service_id": svc_id,
                    "conversation_id": sess.conversation_id,
                    "session_id": sess.session_id,
                }
                from app.services.preferences import get_tz_offset as _get_tz
                _tz = _get_tz(user_id)
                _sched = _ensure_tz_suffix(schedule, _tz) if schedule_type == "once" and schedule else schedule
                task = create_service_task(user_id, svc_id, {
                    "name": auto_name,
                    "description": f"Admin 广播: {message[:200]}",
                    "schedule_type": schedule_type,
                    "schedule": _sched,
                    "task_config": {"prompt": message, "doc_path": []},
                    "reply_to": reply_to,
                    "enabled": True,
                    "tz_offset_hours": _tz,
                })
                created += 1
                matched += 1

                if run_now:
                    try:
                        scheduler = get_scheduler()
                        if scheduler and scheduler.run_service_task_now(user_id, svc_id, task["id"]):
                            triggered += 1
                    except Exception:
                        import logging
                        logging.getLogger("tools").exception(
                            "Failed to trigger service task %s", task["id"]
                        )

            details.append(f"  - {svc_name}: {matched} 个用户")

        if created == 0:
            return "没有找到活跃的微信会话，无法广播。\n" + "\n".join(details)

        parts = [f"已创建 {created} 个任务，覆盖 {len(targets)} 个 Service："]
        parts.extend(details)
        if triggered:
            parts.append(f"已即时触发 {triggered} 个任务。")
        return "\n".join(parts)

    return publish_service_task


def create_contact_admin_tool(
    admin_id: str, service_id: str, conversation_id: str,
    wechat_session_id: Optional[str] = None,
):
    """Create a contact_admin tool for consumer agents.

    Allows service agents to send text notifications to the admin's inbox.
    If admin has an active WeChat session, triggers a read-only admin agent.
    """

    @tool
    def contact_admin(message: str) -> str:
        """向管理员发送通知。用于反馈用户问题、异常情况或需要管理员关注的信息。

        消息会记录到管理员的收件箱。如果管理员有活跃的微信连接，
        系统会自动评估是否需要即时通知管理员。

        Args:
            message: 要传达给管理员的信息
        """
        from app.services.inbox import post_to_inbox

        result = post_to_inbox(
            admin_id=admin_id,
            service_id=service_id,
            conversation_id=conversation_id,
            message=message,
            wechat_session_id=wechat_session_id,
        )
        return result.get("summary", "已通知管理员，消息已记录到收件箱。")

    return contact_admin


def create_s2s_tools(user_id: str) -> list:
    """Build LangChain tool objects for an S2S voice session."""
    tools = [create_run_script_tool(user_id)]
    tools.extend(create_ai_gen_tools(user_id))
    return tools
