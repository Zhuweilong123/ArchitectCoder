"""Validate discovered plans against actual lifecycle dispatch and artifacts."""
import asyncio
import json
import sys
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent_base.core.hooks import (
    AgentRuntime, HookAction, HookContext, HookDecision, HookEvent, HookRegistry,
    get_hooks, reset_runtime, set_runtime,
)
from app.agent_base.core.lifecycle import Contribution, discover_plan, install_plan
from app.agent_base.core.plugins import PluginManager, PluginSpec


def declaration(monkeypatch, contributions=(), enabled=True, module_name="test_stage_plugin"):
    calls = []
    def observe(ctx):
        calls.append(ctx)
    def factory(**kwargs):
        raise AssertionError("Discovery must not instantiate domain providers")
    monkeypatch.setitem(sys.modules, module_name, SimpleNamespace(
        create=factory, observe=observe, list_contributions=lambda **kwargs: contributions,
    ))
    manager = PluginManager((PluginSpec("demo", "enabled", "provider", f"{module_name}:create", ("query",)),))
    return manager, SimpleNamespace(enabled=enabled, provider=f"{module_name}:create"), calls


def item(identifier="demo.observe", stage=HookEvent.RUN_START, **kwargs):
    return Contribution(identifier, stage, "test_stage_plugin:observe", **kwargs)


def test_discovery_exports_interfaces_stable_plan_and_two_graphs(monkeypatch, tmp_path):
    manager, settings, _ = declaration(monkeypatch, (item(),))
    plan = discover_plan(manager, settings)
    assert plan.plugins[0]["status"] == "discovered"
    assert plan.plugins[0]["interfaces"] == ["query"]
    assert plan.as_dict()["plan_id"] == discover_plan(manager, settings).as_dict()["plan_id"]
    plan.write(tmp_path)
    assert json.loads((tmp_path / "plugin-plan.json").read_text(encoding="utf-8")) == json.loads(json.dumps(plan.as_dict()))
    assert "Model call" in (tmp_path / "plugin-schedule.mmd").read_text()
    organization = (tmp_path / "plugin-organization.mmd").read_text()
    assert "demo.interface.query / service" in organization
    assert "P0 --> C" in organization
    assert "demo.observe" in organization
    assert list(tmp_path.glob("*.tmp")) == []


def test_install_is_idempotent_and_dispatch_matches_export_order(monkeypatch):
    first = item("first", priority=0)
    second = item("second", priority=1000, after=("first",))
    manager, settings, calls = declaration(monkeypatch, (second, first))
    plan = discover_plan(manager, settings)
    registry = HookRegistry()
    install_plan(plan, registry)
    # Different declarations can point to the same function without losing their IDs.
    install_plan(plan, registry)
    runtime = AgentRuntime(plugin_plan_id=registry.plan_id)
    registry.emit(HookEvent.RUN_START, HookContext(HookEvent.RUN_START, "demo", runtime=runtime))
    assert len(calls) == 2
    exported = next(stage for stage in plan.as_dict()["stages"] if stage["stage"] == "run_start")
    assert [c["id"] for c in exported["contributions"] if c["plugin"] == "demo" and c["mode"] != "service"] == ["first", "second"]


@pytest.mark.parametrize("enabled,provider,status", [
    (False, "missing_stage_plugin:create", "disabled"),
    (True, "missing_stage_plugin:create", "unavailable"),
])
def test_disabled_and_missing_plugins_remain_visible(monkeypatch, enabled, provider, status):
    manager, settings, _ = declaration(monkeypatch)
    settings.enabled, settings.provider = enabled, provider
    row = discover_plan(manager, settings).plugins[0]
    assert row["status"] == status
    assert row["contributions"] == []


