"""Real compiled bindings govern sync, async and concurrent provider requests."""
import asyncio
import inspect
import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.agent_base.core import hooks
from app.agent_base.core.hooks import HookContext, HookDecision, HookEvent, HookRegistry
from app.agent_base.core.lifecycle import discover_plan, install_plan
from app.agent_base.core.plugins import DEFAULT_PLUGIN_SPECS, PluginManager, PluginSpec
from app.agent_base.core.plugin_dispatch import ASYNC_INTERFACES, service_contributions
from app.trace.tracing import set_current_trace_sink, reset_current_trace_sink


@pytest.mark.parametrize("slot", [spec.name for spec in DEFAULT_PLUGIN_SPECS])
def test_every_plugin_declared_interface_runs_via_its_compiled_stage(monkeypatch, slot):
    spec = next(spec for spec in DEFAULT_PLUGIN_SPECS if spec.name == slot)
    module = f"test_scheduled_{slot}"
    spec = replace(spec, default_provider=f"{module}:create")
    calls, events = [], []
    provider = SimpleNamespace()
    methods = [item.interface_id.split(".", 1)[1] for item in service_contributions(spec)]
    for method in methods:
        if method in ASYNC_INTERFACES.get(slot, ()):
            async def target(value, method=method):
                await asyncio.sleep(0)
                calls.append((method, value))
                return value
        else:
            def target(value, method=method):
                calls.append((method, value))
                return provider if slot == "trace" and method == "query" else value
        setattr(provider, method, target)
    monkeypatch.setitem(sys.modules, module, SimpleNamespace(create=lambda **kwargs: provider))
    registry = HookRegistry()
    monkeypatch.setattr(hooks, "_registry", registry)
    manager = PluginManager((spec,))
    plan = discover_plan(manager, SimpleNamespace())
    assert plan.plugins[0]["status"] == "discovered"
    assert all(any(binding["method"] == method for binding in plan.plugins[0]["interface_bindings"])
               for method in methods)
    install_plan(plan, registry)
    scheduled = manager.load(slot, settings=SimpleNamespace())
    token = set_current_trace_sink(SimpleNamespace(event=lambda kind, **data: events.append(data)))
    try:
        for method in methods:
            call = getattr(scheduled, method)
            result = asyncio.run(call(method)) if inspect.iscoroutinefunction(call) else call(method)
            if slot == "trace" and method == "query":
                assert result._provider is provider
            else:
                assert result == method
    finally:
        reset_current_trace_sink(token)
    assert calls == [(method, method) for method in methods]
    events = [event for event in events if event.get("mode") == "service"]
    assert [event["contribution_id"] for event in events] == [f"{slot}.interface.{method}" for method in methods]
    assert all(event["plan_id"] == plan.as_dict()["plan_id"] and event["status"] == "executed" for event in events)


def test_async_controls_preserve_order_short_circuit_and_observer_isolation():
    registry = HookRegistry()
    seen = []
    async def deny(ctx):
        await asyncio.sleep(0)
        seen.append("deny")
        return HookDecision("veto", reason="policy")
    async def lower(ctx):
        seen.append("must not run")
    async def observe(ctx):
        assert ctx.runtime is None and ctx.invocation is None
        ctx.tool_input["path"] = "modified"
        seen.append("observe")
    registry.register(HookEvent.TOOL_BEFORE, deny, mode="control", priority=20)
    registry.register(HookEvent.TOOL_BEFORE, lower, mode="control", priority=10)
    registry.register(HookEvent.TOOL_BEFORE, observe, mode="observer")
    context = HookContext(HookEvent.TOOL_BEFORE, "agent", tool_input={"path": "original"})
    assert asyncio.run(registry.atrigger(context.event, context)).action == "veto"
    assert seen == ["deny", "observe"]
    assert context.tool_input["path"] == "original"


def test_concurrent_requests_keep_results_local_and_do_not_execute_other_services(monkeypatch):
    module = "test_concurrent_memory"
    async def recall(value):
        await asyncio.sleep(0)
        return value
    async def archive(value):
        raise AssertionError("unrequested interface must not execute")
    spec = PluginSpec("memory", "enabled", "provider", f"{module}:create", ("recall", "archive"))
    monkeypatch.setitem(sys.modules, module, SimpleNamespace(create=lambda **kwargs: SimpleNamespace(recall=recall, archive=archive)))
    registry = HookRegistry()
    monkeypatch.setattr(hooks, "_registry", registry)
    manager = PluginManager((spec,))
    install_plan(discover_plan(manager, SimpleNamespace()), registry)
    provider = manager.load("memory", settings=SimpleNamespace())
    async def run():
        return await asyncio.gather(provider.recall("first"), provider.recall("second"))
    assert asyncio.run(run()) == ["first", "second"]
    registry.clear_managed()
    with pytest.raises(RuntimeError, match="not in the active"):
        asyncio.run(provider.recall("missing binding"))


