"""Stable orchestration port owned by the Agent core.

The core deliberately knows nothing about a concrete planner or explorer.  A
provider may be installed through configuration, while ``NoOpOrchestrator``
keeps the normal single-agent path fully functional when the provider is
disabled or unavailable.
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


@dataclass(frozen=True)
class RuntimeDirectives:
    """Provider suggestions applied by the core runtime, not by a plugin."""

    requires_todo_plan: bool = False
    requires_acceptance_todos: bool = False
    todos: tuple[dict[str, Any], ...] = ()
    strategy_subagent_used: bool = False


@dataclass(frozen=True)
class OrchestrationPreparation:
    """The only provider output consumed by the main Agent loop."""

    context_blocks: tuple[str, ...] = ()
    excluded_tools: tuple[str, ...] = ()
    token_overhead: int = 0
    runtime_directives: RuntimeDirectives = field(default_factory=RuntimeDirectives)
    phase: str = "plan"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def context(self) -> str:
        return "\n\n".join(block for block in self.context_blocks if block)


class OrchestrationPort(Protocol):
    async def prepare(self, request: OrchestrationRequest) -> OrchestrationPreparation:
        """Prepare optional context, tool boundaries, and runtime directives."""


class NoOpOrchestrator:
    """Zero-cost fallback preserving the pre-orchestration behavior."""

    async def prepare(self, request: OrchestrationRequest) -> OrchestrationPreparation:
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
        except Exception:
            logger.warning("[Orchestration] provider failed during prepare; using no-op", exc_info=True)
            return OrchestrationPreparation()


def _load_factory(provider: str):
    """Compatibility hook; actual loading remains owned by PluginManager."""
    from .plugins import PluginManager

    return PluginManager._load_factory(provider)


def load_orchestrator(*, llm, settings, **kwargs) -> OrchestrationPort:
    """Load orchestration through the central extension manager."""
    from .plugins import get_plugin_manager

    instance = get_plugin_manager().load(
        "orchestration",
        settings=settings,
        kwargs={"llm": llm, **kwargs},
        factory_loader=_load_factory,
    )
    if instance is None:
        return NoOpOrchestrator()
    return _ResilientOrchestrator(instance)


def apply_runtime_directives(directives: RuntimeDirectives) -> None:
    """Apply provider suggestions through the core runtime boundary."""

    if (
        not directives.requires_todo_plan
        and not directives.requires_acceptance_todos
        and not directives.todos
        and not directives.strategy_subagent_used
    ):
        return
    from .hooks import get_runtime

    runtime = get_runtime()
    runtime.requires_todo_plan = bool(directives.requires_todo_plan)
    runtime.requires_acceptance_todos = bool(directives.requires_acceptance_todos)
    runtime.strategy_subagent_used = bool(directives.strategy_subagent_used)
    runtime.todos = [dict(todo) for todo in directives.todos]
    runtime.rounds_since_todo = 0


def exclude_tools(tool_names: list[str], excluded: tuple[str, ...] | list[str]) -> list[str]:
    excluded_set = set(excluded)
    return [name for name in tool_names if name not in excluded_set]
