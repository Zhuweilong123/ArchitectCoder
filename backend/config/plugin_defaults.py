"""Compatibility names backed by each extension's plugin.json."""

from .plugin_catalog import packaged_manifests

_PROVIDERS = {item["id"]: item["provider"] for item in packaged_manifests()}
DEFAULT_ORCHESTRATION_PROVIDER = _PROVIDERS.get("orchestration", "none")
DEFAULT_MEMORY_PROVIDER = _PROVIDERS.get("memory", "none")
DEFAULT_TRACE_PROVIDER = _PROVIDERS.get("trace", "none")
DEFAULT_EVALS_PROVIDER = _PROVIDERS.get("evals", "none")
DEFAULT_KNOWLEDGE_GRAPH_PROVIDER = _PROVIDERS.get("knowledge_graph", "none")
DEFAULT_DESIGN_CONTRACT_PROVIDER = _PROVIDERS.get("design_contract", "none")
DEFAULT_SKILLS_PROVIDER = _PROVIDERS.get("skills", "none")