@pytest.mark.parametrize("contributions", [
    (item(), item()),
    (item(after=("missing",)),),
    (item("a", after=("b",)), item("b", after=("a",))),
    (item(mode="control"),),
    (replace(item(), stage="unknown"),),
    (item(scope="global"),),
    (item(fail_closed=True),),
])
def test_invalid_declarations_are_explained_and_not_installed(monkeypatch, contributions):
    manager, settings, _ = declaration(monkeypatch, contributions)
    plan = discover_plan(manager, settings)
    assert plan.plugins[0]["status"] == "unavailable"
    assert plan.plugins[0]["error"]
    assert all(plugin != "demo" for plugin, _ in plan.contributions)


def test_control_short_circuit_preserves_observers_and_records_skips():
    from app.trace.tracing import reset_current_trace_sink, set_current_trace_sink
    events, called = [], []
    sink = SimpleNamespace(event=lambda kind, **payload: events.append((kind, payload)))
    token = set_current_trace_sink(sink)
    registry = HookRegistry()
    runtime = AgentRuntime(plugin_plan_id="test-plan")
    registry.register(HookEvent.TOOL_BEFORE, lambda ctx: HookDecision(HookAction.VETO, reason="permission", message="Path is protected"),
                      priority=100, contribution_id="deny", plugin="demo", mode="control")
    registry.register(HookEvent.TOOL_BEFORE, lambda ctx: called.append("skipped"),
                      priority=50, contribution_id="lower", plugin="demo", mode="control")
    def observer(ctx):
        called.append("observed")
        assert ctx.runtime is None
        assert ctx.payload["plugin_plan_id"] == "test-plan"
        ctx.tool_input["path"] = "changed"
        return HookDecision(HookAction.STOP)
    registry.register(HookEvent.TOOL_BEFORE, observer, priority=0,
                      contribution_id="watch", plugin="demo", mode="observer")
    ctx = HookContext(HookEvent.TOOL_BEFORE, "demo", tool_input={"path": "original"}, runtime=runtime)
    try:
        result = registry.trigger(HookEvent.TOOL_BEFORE, ctx)
    finally:
        reset_current_trace_sink(token)
    assert result.action == HookAction.VETO
    assert called == ["observed"]
    assert ctx.tool_input["path"] == "original"
    assert [payload["status"] for _, payload in events] == ["executed", "skipped", "executed"]
    assert all(payload["plan_id"] == "test-plan" for _, payload in events)
    assert events[1][1]["blocked_by"] == "deny"
    assert events[1][1]["decision_action"] == "veto"
    assert events[1][1]["decision_reason"] == "permission"
    assert events[1][1]["decision_message"] == "Path is protected"
    assert "decision_action" not in events[2][1]  # Observer return values never control the run.


@pytest.mark.parametrize("dispatch,fail_closed", [("trigger", True), ("trigger", False), ("emit", True), ("emit", False)])
def test_failed_contribution_records_error_and_actual_failure_policy(dispatch, fail_closed):
    from app.trace.tracing import reset_current_trace_sink, set_current_trace_sink
    events = []
    token = set_current_trace_sink(SimpleNamespace(event=lambda kind, **payload: events.append(payload)))
    registry = HookRegistry()
    stage = HookEvent.LLM_BEFORE if dispatch == "trigger" else HookEvent.TOOL_BATCH_AFTER
    def broken(ctx):
        raise ValueError("configuration missing")
    registry.register(stage, broken, contribution_id="broken", plugin="demo", mode="control", fail_closed=fail_closed)
    try:
        result = getattr(registry, dispatch)(stage, HookContext(stage, "demo", run_id="run"))
    finally:
        reset_current_trace_sink(token)
    assert len(events) == 1
    event = events[0]
    assert event["status"] == "error"
    assert event["error_type"] == "ValueError"
    assert event["error_message"] == "configuration missing"
    assert event["failure_effect"] == ("continue" if not fail_closed else "block" if dispatch == "trigger" else "finalize")
    if fail_closed:
        decision = result if dispatch == "trigger" else result[0]
        assert event["decision_action"] == decision.action
        assert event["decision_reason"] == "hook_error"
        assert "configuration missing" in event["decision_message"]
    else:
        assert "decision_action" not in event


