"""
JellyfishBot — 可选的 Python 包（fcnAgent 等工具）

使用 deepagents 构建的 Agent 相关能力。

主要组件:
- fcnAgent.py: Agent 工厂和运行函数
- config_manager.py: 配置管理
- config/: 配置文件目录

使用方式:
    # 命令行运行
    python fcnAgent.py -i  # 交互模式
    python fcnAgent.py -q "你的问题"  # 直接问答

    # 作为模块导入（包名以实际安装名为准）
    from .fcnAgent import create_fcn_agent, run_agent_readonly
    agent = create_fcn_agent()
    result = run_agent_readonly(agent, "你的问题")
"""

from .fcnAgent import (
    create_fcn_agent,
    run_agent_readonly,
    extract_ai_response,
    create_dify_tools,
    build_subagents
)

from .config_manager import (
    ConfigManager,
    get_config
)

__all__ = [
    "create_fcn_agent",
    "run_agent_readonly",
    "extract_ai_response",
    "create_dify_tools",
    "build_subagents",
    "ConfigManager",
    "get_config",
]

__version__ = "1.0.0"
