"""BaseAgents Framework — 轻量级 Agent 框架

分层解耦、职责单一、接口统一。

架构:
- core/     : 核心基础设施 (LLM、Message、Config、Agent基类、异常)
- agents/   : 4 种 Agent 范式 (Simple、ReAct、Reflection、PlanAndSolve)
- tools/    : 工具系统 (Tool基类、ToolRegistry、ToolChain、AsyncToolExecutor)

Usage::

    from app.agent_base import BaseAgentsLLM, Config, SimpleAgent

    llm = BaseAgentsLLM()
    agent = SimpleAgent(name="助手", llm=llm)
    response = agent.run("你好！")
"""

from .core.exceptions import (
    BaseAgentsException, ConfigError, LLMError, AgentError, ToolError,
)
from .core.config import Config
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

from .agents import SimpleAgent, ReActAgent, ReflectionAgent, PlanAndSolveAgent

from .tools import Tool, ToolParameter, ToolRegistry, ToolChain, ToolChainManager, AsyncToolExecutor
from .execution import ToolExecutor

__all__ = [
    # core
    "BaseAgentsException", "ConfigError", "LLMError", "AgentError", "ToolError",
    "Config",
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
    "SimpleAgent",
    "ReActAgent",
    "ReflectionAgent",
    "PlanAndSolveAgent",
    # tools
    "Tool", "ToolParameter",
    "ToolRegistry",
    "ToolChain", "ToolChainManager",
    "AsyncToolExecutor",
    "ToolExecutor",
]