def test_batch_control_decisions_are_consumed_with_finalize_precedence():
    registry = HookRegistry()
    for action in (HookAction.FINALIZE, HookAction.RECOVER):
        registry.register(HookEvent.TOOL_BATCH_AFTER, lambda ctx, action=action: HookDecision(action), mode="control")
    runtime = AgentRuntime()
    registry.emit(HookEvent.TOOL_BATCH_AFTER, HookContext(HookEvent.TOOL_BATCH_AFTER, "demo", runtime=runtime))
    assert runtime.control_decision.action == HookAction.FINALIZE


def test_failed_control_blocks_model_and_observers_still_run():
    registry = HookRegistry()
    seen = []
    def broken(ctx):
        raise RuntimeError("failure")
    registry.register(HookEvent.LLM_BEFORE, broken, priority=50, mode="control", fail_closed=True)
    registry.register(HookEvent.LLM_BEFORE, lambda ctx: seen.append(ctx.event), mode="observer")
    result = registry.trigger(HookEvent.LLM_BEFORE, HookContext(HookEvent.LLM_BEFORE, "test"))
    assert result.action == HookAction.STOP
    assert seen == [HookEvent.LLM_BEFORE]


def test_manifest_discovers_extra_plugins_without_changing_slots(monkeypatch, tmp_path):
    from app.agent_base.core.lifecycle import build_plan
    manager, settings, _ = declaration(monkeypatch)
    path = tmp_path / "plugins.json"
    path.write_text(json.dumps({"schema_version": 1, "plugins": [
        {"name": "extra", "provider": "test_stage_plugin:create", "enabled": False, "interfaces": ["query"]},
    ]}), encoding="utf-8")
    settings.plugin_manifest_file = str(path)
    first = build_plan(manager, settings)
    assert first.plugins[1]["name"] == "extra"
    assert first.plugins[1]["status"] == "disabled"
    assert first.as_dict()["plan_id"] == build_plan(manager, settings).as_dict()["plan_id"]


def test_manifest_failure_is_atomic_and_cannot_override_existing_slots(monkeypatch, tmp_path):
    manager, settings, _ = declaration(monkeypatch)
    path = tmp_path / "plugins.json"
    path.write_text(json.dumps({"schema_version": 1, "plugins": [
        {"name": "new", "provider": "new:create"},
        {"name": "demo", "provider": "replacement:create"},
    ]}), encoding="utf-8")
    with pytest.raises(ValueError, match="reserved"):
        manager.discover_specs(path)
    assert [spec.name for spec in manager.specs] == ["demo"]


def test_builtin_routing_contributions_are_discovered_and_fallback_does_not_duplicate():
    from app.agent_base.core.plugins import DEFAULT_PLUGIN_SPECS
    from extensions.orchestration.architecture_aware.routing_checkpoint import register_routing_checkpoint_hooks
    spec = next(spec for spec in DEFAULT_PLUGIN_SPECS if spec.name == "orchestration")
    manager = PluginManager((spec,))
    plan = discover_plan(manager, SimpleNamespace())
    assert len([key for key in plan.plugins[0]["contributions"] if key.startswith("orchestration.routing.")]) == 3
    assert "orchestration.interface.prepare" in plan.plugins[0]["contributions"]
    registry = get_hooks()
    saved = ({stage: list(bindings) for stage, bindings in registry._hooks.items()},
             dict(registry._metadata), registry.plan_id)
    try:
        install_plan(plan, registry)
        before = sum(map(len, registry._hooks.values()))
        register_routing_checkpoint_hooks()
        assert sum(map(len, registry._hooks.values())) == before
        assert discover_plan(manager, SimpleNamespace()).as_dict()["plan_id"] == plan.as_dict()["plan_id"]
    finally:
        registry._hooks, registry._metadata, registry.plan_id = saved


