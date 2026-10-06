"""Architecture-aware scheduling provider."""

from __future__ import annotations

from app.agent_base.core.orchestration import (
    OrchestrationPreparation,
)


def list_contributions(*, settings=None):
    from .architecture_aware.routing_checkpoint import list_contributions as declarations
    return declarations(settings=settings)


class UnavailableArchitectureScheduler:
    """Explicit no-op result when graph-guided scheduling cannot be offered."""

    def __init__(self, reason: str):
        self.reason = reason

    async def prepare(self, _request) -> OrchestrationPreparation:
        return OrchestrationPreparation(
            excluded_tools=("route_architecture", "explore_architecture"),
            phase="unavailable",
            metadata={
                "architecture_scheduling": "unavailable",
                "architecture_scheduling_reason": self.reason,
            },
        )

    def create_tools(self, **_kwargs):
        return []


def create(
    *,
    llm,
    settings,
    project_file: str = "",
    source_dir: str = "",
    test_dir: str = "",
    explorer_factory=None,
    **kwargs,
):
    """Provider factory used by ``load_orchestrator``."""

    if not getattr(settings, "agent_knowledge_graph_enabled", False):
        return UnavailableArchitectureScheduler("knowledge graph is disabled")
    if not project_file:
        return UnavailableArchitectureScheduler("project file is unavailable")
    if not explorer_factory:
        return UnavailableArchitectureScheduler("read-only explorer is unavailable")

    try:
        from .architecture_aware import ArchitectureAwareOrchestrator

        return ArchitectureAwareOrchestrator(
            llm=llm,
            settings=settings,
            project_file=project_file,
            source_dir=source_dir,
            test_dir=test_dir,
            explorer_factory=explorer_factory,
        )
    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning(
            "[ArchitectureScheduling] initialization failed; using single-agent path: %s",
            exc,
            exc_info=True,
        )
        return UnavailableArchitectureScheduler(
            f"architecture scheduler initialization failed: {type(exc).__name__}: {exc}"
        )
