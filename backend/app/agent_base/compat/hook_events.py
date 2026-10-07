"""Historical stage names accepted when reading configuration and trace data.

These names do not define stages or select when a plugin capability executes.
New code and plugin declarations must use the canonical lifecycle names.
"""
from types import MappingProxyType


LEGACY_STAGE_ALIASES = MappingProxyType({
    "llm_before": "model_before",
    "llm_after": "model_after",
    "run_finalize": "finalize",
    "agent_initialize": "initialize",
    "context_prepare": "prepare",
    "memory_reinforce": "prepare",
    "orchestration_prepare": "prepare",
    "task_archive": "finalize",
    "trace_initialize": "initialize",
    "skill_read": "tool_before",
    "orchestration_execute": "tool_before",
    "trace_query": "run_start",
    "trace_replay": "run_start",
    "evaluation_query": "run_start",
    "evaluation_run": "run_start",
    "evaluation_update": "run_start",
    "graph_query": "tool_before",
    "graph_update": "tool_before",
    "contract_collect": "finalize",
    "plugin_service": "run_start",
})


def resolve_legacy_event(event_type, value):
    """Convert only recognized legacy inputs to the supplied canonical enum."""
    if not isinstance(value, str):
        return None
    canonical = LEGACY_STAGE_ALIASES.get(value)
    return event_type(canonical) if canonical is not None else None
