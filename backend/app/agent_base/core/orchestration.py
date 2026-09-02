"""Stable orchestration port owned by the Agent core.

The core deliberately knows nothing about a concrete planner or explorer.  A
provider may be installed through configuration, while ``NoOpOrchestrator``
keeps the normal single-agent path fully functional when the provider is
disabled or unavailable.
"""

from __future__ import annotations

import importlib
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
    module_name, separator, attribute = provider.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("orchestrator provider must use 'module:factory' syntax")
    module = importlib.import_module(module_name)
    factory = getattr(module, attribute)
    if not callable(factory):
        raise TypeError(f"orchestrator provider is not callable: {provider}")
    return factory


def load_orchestrator(*, llm, settings, **kwargs) -> OrchestrationPort:
    """Load an optional provider without making the core depend on its package."""

    if not getattr(settings, "agent_orchestration_enabled", True):
        return NoOpOrchestrator()
    provider = str(
        getattr(
            settings,
            "agent_orchestrator_provider",
            "app.agent_base.orchestration.provider:create",
        )
        or ""
    ).strip()
    if not provider or provider.lower() in {"none", "noop", "disabled"}:
        return NoOpOrchestrator()
    try:
        factory = _load_factory(provider)
        instance = factory(llm=llm, settings=settings, **kwargs)
        if not hasattr(instance, "prepare"):
            raise TypeError("orchestrator provider must expose prepare()")
        return _ResilientOrchestrator(instance)
    except Exception:
        # Plugin failures must not take down ordinary chat or development work.
        logger.warning("[Orchestration] provider unavailable; using no-op", exc_info=True)
        return NoOpOrchestrator()


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
