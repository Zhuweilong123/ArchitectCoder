"""BaseAgents Framework — 轻量级 Agent 框架

分层解耦、职责单一、接口统一。

架构:
- core/     : 核心基础设施 (LLM、Message、AgentConfig、Agent基类、异常)
- agents/   : ReAct、PlanAndSolve Agent 范式
- tools/    : 工具基类、注册表、异步工具与执行契约

Usage::

    from app.agent_base import BaseAgentsLLM, AgentConfig, ReActAgent

    llm = BaseAgentsLLM()
    agent = ReActAgent(name="助手", llm=llm, tool_registry=ToolRegistry())
    response = agent.run("你好！")
"""

from .core.exceptions import (
    BaseAgentsException, ConfigError, LLMError, AgentError, ToolError,
)
from backend.config import AgentConfig
from .core.message import Message, MessageRole
from .core.llm import BaseAgentsLLM
from .core.agent import Agent
from .core.knowledge_graph import (
    KnowledgeGraphProvider,
    NoOpKnowledgeGraphProvider,
    load_knowledge_graph,
)
from .core.plugins import (
    PluginManager,
    PluginSpec,
    PluginState,
    get_plugin_manager,
)

from .agents import ReActAgent, PlanAndSolveAgent

from .tools import (
    Tool,
    ToolParameter,
    ToolRegistry,
    AsyncTool,
)
from .execution import ToolExecutor

__all__ = [
    # core
    "BaseAgentsException", "ConfigError", "LLMError", "AgentError", "ToolError",
    "AgentConfig",
    "Message", "MessageRole",
    "BaseAgentsLLM",
    "Agent",
    "KnowledgeGraphProvider",
    "NoOpKnowledgeGraphProvider",
    "load_knowledge_graph",
    "PluginManager",
    "PluginSpec",
    "PluginState",
    "get_plugin_manager",
    # agents
    "ReActAgent",
    "PlanAndSolveAgent",
    # tools
    "Tool", "ToolParameter",
    "ToolRegistry",
    "AsyncTool",
    "ToolExecutor",
]
