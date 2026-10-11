"""Deployment values used across plugin boundaries, without importing Settings."""
from typing import Mapping, Protocol


class PluginConfiguration(Protocol):
    strict_production: bool
    agent_design_contract_enabled: bool
    agent_orchestration_enabled: bool
    agent_knowledge_graph_enabled: bool
    agent_memory_recall_top_k: int
    agent_memory_recall_max_tokens: int
    agent_context_soft_limit_tokens: int
    agent_context_hard_limit_tokens: int
    agent_max_run_seconds: int
    agent_max_tool_calls: int
    agent_architecture_scheduling_max_workers: int
    agent_architecture_scheduling_total_tokens: int
    agent_architecture_scheduling_worker_seconds: float
    project_dir: str
    runtime_dir: str
    workspace_roots: str
    plugin_configs: Mapping[str, Mapping[str, object]]
