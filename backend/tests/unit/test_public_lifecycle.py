"""Public boundaries remain stable while concurrent/nested operations are traced."""
import asyncio
from types import SimpleNamespace

import pytest

from app.agent_base.core import hooks
from app.agent_base.core.hooks import AgentRuntime, HookContext, HookDecision, HookEvent, HookRegistry, PUBLIC_STAGES
from app.agent_base.core.lifecycle import discover_plan
from app.agent_base.core.operations import current_operation, operation_scope
from app.agent_base.core.plugins import PluginManager, PluginSpec
from app.agent_base.core.plugin_dispatch import ScheduledProvider
from app.trace.tracing import set_current_trace_sink, reset_current_trace_sink


@pytest.fixture
def recording(monkeypatch):
    registry, events = HookRegistry(), []
    monkeypatch.setattr(hooks, "_registry", registry)
    token = set_current_trace_sink(SimpleNamespace(event=lambda kind, **data: events.append({"event_type": kind, **data})))
    runtime_token = hooks.set_runtime(AgentRuntime(run_id="parent"))
    try:
        yield registry, events
    finally:
        hooks.reset_runtime(runtime_token)
        reset_current_trace_sink(token)
    assert current_operation() is None


def test_exact_public_plan_and_alias_single_dispatch(recording):
    registry, _ = recording
    plan = discover_plan(PluginManager(()), SimpleNamespace()).as_dict()
    assert [row["stage"] for row in plan["stages"]] == [stage.value for stage in PUBLIC_STAGES]
    assert len(plan["stages"]) == 13
    assert {row["stage"] for row in plan["notifications"]} == {"error", "cancel", "review_after", "background_before", "background_after"}
    seen = []
    registry.register(HookEvent("llm_before"), lambda ctx: seen.append(ctx.event), mode="observer")
    registry.trigger(HookEvent.MODEL_BEFORE, HookContext(HookEvent.MODEL_BEFORE, "test"))
    assert seen == [HookEvent.MODEL_BEFORE]


def test_concurrent_plugin_calls_inherit_phase_scope_and_parent_without_phase_broadcast(recording):
    registry, events = recording
    async def recall(value):
        before = current_operation()
        await asyncio.sleep(0)
        assert current_operation() is before
        return value
    provider = ScheduledProvider(SimpleNamespace(recall=recall), PluginSpec("memory", "enabled", "provider", "test:create", ("recall",)))
    seen = []
    registry.register(HookEvent.PREPARE, lambda ctx: seen.append(ctx.event), mode="observer")
    async def run():
        with operation_scope("prepare", run_id="parent", stage="prepare", scope="request") as parent:
            registry.emit(HookEvent.PREPARE, HookContext(HookEvent.PREPARE, "test"))
            assert await asyncio.gather(provider.recall("first"), provider.recall("second")) == ["first", "second"]
            assert current_operation() is parent
            return parent.operation_id
    parent_id = asyncio.run(run())
    assert seen == [HookEvent.PREPARE]
    calls = [row for row in events if row["event_type"] == "plugin_contribution"]
    assert len(calls) == 2 and len({row["operation_id"] for row in calls}) == 2
    assert all(row["parent_operation_id"] == parent_id and row["stage"] == "prepare" and row["scope"] == "request" for row in calls)
    # Merely publishing a phase must not execute a service.
    assert asyncio.run(provider.recall("third")) == "third"
    assert seen == [HookEvent.PREPARE]


@pytest.mark.parametrize("outcome", ["success", "timeout", "error", "cancel", "blocked"])
def test_model_after_records_every_attempt_once(recording, outcome):
    from app.agent_base.agents.react_runtime.fc_loop import _invoke_fc_model
    registry, events = recording
    seen = []
    async def model(**kwargs):
        if outcome == "timeout":
            raise asyncio.TimeoutError()
        if outcome == "error":
            raise ValueError("failed request")
        if outcome == "cancel":
            raise asyncio.CancelledError()
        return {"content": "done"}
    if outcome == "blocked":
        registry.register(HookEvent.MODEL_BEFORE, lambda ctx: HookDecision("stop"), mode="control")
    registry.register(HookEvent.MODEL_AFTER, lambda ctx: seen.append(ctx.payload["status"]), mode="observer")
    request = _invoke_fc_model(SimpleNamespace(name="test", llm=SimpleNamespace(ainvoke_with_tools=model)),
        messages=[], tool_specs=[], finalization_mode=False, request_context={}, runtime=hooks.get_runtime(),
        temperature=0.3, timeout_seconds=1)
    if outcome in {"error", "cancel"}:
        with pytest.raises(ValueError if outcome == "error" else asyncio.CancelledError):
            asyncio.run(request)
    else:
        asyncio.run(request)
    status = {"success": "completed", "timeout": "failed", "error": "failed", "cancel": "cancelled", "blocked": "blocked"}[outcome]
    assert seen == [status]
    operation_events = [row for row in events if row["event_type"] == "operation"]
    assert [row["status"] for row in operation_events] == ["running", status]
    assert operation_events[-1]["stage"] == "model_after"


