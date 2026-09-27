"""Focused checks for durable read-only exploration scheduling."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.services.run_state import RunStatus, RunStore
from app.agent_base.core.orchestration import OrchestrationPreparation, OrchestrationRequest
from extensions.orchestration.architecture_aware import scheduler as scheduling
from extensions.orchestration.architecture_aware.evidence import collect_file_evidence
from extensions.orchestration.architecture_aware.impact import ImpactSlice
from extensions.orchestration.architecture_aware.partition import ExplorationPackage, partition_impact
from extensions.orchestration.architecture_aware.provider import ArchitectureAwareOrchestrator
from extensions.orchestration.architecture_aware.store import (
    ScheduleConflict,
    ScheduleStore,
    WorkAssignment,
)


def test_dynamic_scheduler_rebalances_and_reuses_completed_work(tmp_path, monkeypatch):
    runs = RunStore(tmp_path / "runs.db")
    monkeypatch.setattr(scheduling, "get_run_store", lambda: runs)
    scheduler = scheduling.DynamicExplorationScheduler(max_workers=2, worker_seconds=1)
    items = tuple(
        WorkAssignment(item_id, (item_id,), 1.0, slot, 1)
        for item_id, slot in (("a", 0), ("b", 1), ("c", 0), ("d", 1))
    )
    calls: list[str] = []

    async def explore(package, limit):
        calls.append(package.id)
        await asyncio.sleep(0.2 if package.id == "b" else 0.01)
        return {
            "package": package.id,
            "status": "completed",
            "summary": "evidence",
            "tokens": 100,
            "node_count": 1,
            "estimated_cost": package.cost,
        }

    async def run_once():
        return await scheduler.run(
            root_run_id="root", fingerprint="graph-v1", items=items,
            total_tokens=12000, worker=explore,
        )

    first = asyncio.run(run_once())
    resumed = asyncio.run(run_once())

    assert len(first.results) == len(resumed.results) == 4
    assert first.plan.revision >= 2
    assert first.worker_tokens == 400
    assert resumed.worker_tokens == 0
    assert len(calls) == 4
    assert all(
        runs.get(item.child_run_id).status == RunStatus.SUCCEEDED.value
        for item in first.plan.items
    )


def test_schedule_rejects_stale_assignment_result(tmp_path):
    store = ScheduleStore(tmp_path / "runs.db")
    item = WorkAssignment("a", ("node",), 1.0, 0, 1)
    store.open("schedule", "root", "graph-v1", (item,))
    revised = store.repartition("schedule", 1, {"a": 1}, "idle slot")
    claimed = store.claim("schedule", "a", 1)

    assert revised.revision == claimed.revision == 2
    with pytest.raises(ScheduleConflict):
        store.finish("schedule", "a", claimed.child_run_id,
                     claimed.lease_token, 1, {"status": "completed"})
    assert store.get("schedule").items[0].result is None


def test_planner_accepts_fenced_json_and_configured_token_limit():
    calls = []
    payload = {
        "needs_execution": True,
        "needs_exploration": True,
        "goal": "Update sales flow",
        "search_queries": ["SalesService"],
        "steps": [{"id": "inspect", "content": "Inspect design and source",
                   "phase": "explore", "acceptance": "Impact is known"}],
    }

    class LLM:
        async def ainvoke_with_metadata(self, messages, **kwargs):
            calls.append(kwargs["max_tokens"])
            return {"content": "```json\n" + json.dumps(payload) + "\n```\nDone.",
                    "usage": {"total_tokens": 23}}

    settings = SimpleNamespace(agent_planner_max_tokens=3000,
                               agent_planner_timeout_seconds=30)
    provider = ArchitectureAwareOrchestrator(
        llm=LLM(), settings=settings, legacy=None,
        project_file="trade.umlproj", source_dir="src", test_dir="test",
        explorer_factory=None,
    )
    plan, queries, tokens = asyncio.run(provider._plan(
        OrchestrationRequest(user_message="Update sales flow")))

    assert plan.needs_exploration
    assert queries == ("SalesService",)
    assert tokens == 23
    assert calls == [3000]


def test_empty_planner_response_keeps_token_usage_on_fallback():
    class LLM:
        async def ainvoke_with_metadata(self, messages, **kwargs):
            return {"content": "", "usage": {"total_tokens": 1800}}

    class Legacy:
        async def prepare(self, request):
            return OrchestrationPreparation()

    settings = SimpleNamespace(agent_knowledge_graph_enabled=True,
                               agent_planner_max_tokens=3000,
                               agent_planner_timeout_seconds=30)
    provider = ArchitectureAwareOrchestrator(
        llm=LLM(), settings=settings, legacy=Legacy(),
        project_file="trade.umlproj", source_dir="src", test_dir="test",
        explorer_factory=object(),
    )
    result = asyncio.run(provider.prepare(
        OrchestrationRequest(user_message="Update sales flow")))

    assert result.metadata["architecture_scheduling"] == "fallback"
    assert result.token_overhead == 1800


def test_file_evidence_stays_inside_project_roots(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    safe = source / "sales.py"
    safe.write_text("class SalesService:\n    pass\n", encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("class Secret:\n    pass\n", encoding="utf-8")
    project_file = tmp_path / "trade.umlproj"
    project_file.write_text('{\n  "name": "SalesService"\n}\n', encoding="utf-8")
    impact = ImpactSlice("trade", {
        "safe": {"id": "safe", "name": "SalesService", "file": str(safe)},
        "outside": {"id": "outside", "name": "Secret", "file": str(outside)},
        "design": {"id": "design", "name": "SalesService"},
    }, (), ("safe",), ("SalesService",))
    request = OrchestrationRequest(user_message="Inspect sales", project_file=str(project_file),
                                   source_dir=str(source))

    evidence = collect_file_evidence(
        request, impact, ExplorationPackage("explore", tuple(impact.nodes), 1.0))

    assert len(evidence) == 2
    assert all(str(outside) not in excerpt for excerpt in evidence)
    assert any("sales.py:1:" in excerpt for excerpt in evidence)
    assert any("trade.umlproj:2:" in excerpt for excerpt in evidence)


def test_excerpt_only_exploration_is_partial(tmp_path, monkeypatch):
    runs = RunStore(tmp_path / "runs.db")
    monkeypatch.setattr(scheduling, "get_run_store", lambda: runs)
    source = tmp_path / "src"
    source.mkdir()
    file = source / "sales.py"
    file.write_text("class SalesService:\n    pass\n", encoding="utf-8")
    project_file = tmp_path / "trade.umlproj"
    project_file.write_text("{}", encoding="utf-8")
    impact = ImpactSlice("trade", {
        "sales": {"id": "sales", "name": "SalesService", "file": str(file)},
    }, (), ("sales",), ("SalesService",))

    class Worker:
        def __init__(self, **kwargs):
            self.last_token_usage = 100
            self.last_evidence_summary = []

        async def _execute(self, params):
            return "sales.py:1 contains SalesService"

    settings = SimpleNamespace(agent_architecture_scheduling_total_tokens=5000,
                               agent_architecture_scheduling_worker_seconds=5,
                               agent_architecture_scheduling_max_workers=2)
    provider = ArchitectureAwareOrchestrator(
        llm=object(), settings=settings, legacy=None,
        project_file=str(project_file), source_dir=str(source), test_dir="",
        explorer_factory=Worker,
    )
    request = OrchestrationRequest(
        user_message="Update sales flow", project_file=str(project_file),
        source_dir=str(source), run_id="root",
    )
    results, tokens, plan = asyncio.run(provider._explore_dynamic(
        request, impact, partition_impact(impact), 0))

    assert results[0]["status"] == "partial"
    assert results[0]["grounded_excerpts"] == 1
    assert runs.get(plan.items[0].child_run_id).status == RunStatus.PARTIAL.value
    assert tokens == 100
