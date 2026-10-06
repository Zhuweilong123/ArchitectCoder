"""Stable orchestration port owned by the Agent core.

The core deliberately knows nothing about graph storage or worker scheduling.
A provider may be installed through configuration, while ``NoOpOrchestrator``
keeps the single-Agent path fully functional when scheduling is disabled or
unavailable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OrchestrationRequest:
    user_message: str
    project_file: str = ""
    source_dir: str = ""
    test_dir: str = ""
    previous_checkpoint: dict[str, Any] = field(default_factory=dict)
    available_tools: tuple[str, ...] = ()
    run_id: str = ""


@dataclass(frozen=True)
class OrchestrationPreparation:
    """The only provider output consumed by the main Agent loop."""

    context_blocks: tuple[str, ...] = ()
    excluded_tools: tuple[str, ...] = ()
    phase: str = "disabled"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def context(self) -> str:
        return "\n\n".join(block for block in self.context_blocks if block)


@dataclass(frozen=True)
class ExplorationDemand:
    """A main Agent's bounded request for architecture-guided exploration."""

    goal: str
    search_queries: tuple[str, ...]
    run_id: str
    schedule_root_run_id: str = ""


@dataclass(frozen=True)
class ExplorationEvidence:
    """One finding whose coordinates fall inside a worker file-tool read."""

    path: str
    start_line: int
    end_line: int
    symbol: str
    behavior: str
    role: str


@dataclass(frozen=True)
class ExplorationFinding:
    """Evidence returned for one assigned read-only work item."""

    work_item: str
    status: str
    summary: str
    evidence: tuple[ExplorationEvidence, ...] = ()
    unresolved: tuple[str, ...] = ()
    excluded_candidates: tuple[str, ...] = ()
    child_run_id: str = ""
    node_ids: tuple[str, ...] = ()
    node_count: int = 0
    estimated_cost: float = 0.0
    tokens: int = 0
    token_budget: int = 0
    seconds: float = 0.0
    slot: int = -1
    grounded_excerpts: int = 0
    tool_evidence: bool = False


@dataclass(frozen=True)
class ExplorationReport:
    """Evidence and scheduling outcome returned to the requesting Agent."""

    status: str
    reason: str = ""
    next_step: str = ""
    schedule_id: str = ""
    schedule_root_run_id: str = ""
    schedule_revision: int = 0
    affected_nodes: int = 0
    impact_truncated: bool = False
    unmatched_queries: tuple[str, ...] = ()
    exploration_budget: int = 0
    partition_count: int = 0
    partition_objective: float = 0.0
    partition_max_load: float = 0.0
    partition_cut_weight: float = 0.0
    partition_conflict: float = 0.0
    pending_work_items: int = 0
    worker_tokens: int = 0
    findings: tuple[ExplorationFinding, ...] = ()


class OrchestrationPort(Protocol):
    async def prepare(self, request: OrchestrationRequest) -> OrchestrationPreparation:
        """Prepare optional scheduling context and tool boundaries."""


class ExplorationPort(Protocol):
    async def explore(self, demand: ExplorationDemand) -> ExplorationReport:
        """Run graph-guided exploration only after the main Agent requests it."""


class NoOpOrchestrator:
    """Zero-cost fallback preserving the single-Agent behavior."""

    async def prepare(self, _request: OrchestrationRequest) -> OrchestrationPreparation:
        return OrchestrationPreparation()


class _ResilientOrchestrator:
    """Contain provider runtime failures at the core/plugin boundary."""

    def __init__(self, provider: OrchestrationPort):
        self.provider = provider

    async def prepare(self, request: OrchestrationRequest) -> OrchestrationPreparation:
        try:
            result = await self.provider.prepare(request)
            if not isinstance(result, OrchestrationPreparation):
                raise TypeError("orchestrator prepare() returned an invalid result")
            return result
        except Exception as exc:
            logger.warning("[Orchestration] provider failed during prepare; using no-op", exc_info=True)
            return OrchestrationPreparation(
                excluded_tools=("route_architecture", "explore_architecture"),
                phase="unavailable",
                metadata={
                    "architecture_scheduling": "unavailable",
                    "architecture_scheduling_reason": (
                        f"provider preparation failed: {type(exc).__name__}: {exc}"
                    ),
                },
            )


def _load_factory(provider: str):
    """Compatibility hook; actual loading remains owned by PluginManager."""
    from .plugins import PluginManager

    return PluginManager._load_factory(provider)


def load_orchestrator(*, llm, settings, **kwargs) -> OrchestrationPort:
    """Load orchestration through the central extension manager."""
    from .plugins import get_plugin_manager

    instance = get_plugin_manager().load_optional(
        "orchestration",
        settings=settings,
        kwargs={"llm": llm, **kwargs},
        factory_loader=_load_factory,
    )
    if instance is None:
        return NoOpOrchestrator()
    return _ResilientOrchestrator(instance)


def exclude_tools(tool_names: list[str], excluded: tuple[str, ...] | list[str]) -> list[str]:
    excluded_set = set(excluded)
    return [name for name in tool_names if name not in excluded_set]
