"""对话编排策略(填充语 / 进度播报节流 / 打断记忆 / 未来沉默判定)。

刻意**不依赖 LiveKit**——这是「活人感」的灵魂,做成可移植模块,将来即便把
LiveKit 换成别的实时框架,这层策略仍能复用。LiveKit 适配胶水只调用这里的纯函数。

职责:
- 按状态挑选填充语(委派/工具运行/长任务),并随机化避免重复;
- 进度播报节流(避免把后台每个 tool_call 都念出来,只在关键节点+冷却后开口);
- 打断记忆(记录「正说到哪」「正在委派什么」,被打断后可衔接);
- 多人沉默判定(场景B的雏形:带说话人标签时判断是否该开口)。
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional


# ── 进度事件 → 自然语言短句 ──────────────────────────────────────────

def describe_event(event: Dict) -> Optional[str]:
    """把一个后台事件翻译成一句可播报的进度短语;不值得播报则返回 None。"""
    t = event.get("type")
    if t == "tool_call":
        name = event.get("name", "")
        return _TOOL_NARRATION.get(name, "正在处理")
    if t == "subagent_call" or t == "subagent_start":
        return "正在安排一个子任务"
    if t == "subagent_end":
        return "子任务完成了"
    return None


_TOOL_NARRATION: Dict[str, str] = {
    "web_search": "正在联网搜索",
    "read_file": "正在查看文档",
    "write_file": "正在写入文件",
    "edit_file": "正在修改文档",
    "ls": "正在浏览目录",
    "glob": "正在查找文件",
    "grep": "正在检索内容",
    "run_script": "正在运行脚本",
}


@dataclass
class Orchestrator:
    """单通会话的编排状态机。"""

    fillers: Dict[str, List[str]] = field(default_factory=dict)
    # 进度播报最小间隔(秒):两次「正在……」之间至少隔这么久,避免絮叨。
    narration_cooldown: float = 6.0

    _last_narration_at: float = 0.0
    _recent_fillers: Dict[str, str] = field(default_factory=dict)
    # 打断记忆
    last_instruction: str = ""
    last_partial_answer: str = ""

    # ── 填充语 ──
    def pick_filler(self, state: str) -> Optional[str]:
        """为某状态挑一句填充语,尽量不与上一句重复。"""
        pool = self.fillers.get(state) or []
        if not pool:
            return None
        if len(pool) == 1:
            return pool[0]
        prev = self._recent_fillers.get(state)
        choices = [p for p in pool if p != prev] or pool
        choice = random.choice(choices)
        self._recent_fillers[state] = choice
        return choice

    # ── 进度播报节流 ──
    def should_narrate(self, now: Optional[float] = None) -> bool:
        now = now if now is not None else time.monotonic()
        if now - self._last_narration_at < self.narration_cooldown:
            return False
        self._last_narration_at = now
        return True

    def narration_for(self, event: Dict, now: Optional[float] = None) -> Optional[str]:
        """若该事件值得播报且已过冷却,返回播报短语,否则 None。"""
        phrase = describe_event(event)
        if not phrase:
            return None
        if not self.should_narrate(now):
            return None
        return phrase + "……"

    # ── 打断记忆 ──
    def remember_delegation(self, instruction: str) -> None:
        self.last_instruction = instruction
        self.last_partial_answer = ""

    def accumulate_answer(self, text: str) -> None:
        self.last_partial_answer += text

    def interruption_context(self) -> str:
        """被打断后,给前台 LLM 的衔接提示(它可据此说「刚才提到的那个…要继续吗」)。"""
        if not self.last_instruction:
            return ""
        ans = self.last_partial_answer.strip()
        tail = (ans[-200:] if ans else "")
        return (
            f"[被打断的上下文] 用户上一条委派:{self.last_instruction}。"
            + (f"已说到:…{tail}" if tail else "尚未开始回答。")
        )


# ── 多人沉默判定(场景B雏形)──────────────────────────────────────

def should_agent_speak(
    transcript: str,
    *,
    agent_name_aliases: Optional[List[str]] = None,
    primary_speaker_only: bool = True,
) -> bool:
    """多人 room 中,判断这段转写是否「在对 agent 说话」。

    一期保守策略:仅当显式点名(出现 agent 别名/唤醒词)时才开口;
    人际讨论默认沉默。带说话人标签(diarization)时可进一步细化。
    将来可换成轻量 LLM 的 addressee 判定。
    """
    aliases = agent_name_aliases or ["copilot", "助手", "助理", "jellyfish"]
    low = (transcript or "").lower()
    return any(a.lower() in low for a in aliases)