def test_trace_contribution_preserves_phase_outcome_and_plan_id():
    from app.trace.tracing import reset_current_trace_sink, set_current_trace_sink
    from extensions.trace.lifecycle import observe
    events = []
    token = set_current_trace_sink(SimpleNamespace(event=lambda kind, **data: events.append((kind, data))))
    try:
        observe(HookContext(HookEvent.RUN_END, "test", run_id="run-1", payload={
            "plugin_plan_id": "plan-1", "status": "partial", "step": 2,
        }))
    finally:
        reset_current_trace_sink(token)
    assert events[0][0] == "lifecycle_stage"
    assert events[0][1]["plan_id"] == "plan-1"
    assert events[0][1]["stage_data"] == {"status": "partial", "step": 2}


def test_lifecycle_and_contribution_events_reach_real_jsonl_on_stream_close(tmp_path, caplog):
    from app.agent_base.agents.react_agent import ReActAgent
    from app.agent_base.agents.react_runtime.fc_loop import run_fc_loop
    from app.agent_base.tools.registry import ToolRegistry
    from app.agent_base.core.plugins import DEFAULT_PLUGIN_SPECS
    from app.trace.tracing import TraceSession, TraceSessionRequest, load_trace
    settings = SimpleNamespace(agent_trace_enabled=True, agent_trace_provider="extensions.trace:create")
    spec = next(spec for spec in DEFAULT_PLUGIN_SPECS if spec.name == "trace")
    plan = discover_plan(PluginManager((spec,)), settings)
    registry = get_hooks()
    saved = ({stage: list(bindings) for stage, bindings in registry._hooks.items()},
             dict(registry._metadata), registry.plan_id)
    sink = load_trace(settings=settings).create(TraceSessionRequest(
        session_id="lifecycle-jsonl", trace_dir=str(tmp_path),
    ))
    sink.set_run_id("parent-run")
    class LLM:
        async def ainvoke_with_tools(self, **kwargs):
            return {"content": "done", "tool_calls": None}
    agent = ReActAgent("test", LLM(), ToolRegistry())
    token = set_runtime(AgentRuntime(run_id="task-run"))
    async def consume_final_then_close():
        stream = run_fc_loop(agent, "hello")
        progress = await anext(stream)
        assert progress.is_final
        await stream.aclose()  # Reproduce GeneratorExit after final output.
    try:
        install_plan(plan, registry)
        with TraceSession(session_id="lifecycle-jsonl", sink=sink):
            asyncio.run(consume_final_then_close())
    finally:
        reset_runtime(token)
        registry._hooks, registry._metadata, registry.plan_id = saved
    events = [json.loads(line) for line in Path(sink.path).read_text(encoding="utf-8").splitlines()]
    lifecycle = [event for event in events if event["event_type"] == "lifecycle_stage"]
    assert [event["stage"] for event in lifecycle] == [
        "run_start", "round_before", "model_before", "model_after", "round_after", "finalize", "run_end",
    ]
    contributions = [event for event in events if event["event_type"] == "plugin_contribution"]
    assert len(contributions) > len(lifecycle)
    assert all(event["run_id"] == "task-run" and event["plan_id"] == plan.as_dict()["plan_id"]
               and event["trace_id"] == sink.trace_id for event in lifecycle + contributions)
    assert lifecycle[-1]["stage_data"]["status"] == "completed"
    assert "provider operation event failed" not in caplog.text


def test_blocked_tool_still_emits_tool_after():
    from app.agent_base.agents.react_runtime.tool_round_executor import ToolRoundExecutor
    from app.agent_base.tools.registry import ToolRegistry
    executor = ToolRoundExecutor(ToolRegistry(), agent_name="test")
    seen = []
    observer = lambda ctx: seen.append(ctx.tool_status)
    executor.hooks.register(HookEvent.TOOL_AFTER, observer, mode="observer")
    try:
        result = asyncio.run(executor._execute_one("denied", {}, "blocked"))
    finally:
        executor.hooks.unregister(HookEvent.TOOL_AFTER, observer)
    assert result[2].status == "blocked"
    assert seen == ["blocked"]