def test_stream_close_after_final_output_preserves_completed_run(recording):
    from app.agent_base.agents.react_agent import ReActAgent
    from app.agent_base.tools.registry import ToolRegistry
    registry, events = recording
    seen = []
    for stage in (HookEvent.FINALIZE, HookEvent.RUN_END):
        registry.register(stage, lambda ctx: seen.append((ctx.event, ctx.payload)), mode="observer")
    async def model(**kwargs):
        return {"content": "done", "tool_calls": None}
    agent = ReActAgent("test", SimpleNamespace(ainvoke_with_tools=model), ToolRegistry())
    assert asyncio.run(agent.arun("hello")) == "done"
    assert [stage for stage, _ in seen] == [HookEvent.FINALIZE, HookEvent.RUN_END]
    assert seen[-1][1]["execution_only"] is True
    assert [row["status"] for row in events if row["event_type"] == "operation" and row["operation_kind"] == "run"] == ["running", "completed"]


@pytest.mark.parametrize("outcome", ["blocked", "cancel"])
def test_tool_after_on_blocked_or_cancelled_attempt(recording, outcome):
    from app.agent_base.agents.react_runtime.tool_round_executor import ToolRoundExecutor
    registry, events = recording
    seen = []
    registry.register(HookEvent.TOOL_AFTER, lambda ctx: seen.append(ctx.tool_status or ctx.payload.get("status")), mode="observer")
    async def execute(*args):
        raise asyncio.CancelledError()
    executor = ToolRoundExecutor(SimpleNamespace(aexecute_tool_result_with_params=execute), agent_name="test")
    request = executor._execute_one("test", {}, "denied" if outcome == "blocked" else None)
    if outcome == "cancel":
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(request)
    else:
        assert asyncio.run(request)[2].status == "blocked"
    assert seen == ["blocked" if outcome == "blocked" else "cancelled"]
    assert events[-1]["status"] == ("blocked" if outcome == "blocked" else "cancelled")


@pytest.mark.parametrize("outcome", ["success", "timeout", "cancel"])
def test_child_operations_have_own_run_and_complete_boundaries(recording, tmp_path, outcome):
    from app.agent_base.tools.my_tools.subagent_tool import SpawnSubagentTool
    registry, events = recording
    seen = []
    for stage in PUBLIC_STAGES:
        registry.register(stage, lambda ctx: seen.append((ctx.event, ctx.run_id, ctx.payload)), mode="observer")
    async def model(**kwargs):
        if outcome == "timeout":
            raise asyncio.TimeoutError()
        if outcome == "cancel":
            raise asyncio.CancelledError()
        return {"content": "done", "tool_calls": None}
    tool = SpawnSubagentTool(llm=SimpleNamespace(ainvoke_with_tools=model), source_dir=str(tmp_path))
    async def run():
        with operation_scope("tool", run_id="parent", stage="tool_before") as parent:
            if outcome == "cancel":
                with pytest.raises(asyncio.CancelledError):
                    await tool._execute({"description": "inspect"})
            else:
                result = await tool._execute({"description": "inspect"})
                assert "done" in result if outcome == "success" else "timed out" in result
            return parent.operation_id
    parent_id = asyncio.run(run())
    phases = [stage for stage, _, _ in seen]
    assert phases.count(HookEvent.MODEL_AFTER) == 1
    assert phases[-3:] == [HookEvent.ROUND_AFTER, HookEvent.FINALIZE, HookEvent.RUN_END]
    assert all(run_id.startswith("parent/") for _, run_id, _ in seen)
    child = next(row for row in events if row.get("operation_kind") == "subagent" and row["status"] == "running")
    assert child["parent_operation_id"] == parent_id
    model_record = next(row for row in events if row.get("operation_kind") == "model" and row["status"] != "running")
    assert model_record["parent_operation_id"] == child["operation_id"]
    assert model_record["status"] == {"success": "completed", "timeout": "failed", "cancel": "cancelled"}[outcome]


def test_background_archive_links_closed_run_without_reopening_public_phase(recording):
    from app.services.agent_execution import _archive_task_to_memory
    registry, events = recording
    notifications = []
    registry.register(HookEvent.BACKGROUND_AFTER, lambda ctx: notifications.append(ctx.payload["status"]), mode="observer")
    async def archive(request):
        return SimpleNamespace(stored_count=1, metadata={})
    provider = ScheduledProvider(SimpleNamespace(archive=archive), PluginSpec("memory", "enabled", "provider", "test:create", ("archive",)))
    with operation_scope("run", run_id="parent") as parent:
        hooks.get_runtime().run_operation_id = parent.operation_id
    asyncio.run(_archive_task_to_memory(memory=provider, project_id="project", user_message="hello",
        final_answer="done", tool_calls_detail=[], run_id="parent"))
    background = next(row for row in events if row.get("operation_kind") == "background" and row["status"] == "running")
    assert background["parent_operation_id"] == parent.operation_id
    call = next(row for row in events if row["event_type"] == "plugin_contribution")
    assert call["parent_operation_id"] == background["operation_id"] and call["scope"] == "background"
    assert notifications == ["completed"]
