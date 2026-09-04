"""Optional LLM orchestration provider.

This module is intentionally loaded by the core loader and is not imported by
the main chat flow directly.  Removing the module therefore falls back to the
core ``NoOpOrchestrator`` without a source change in the Agent transport.
"""

from __future__ import annotations

from app.agent_base.core.orchestration import (
    OrchestrationPreparation,
    OrchestrationRequest,
    RuntimeDirectives,
)

from .orchestrator import TaskOrchestrator


class LLMOrchestrator:
    """Adapter from the concrete planner implementation to the stable port."""

    def __init__(self, implementation: TaskOrchestrator):
        self.implementation = implementation

    async def prepare(self, request: OrchestrationRequest) -> OrchestrationPreparation:
        result = await self.implementation.prepare(
            request.user_message,
            previous_checkpoint=request.previous_checkpoint,
        )
        directives = result.runtime_directives or {}
        runtime_directives = RuntimeDirectives(
            requires_todo_plan=bool(directives.get("requires_todo_plan", False)),
            requires_acceptance_todos=bool(directives.get("requires_acceptance_todos", False)),
            todos=tuple(dict(todo) for todo in (directives.get("todos") or ())),
            strategy_subagent_used=bool(directives.get("strategy_subagent_used", False)),
        )
        excluded_tools = ()
        if result.exploration_summary:
            excluded_tools = tuple(
                name for name in request.available_tools
                if name not in self.implementation.allowed_main_tools(request.available_tools)
            )
        context_blocks = (result.as_context(),) if result.should_continue_with_main_agent else ()
        return OrchestrationPreparation(
            context_blocks=context_blocks,
            excluded_tools=excluded_tools,
            token_overhead=result.total_tokens,
            runtime_directives=runtime_directives,
            phase=result.phase.value,
            metadata={
                "goal": result.plan.goal,
                "source": result.plan.source,
                "needs_execution": result.plan.needs_execution,
                "needs_exploration": result.plan.needs_exploration,
                "steps": tuple(step.id for step in result.plan.steps),
                "planner_tokens": result.planner_tokens,
                "worker_tokens": result.worker_tokens,
            },
        )


def create(*, llm, settings, project_file: str = "", source_dir: str = "", test_dir: str = "", **kwargs):
    """Provider factory used by ``load_orchestrator``."""

    return LLMOrchestrator(TaskOrchestrator(
        llm,
        project_file=project_file,
        source_dir=source_dir,
        test_dir=test_dir,
        planner_max_tokens=settings.agent_planner_max_tokens,
        planner_timeout_seconds=settings.agent_planner_timeout_seconds,
        worker_max_steps=settings.agent_explorer_max_steps,
        worker_max_total_tokens=settings.agent_subagent_max_total_tokens,
    ))