def test_plan_api_exposes_same_snapshot_and_validates_view(monkeypatch):
    from app.api.plugins import router
    manager, settings, _ = declaration(monkeypatch, (item(),))
    plan = discover_plan(manager, settings)
    app = FastAPI()
    app.state.plugin_plan = plan
    app.include_router(router)
    with TestClient(app) as client:
        assert client.get("/api/plugins/plan").json()["plan_id"] == plan.as_dict()["plan_id"]
        assert client.get("/api/plugins/graph?view=organization").text == plan.mermaid("organization")
        assert client.get("/api/plugins/graph?view=invalid").status_code == 422


def test_backend_startup_installs_and_exports_authenticated_plan(monkeypatch, tmp_path):
    from app import main
    from app.core import auth
    manager, settings, _ = declaration(monkeypatch, (item(),))
    settings.plugin_plan_dir = str(tmp_path)
    settings.plugin_manifest_file = ""
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "plugin_manager", manager)
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(internal_api_token="test-plan-token"))
    registry = get_hooks()
    saved = ({stage: list(bindings) for stage, bindings in registry._hooks.items()},
             dict(registry._metadata), registry.plan_id)
    try:
        with TestClient(main.app) as client:
            assert client.get("/api/plugins/plan").status_code == 401
            response = client.get("/api/plugins/plan", headers={"Authorization": "Bearer test-plan-token"})
            assert response.status_code == 200
            disk = json.loads((tmp_path / "plugin-plan.json").read_text(encoding="utf-8"))
            assert response.json() == disk
            assert registry.plan_id == disk["plan_id"]
            assert client.get("/api/plugins/graph", headers={"Authorization": "Bearer test-plan-token"}).text == (
                tmp_path / "plugin-schedule.mmd").read_text(encoding="utf-8")
    finally:
        registry._hooks, registry._metadata, registry.plan_id = saved
        if hasattr(main.app.state, "plugin_plan"):
            del main.app.state.plugin_plan


@pytest.mark.parametrize("failure", ["none", "error", "cancel"])
def test_agent_emits_complete_lifecycle_and_terminal_status(failure):
    from app.agent_base.agents.react_agent import ReActAgent
    from app.agent_base.tools.registry import ToolRegistry
    seen = []
    registry = get_hooks()
    def observe(ctx):
        seen.append((ctx.event, ctx.payload))
    for stage in HookEvent:
        registry.register(stage, observe, mode="observer")
    class LLM:
        async def ainvoke_with_tools(self, **kwargs):
            if failure == "error":
                raise RuntimeError("test model failure")
            if failure == "cancel":
                raise asyncio.CancelledError()
            return {"content": "done", "tool_calls": None}
    agent = ReActAgent("test", LLM(), ToolRegistry())
    token = set_runtime(AgentRuntime())
    try:
        if failure == "none":
            assert asyncio.run(agent.arun("hello")) == "done"
        else:
            with pytest.raises(RuntimeError if failure == "error" else asyncio.CancelledError):
                asyncio.run(agent.arun("hello"))
    finally:
        reset_runtime(token)
        for stage in HookEvent:
            registry.unregister(stage, observe)
    stages = [stage for stage, _ in seen]
    assert stages[:3] == [HookEvent.RUN_START, HookEvent.ROUND_BEFORE, HookEvent.LLM_BEFORE]
    assert stages[-1] == HookEvent.RUN_END
    assert stages.count(HookEvent.ROUND_AFTER) == 1
    assert seen[-1][1]["status"] == {"none": "completed", "error": "failed", "cancel": "cancelled"}[failure]
    if failure == "none":
        assert stages[-3:] == [HookEvent.ROUND_AFTER, HookEvent.RUN_FINALIZE, HookEvent.RUN_END]
    else:
        assert (HookEvent.ERROR if failure == "error" else HookEvent.CANCEL) in stages
