"""Focused checks for durable read-only exploration scheduling."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.services.run_state import RunStatus, RunStore
from app.agent_base.host_api.orchestration import OrchestrationRequest
from extensions.orchestration.architecture_aware import scheduler as scheduling
from extensions.orchestration.architecture_aware.evidence import collect_file_evidence
from extensions.orchestration.architecture_aware.impact import ImpactSlice
from extensions.orchestration.architecture_aware.graph_files import SliceReadiness
from extensions.orchestration.architecture_aware.partition import ExplorationPackage, partition_impact
from extensions.orchestration.architecture_aware.provider import ArchitectureAwareOrchestrator
from extensions.orchestration.architecture_aware.store import (
    ScheduleConflict,
    ScheduleStore,
    WorkAssignment,
)


def test_default_shared_budget_funds_four_equal_128k_explorers():
    items = tuple(WorkAssignment(str(i), (str(i),), float(i + 1), i % 2, 1)
                  for i in range(4))
    budgets = [scheduling.item_token_budget(item, 524288, items) for item in items]

    assert budgets == [131072] * 4
    assert sum(budgets) == 524288
    assert scheduling.work_item_limit(524288, 2) == 4
    assert scheduling.work_item_limit(262144, 2) == 2
    constrained = [scheduling.item_token_budget(item, 5000, items) for item in items]
    assert sum(constrained) <= 5000


@pytest.mark.parametrize("phase", ["planned", "completed"])
def test_cost_audit_records_instance_request_token_cap(monkeypatch, phase):
    events = []
    monkeypatch.setattr(
        "extensions.orchestration.architecture_aware.provider.get_host_services",
        lambda: SimpleNamespace(emit_event=lambda kind, payload: events.append((kind, payload))),
    )
    impact = ImpactSlice("trade", {
        "sales": {"id": "sales", "name": "SalesService"},
    }, (), ("sales",), ("SalesService",))
    decision = partition_impact(impact)
    items = (WorkAssignment("sales-item", ("sales",), 1.0, 0, 1),)
    provider = ArchitectureAwareOrchestrator(
        llm=object(), settings=SimpleNamespace(agent_context_hard_limit_tokens=12345),
        project_file="trade.umlproj", source_dir="src", test_dir="test",
        explorer_factory=object(),
    )

    provider._emit_cost_audit(
        impact, decision, budget=5000, readiness=SliceReadiness(),
        initial_items=items, phase=phase,
    )

    assert len(events) == 1
    kind, audit = events[0]
    assert kind == "architecture_cost_audit"
    assert audit["phase"] == phase
    assert audit["scheduler"]["items"][0]["single_request_token_cap"] == 12345


def test_dynamic_scheduler_rebalances_and_reuses_completed_work(tmp_path, monkeypatch):
    runs = RunStore(tmp_path / "runs.db")
    monkeypatch.setattr(scheduling, "get_host_services", lambda: SimpleNamespace(run_store=lambda: runs))
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


def test_prepare_only_advertises_demand_tool_without_model_call(monkeypatch):
    async def fake_routing_map(*_args):
        return {}, ""

    monkeypatch.setattr(
        "extensions.orchestration.architecture_aware.provider.routing_map",
        fake_routing_map,
    )

    class LLM:
        async def ainvoke_with_metadata(self, messages, **kwargs):
            raise AssertionError("preparation must not call the planner")

    provider = ArchitectureAwareOrchestrator(
        llm=LLM(), settings=SimpleNamespace(agent_knowledge_graph_enabled=True),
        project_file="trade.umlproj", source_dir="src", test_dir="test",
        explorer_factory=object(),
    )
    result = asyncio.run(provider.prepare(OrchestrationRequest(
        user_message="Update sales flow", available_tools=("route_architecture",),
    )))

    assert result.metadata["architecture_scheduling"] == "demand_driven_ready"
    assert "route_architecture" in result.context


def test_prepare_without_demand_tool_has_no_architecture_context():
    class LLM:
        async def ainvoke_with_metadata(self, messages, **kwargs):
            raise AssertionError("preparation must not call the planner")

    provider = ArchitectureAwareOrchestrator(
        llm=LLM(), settings=SimpleNamespace(),
        project_file="trade.umlproj", source_dir="src", test_dir="test",
        explorer_factory=object(),
    )
    result = asyncio.run(provider.prepare(
        OrchestrationRequest(user_message="Update sales flow")))

    assert result.metadata["architecture_scheduling"] == "unavailable"
    assert result.context == ""
    assert result.excluded_tools == ("route_architecture",)


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
    assert all(str(outside) not in excerpt.text for excerpt in evidence)
    assert any("sales.py:1:" in excerpt.text for excerpt in evidence)
    assert any("trade.umlproj:2:" in excerpt.text for excerpt in evidence)


def test_excerpt_only_exploration_is_partial(tmp_path, monkeypatch):
    runs = RunStore(tmp_path / "runs.db")
    monkeypatch.setattr(scheduling, "get_host_services", lambda: SimpleNamespace(run_store=lambda: runs))
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
        llm=object(), settings=settings,
        project_file=str(project_file), source_dir=str(source), test_dir="",
        explorer_factory=Worker,
    )
    request = OrchestrationRequest(
        user_message="Update sales flow", project_file=str(project_file),
        source_dir=str(source), run_id="root",
    )
    results, tokens, plan = asyncio.run(provider._explore_dynamic(
        request, impact, partition_impact(impact)))

    assert results[0]["status"] == "partial"
    assert results[0]["grounded_excerpts"] == 1
    assert runs.get(plan.items[0].child_run_id).status == RunStatus.PARTIAL.value
    assert tokens == 100


@pytest.mark.parametrize("with_source", [False, True])
def test_evidence_coordinates_are_readable_with_real_alias_directories(tmp_path, with_source):
    import json
    import re
    from dataclasses import replace
    from app.agent_base.tools.my_tools.subagent_tool import SpawnSubagentTool

    design = tmp_path / "design" / "uml"
    design.mkdir(parents=True)
    project = design / "泊车 model.umlproj"
    project.write_text('{\n  "name": "ParkingController"\n}\n', encoding="utf-8")
    engine = tmp_path / "engine"
    engine.mkdir()
    source_file = engine / "parking.py"
    source_file.write_text("class ParkingController:\n    pass\n", encoding="utf-8")
    # Real source/design directories win over identically named aliases.
    (tmp_path / "source").mkdir()
    (tmp_path / "source" / "parking.py").write_text("wrong_source = True\n", encoding="utf-8")
    request = OrchestrationRequest(
        user_message="Inspect parking", project_file=str(project),
        source_dir=str(engine) if with_source else "",
        workspace_root=str(tmp_path), design_dir=str(design),
    )
    nodes = {"design": {"id": "design", "name": "ParkingController"}}
    if with_source:
        nodes["code"] = {"id": "code", "name": "ParkingController", "file": str(source_file)}
    impact = ImpactSlice("parking", nodes, (), tuple(nodes), ("ParkingController",))
    package = ExplorationPackage("explore", tuple(nodes), 1.0)
    evidence = collect_file_evidence(request, impact, package)
    worker = SpawnSubagentTool(llm=object(), source_dir=request.source_dir,
        workspace_root=request.workspace_root, design_dir=request.design_dir,
        project_file=request.project_file, toolkits=("strategy",))
    registry = worker.sub_registries["strategy"]
    for excerpt in evidence:
        result = asyncio.run(registry.aexecute_tool_result_with_params("read_file", {
            "path": excerpt.path, "offset": excerpt.start_line - 1,
            "limit": excerpt.end_line - excerpt.start_line + 1,
        }))
        assert result.status == "success", result.text
        assert "ParkingController" in result.text
        assert "wrong_source" not in result.text
    assert any(excerpt.path == project.resolve().as_posix() for excerpt in evidence)
    outside = asyncio.run(registry.aexecute_tool_result_with_params("read_file", {
        "path": str(tmp_path.parent / "outside.py"),
    }))
    assert outside.status == "blocked" and outside.error_code == "POLICY_BLOCKED"
    # A display-only change cannot alter the executable first-read coordinates.
    displayed = (replace(evidence[0], text="display label without any path or line"),)
    description = ArchitectureAwareOrchestrator._worker_description(request, impact, package, displayed)
    match = re.search(r'First call read_file with path=("(?:[^"\\]|\\.)*"), offset=(\d+)', description)
    assert match
    assert json.loads(match[1]) == evidence[0].path
    assert int(match[2]) == evidence[0].start_line - 1
    assert '"path":"source/...' not in description
    scopes = json.loads(description.split("Configured path scopes: ", 1)[1].splitlines()[0])
    assert ("source" in scopes) == with_source


def test_orchestration_factory_passes_parent_workspace_to_real_worker_tools(tmp_path, monkeypatch):
    import json
    import re
    from app.agent_base.tools.my_tools.subagent_tool import SpawnSubagentTool
    from extensions.orchestration.provider import create

    runs = RunStore(tmp_path / "runs.db")
    monkeypatch.setattr(scheduling, "get_host_services", lambda: SimpleNamespace(run_store=lambda: runs))
    design = tmp_path / "design" / "uml"
    design.mkdir(parents=True)
    project = design / "parking.umlproj"
    project.write_text('{\n  "name": "ParkingController"\n}\n', encoding="utf-8")
    (tmp_path / "engine").mkdir()
    (tmp_path / "engine" / "parking.py").write_text("parking_marker = 1\n", encoding="utf-8")
    captured = []

    class Worker(SpawnSubagentTool):
        def __init__(self, **kwargs):
            captured.append(kwargs)
            super().__init__(**kwargs)

        async def _execute(self, params):
            description = params["description"]
            match = re.search(r'First call read_file with path=("(?:[^"\\]|\\.)*"), offset=(\d+)', description)
            path = json.loads(match[1])
            registry = self.sub_registries["strategy"]
            read = await registry.aexecute_tool_result_with_params("read_file", {"path": path})
            assert read.status == "success", read.text
            assert "ParkingController" in read.text
            # The child must retain the parent workspace beyond the UML directory.
            code = await registry.aexecute_tool_result_with_params("read_file", {"path": "workspace/engine/parking.py"})
            assert code.status == "success" and "parking_marker" in code.text
            scopes = json.loads(description.split("Configured path scopes: ", 1)[1].splitlines()[0])
            assert scopes == {"workspace": str(tmp_path), "design": str(design)}
            self.last_token_usage = 100
            self.last_evidence_summary = [{"status": "success", "tool_name": "read_file",
                "facts": [f"file={path}", "lines 1-3"]}]
            return json.dumps({"findings": [{"path": path, "start_line": 1, "end_line": 3,
                "symbol": "ParkingController", "behavior": "Design participant exists", "role": "dependency"}],
                "unresolved": [], "excluded_candidates": []})

    provider = create(llm=object(), explorer_factory=Worker,
        project_file=str(project), workspace_root=str(tmp_path), design_dir=str(design),
        settings=SimpleNamespace(agent_knowledge_graph_enabled=True,
            agent_architecture_scheduling_total_tokens=5000,
            agent_architecture_scheduling_worker_seconds=5,
            agent_architecture_scheduling_max_workers=1))
    impact = ImpactSlice("parking", {"parking": {"id": "parking", "name": "ParkingController"}},
        (), ("parking",), ("ParkingController",))
    request = OrchestrationRequest(user_message="Inspect parking", project_file=str(project), run_id="root")
    results, _, _ = asyncio.run(provider._explore_dynamic(request, impact, partition_impact(impact)))
    assert results[0]["status"] == "completed", results
    assert captured[0]["workspace_root"] == str(tmp_path)
    assert captured[0]["design_dir"] == str(design)
    assert captured[0]["source_dir"] == captured[0]["test_dir"] == ""
