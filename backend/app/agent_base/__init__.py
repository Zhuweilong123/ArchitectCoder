"""Framework facade: public types are loaded only when requested.

Domain providers enter through plugin slots, never through this package facade.
"""
from importlib import import_module
from .adapters.host_services import ApplicationHostServices
from .host_api.services import install_host_services

install_host_services(ApplicationHostServices())

_EXPORTS = {
    "BaseAgentsException": ".core.exceptions", "ConfigError": ".core.exceptions",
    "LLMError": ".core.exceptions", "AgentError": ".core.exceptions", "ToolError": ".core.exceptions",
    "AgentConfig": "backend.config", "Message": ".core.message", "MessageRole": ".core.message",
    "BaseAgentsLLM": ".core.llm", "Agent": ".core.agent",
    "PluginManager": ".core.plugins", "PluginSpec": ".core.plugins",
    "PluginState": ".core.plugins", "get_plugin_manager": ".core.plugins",
    "ReActAgent": ".agents", "PlanAndSolveAgent": ".agents", "Tool": ".tools",
    "ToolParameter": ".tools", "ToolRegistry": ".tools", "AsyncTool": ".tools", "ToolExecutor": ".execution",
}
__all__ = list(_EXPORTS)


def __getattr__(name):
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module, __name__), name)
    globals()[name] = value
    return value