def test_undeclared_capabilities_cannot_bypass_dispatch_and_teardown_remains_available(monkeypatch):
    module = "test_undeclared_service"
    monkeypatch.setitem(sys.modules, module, SimpleNamespace(create=lambda **kwargs: SimpleNamespace(
        ping=lambda: "ok", hidden=lambda: "bypass", close=lambda: "closed")))
    registry = HookRegistry()
    monkeypatch.setattr(hooks, "_registry", registry)
    spec = PluginSpec("team", "enabled", "provider", f"{module}:create", ("ping",))
    provider = PluginManager((spec,)).load("team", settings=SimpleNamespace())
    assert provider.ping() == "ok"
    with pytest.raises(RuntimeError, match="undeclared plugin interface"):
        provider.hidden()
    assert provider.close() == "closed"


def test_service_failure_and_cancellation_propagate_and_are_recorded(monkeypatch):
    module = "test_failed_memory"
    async def recall(value):
        if value == "cancel":
            raise asyncio.CancelledError()
        raise ValueError("bad query")
    spec = PluginSpec("memory", "enabled", "provider", f"{module}:create", ("recall",))
    monkeypatch.setitem(sys.modules, module, SimpleNamespace(create=lambda **kwargs: SimpleNamespace(recall=recall)))
    registry = HookRegistry()
    monkeypatch.setattr(hooks, "_registry", registry)
    manager = PluginManager((spec,))
    install_plan(discover_plan(manager, SimpleNamespace()), registry)
    provider = manager.load("memory", settings=SimpleNamespace())
    events = []
    token = set_current_trace_sink(SimpleNamespace(event=lambda kind, **data: events.append(data)))
    try:
        with pytest.raises(ValueError, match="bad query"):
            asyncio.run(provider.recall("error"))
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(provider.recall("cancel"))
    finally:
        reset_current_trace_sink(token)
    events = [event for event in events if event.get("mode") == "service"]
    assert [event["status"] for event in events] == ["error", "interrupted"]
    assert events[0]["error_message"] == "bad query"
    assert events[1]["error_type"] == "CancelledError"


def test_manifest_interface_phase_controls_the_actual_dispatch(monkeypatch, tmp_path):
    module = "test_manifest_service"
    monkeypatch.setitem(sys.modules, module, SimpleNamespace(create=lambda **kwargs: SimpleNamespace(query=lambda: "answer")))
    path = tmp_path / "plugins.json"
    path.write_text('{"schema_version":1,"plugins":[{"name":"team","provider":"test_manifest_service:create",'
                    '"interfaces":["query"],"interface_stages":{"query":"graph_query"}}]}', encoding="utf-8")
    manager = PluginManager(())
    manager.discover_specs(path)
    registry = HookRegistry()
    monkeypatch.setattr(hooks, "_registry", registry)
    plan = discover_plan(manager, SimpleNamespace())
    install_plan(plan, registry)
    events = []
    token = set_current_trace_sink(SimpleNamespace(event=lambda kind, **data: events.append(data)))
    try:
        assert manager.load("team", settings=SimpleNamespace()).query() == "answer"
    finally:
        reset_current_trace_sink(token)
    assert events[0]["stage"] == "tool_before"
    assert plan.plugins[0]["interface_bindings"] == [{"method": "query", "stage": "tool_before", "contribution_id": "team.interface.query"}]


def test_async_handler_is_awaited_in_real_agent_model_phase(monkeypatch):
    from app.agent_base.agents.react_agent import ReActAgent
    from app.agent_base.tools.registry import ToolRegistry
    registry = HookRegistry()
    monkeypatch.setattr(hooks, "_registry", registry)
    seen = []
    async def before(ctx):
        await asyncio.sleep(0)
        seen.append("before")
    async def finalize(ctx):
        await asyncio.sleep(0)
        seen.append("finalize")
    registry.register(HookEvent.LLM_BEFORE, before, mode="transform")
    registry.register(HookEvent.RUN_FINALIZE, finalize, mode="observer")
    class Model:
        async def ainvoke_with_tools(self, **kwargs):
            assert seen == ["before"]
            return {"content": "done", "tool_calls": None}
    agent = ReActAgent("test", Model(), ToolRegistry())
    assert asyncio.run(agent.arun("hello")) == "done"
    assert seen == ["before", "finalize"]
