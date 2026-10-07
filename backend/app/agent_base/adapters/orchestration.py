"""Loading, fallback and validation adapters for optional capabilities."""

from __future__ import annotations
import logging

from app.agent_base.host_api.orchestration import OrchestrationRequest, OrchestrationPreparation, OrchestrationPort

logger = logging.getLogger(__name__)

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
    from app.agent_base.core.plugins import PluginManager

    return PluginManager._load_factory(provider)


def load_orchestrator(*, llm, settings, **kwargs) -> OrchestrationPort:
    """Load orchestration through the central extension manager."""
    from app.agent_base.core.plugins import get_plugin_manager

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
