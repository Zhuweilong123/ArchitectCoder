"""BaseAgents Framework — 轻量级 Agent 框架

分层解耦、职责单一、接口统一。

架构:
- core/     : 核心基础设施 (LLM、Message、AgentConfig、Agent基类、异常)
- host_api/ : 宿主生命周期、回调和编排协议
- adapters/ : 能力加载、降级和宿主适配
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
from app.agent_base.adapters.knowledge_graph import (NoOpKnowledgeGraphProvider, load_knowledge_graph)
from app.agent_base.host_api.contract_checks import (ContractCheckResult, ContractViolation)
from app.agent_base.host_api.contexts import (ContractFailureAnalysisContext, ContractFailureAnalyzerPort)
from app.agent_base.adapters.contract_analysis import (NoOpContractFailureAnalyzer, load_contract_failure_analyzer)
from app.agent_base.host_api.contexts import (ContractGateContext, ContractGateDecision, ContractGatePort)
from app.agent_base.adapters.contract_gate import (NoOpContractGate, load_contract_gate, resolve_contract_enabled)
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

# Bind plugin-facing capabilities once at the application composition root.
# The host API itself has no imports of core implementations.
from .adapters.host_services import ApplicationHostServices as _ApplicationHostServices
from .host_api.services import install_host_services as _install_host_services
_install_host_services(_ApplicationHostServices())

__all__ = [
    # core
    "BaseAgentsException", "ConfigError", "LLMError", "AgentError", "ToolError",
    "AgentConfig",
    "Message", "MessageRole",
    "BaseAgentsLLM",
    "Agent",
    "NoOpKnowledgeGraphProvider",
    "load_knowledge_graph",
    "ContractCheckResult",
    "ContractViolation",
    "ContractFailureAnalysisContext",
    "ContractFailureAnalyzerPort",
    "NoOpContractFailureAnalyzer",
    "load_contract_failure_analyzer",
    "ContractGateContext",
    "ContractGateDecision",
    "ContractGatePort",
    "NoOpContractGate",
    "load_contract_gate",
    "resolve_contract_enabled",
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
