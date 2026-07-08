"""OpenJellyfish 语音前台插件(LiveKit Agents Worker)。

作为 OpenJellyfish 之上的实时语音「上层框架」,与任务引擎解耦:
对话进程(本 Worker) ↔ 任务引擎(Core 经 SSE)。详见各模块 docstring 与 README。
"""

__version__ = "0.1.0"
