"""前台 Copilot Agent —— 低延迟对话 + 路由决策(闲聊直答 / 委派重活)。

这是「上层对话框架」:一个快速 LLM 直接处理寒暄与澄清;遇到需要查资料、
读写文档、跑脚本、多步任务时,调用 ``delegate_to_jellyfish`` 把活儿交给后台的
OpenJellyfish agent(经 Core 远程 SSE),并在等待期间用填充语/进度播报维持「活人感」。
"""

from __future__ import annotations

from livekit.agents import Agent, RunContext, function_tool

from .bridge import stream_delegate
from .config import VoiceSession
from .orchestration import Orchestrator


class _CopilotAgent(Agent):
    """前台 Agent;额外净化 STT 事件时间戳。

    某些 STT 插件(如 aliyun Paraformer)会发出 ``start_time/end_time=None``
    的 ``SpeechData``,而 LiveKit 内部 ``_on_stt_event`` 会做 ``end_time > 0``
    比较 → ``TypeError: '>' not supported between 'NoneType' and 'int'`` 并崩溃。
    这里在官方 ``stt_node`` 钩子里把 None 时间戳兜底成 0.0(provider 无关)。
    """

    async def stt_node(self, audio, model_settings):  # type: ignore[override]
        node = Agent.default.stt_node(self, audio, model_settings)
        if node is None:
            return
        async for ev in node:
            alts = getattr(ev, "alternatives", None) or []
            for alt in alts:
                if getattr(alt, "start_time", 0.0) is None:
                    alt.start_time = 0.0
                if getattr(alt, "end_time", 0.0) is None:
                    alt.end_time = 0.0
            yield ev


def build_copilot(sess: VoiceSession, orch: Orchestrator) -> Agent:
    """构造前台 Copilot Agent,委派工具闭包持有会话与编排器。"""

    @function_tool
    async def delegate_to_jellyfish(context: RunContext, instruction: str) -> str:
        """把需要查资料、读写文档、运行脚本或多步推理的任务交给后台执行。

        适用:检索网络/文档、生成或修改文件、跑脚本、需要规划的复杂任务。
        不适用:寒暄、澄清、对已知信息的简单复述(这些直接口头回答即可)。

        Args:
            instruction: 要交办的完整任务描述(用用户原话或更明确的改写)。
        """
        orch.remember_delegation(instruction)

        # 立刻说一句承接语,避免委派期间冷场(enqueue,非阻塞)。
        filler = orch.pick_filler("delegating")
        if filler:
            context.session.say(filler, allow_interruptions=True)

        final_parts: list[str] = []
        async for ev in stream_delegate(sess.bridge_token, instruction):
            etype = ev.get("type")
            if etype == "token":
                txt = ev.get("content") or ""
                final_parts.append(txt)
                orch.accumulate_answer(txt)
            elif etype in ("tool_call", "subagent_call", "subagent_start", "subagent_end"):
                phrase = orch.narration_for(ev)
                if phrase:
                    context.session.say(phrase, allow_interruptions=True)
            elif etype == "error":
                return f"后台执行遇到问题:{ev.get('content', '')}。可以稍后再试或换种说法。"
            elif etype == "done":
                break

        answer = "".join(final_parts).strip()
        return answer or "任务已完成,但后台没有返回可朗读的文本。"

    instructions = sess.system_prompt or "你是用户的实时语音助手,回答简洁、口语化。"
    return _CopilotAgent(instructions=instructions, tools=[delegate_to_jellyfish])
