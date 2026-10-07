"""Public data contract for the architecture exploration capability."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


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


class ExplorationPort(Protocol):
    async def explore(self, demand: ExplorationDemand) -> ExplorationReport:
        """Run graph-guided exploration only after the main Agent requests it."""
