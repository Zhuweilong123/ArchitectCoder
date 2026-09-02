"""Tests for the modular plan/explore/main-agent hand-off."""

import asyncio
import json

from app.agent_base.core.hooks import AgentRuntime, reset_runtime, set_runtime
from app.agent_base.core.orchestration import (
    NoOpOrchestrator,
    OrchestrationRequest,
    RuntimeDirectives,
    apply_runtime_directives,
    load_orchestrator,
)
import app.agent_base.core.orchestration as core_orchestration
from app.agent_base.orchestration import TaskOrchestrator, TaskPhase
import app.agent_base.orchestration.orchestrator as orchestrator_module


class _PlannerLLM:
    def __init__(self, payload=None, *, error=None):
        self.payload = payload
        self.error = error
        self.planner_calls = 0

    async def ainvoke_with_metadata(self, messages, **kwargs):
        self.planner_calls += 1
        if self.error:
            raise self.error
        return {
            "content": json.dumps(self.payload, ensure_ascii=False),
            "usage": {"total_tokens": 23},
            "model": "test-model",
        }


def test_orchestrator_noop_plan_does_not_install_task_gate(tmp_path):
    llm = _PlannerLLM({
        "needs_execution": False,
        "needs_exploration": False,
        "goal": "answer only",
        "steps": [],
    })
    runtime = AgentRuntime()
    token = set_runtime(runtime)
    try:
        result = asyncio.run(TaskOrchestrator(llm, source_dir=str(tmp_path)).prepare("hello"))
        assert result.phase == TaskPhase.PLAN
        assert not result.should_continue_with_main_agent
        assert result.total_tokens == 23
        assert runtime.todos == []
        assert not runtime.requires_todo_plan
    finally:
        reset_runtime(token)


def test_orchestrator_falls_back_to_bounded_plan(tmp_path):
    llm = _PlannerLLM(error=ValueError("bad planner json"))
    runtime = AgentRuntime()
    token = set_runtime(runtime)
    try:
        result = asyncio.run(TaskOrchestrator(llm, source_dir=str(tmp_path)).prepare("repair source"))
        assert result.plan.source == "deterministic"
        assert [step.phase for step in result.plan.steps] == [TaskPhase.EXPLORE, TaskPhase.MODIFY, TaskPhase.VERIFY]
        assert result.runtime_directives["requires_todo_plan"]
        assert not result.runtime_directives["requires_acceptance_todos"]
        assert len(result.runtime_directives["todos"]) == 3
    finally:
        reset_runtime(token)


class _FakeExplorer:
    def __init__(self, *args, **kwargs):
        self.last_token_usage = 41

    async def _execute(self, params):
        assert params["toolkit"] == "strategy"
        return "evidence: source and tests agree"


def test_cross_artifact_task_uses_read_only_worker_and_filters_main_tools(tmp_path, monkeypatch):
    llm = _PlannerLLM({
        "needs_execution": True,
        "needs_exploration": True,
        "goal": "align design and implementation",
        "steps": [
            {"id": "explore", "content": "compare artifacts", "phase": "explore", "acceptance": "evidence recorded"},
            {"id": "modify", "content": "apply alignment", "phase": "modify", "acceptance": "change applied"},
            {"id": "verify", "content": "run tests", "phase": "verify", "acceptance": "tests pass"},
        ],
    })
    monkeypatch.setattr(orchestrator_module, "SpawnSubagentTool", _FakeExplorer)
    runtime = AgentRuntime()
    token = set_runtime(runtime)
    try:
        result = asyncio.run(TaskOrchestrator(
            llm,
            project_file=str(tmp_path / "model.json"),
            source_dir=str(tmp_path / "src"),
            test_dir=str(tmp_path / "tests"),
        ).prepare("align design, source, and tests"))
        assert result.phase == TaskPhase.EXPLORE
        assert result.exploration_summary.startswith("evidence:")
        assert result.total_tokens == 64
        assert result.runtime_directives["requires_acceptance_todos"]
        assert result.runtime_directives["todos"][0]["status"] == "completed"
        assert result.runtime_directives["todos"][-1]["kind"] == "verification"

        apply_runtime_directives(RuntimeDirectives(
            requires_todo_plan=result.runtime_directives["requires_todo_plan"],
            requires_acceptance_todos=result.runtime_directives["requires_acceptance_todos"],
            todos=tuple(result.runtime_directives["todos"]),
        ))
        assert runtime.requires_acceptance_todos
        assert runtime.todos[0]["status"] == "completed"

        allowed = TaskOrchestrator(llm).allowed_main_tools([
            "read_file", "get_project_map", "find_nodes", "edit_file", "bash", "spawn_subagent",
        ])
        assert allowed == ["read_file", "edit_file", "bash"]
    finally:
        reset_runtime(token)


class _Settings:
    agent_orchestration_enabled = True
    agent_orchestrator_provider = "missing.module:create"


def test_loader_returns_noop_when_provider_is_missing():
    provider = load_orchestrator(llm=object(), settings=_Settings())
    assert isinstance(provider, NoOpOrchestrator)
    result = asyncio.run(provider.prepare(OrchestrationRequest("hello")))
    assert result.context == ""
    assert result.token_overhead == 0


def test_loader_returns_noop_when_disabled():
    class DisabledSettings:
        agent_orchestration_enabled = False

    provider = load_orchestrator(llm=object(), settings=DisabledSettings())
    assert isinstance(provider, NoOpOrchestrator)


def test_loader_contains_provider_runtime_failure(monkeypatch):
    class BrokenProvider:
        async def prepare(self, request):
            raise RuntimeError("provider unavailable")

    monkeypatch.setattr(core_orchestration, "_load_factory", lambda _: (lambda **kwargs: BrokenProvider()))
    provider = load_orchestrator(llm=object(), settings=_Settings())
    result = asyncio.run(provider.prepare(OrchestrationRequest("hello")))
    assert result == core_orchestration.OrchestrationPreparation()
