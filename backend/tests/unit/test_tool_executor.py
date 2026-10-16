"""Tests for the provider-neutral tool execution boundary."""

import asyncio
import inspect
from types import SimpleNamespace

import pytest

from app.agent_base.execution import ToolExecutor
from app.agent_base.agents.react_runtime import tool_round_executor
from app.agent_base.agents.react_runtime.tool_round_executor import ToolRoundExecutor
from app.agent_base.tools.base import Tool, ToolParameter
from app.agent_base.tools.registry import ToolRegistry
from app.agent_base.tools.result import ToolResult
from app.agent_base.tools.async_tool import AsyncTool
from app.agent_base.host_api.errors import AgentInterrupted
from app.core.capabilities import CapabilityPolicy


class _EchoTool(Tool):
    def __init__(self):
        super().__init__("echo", "echo input")

    def get_parameters(self):
        return [ToolParameter(name="value", type="string", description="value")]

    def run(self, parameters):
        return parameters.get("value", "")


class _AsyncTool(Tool):
    def __init__(self):
        super().__init__("async_echo", "async echo")

    def get_parameters(self):
        return []

    async def run(self, parameters):
        return ToolResult.success(parameters.get("value", ""))


class _NativeAsyncTool(AsyncTool):
    async def _execute(self, parameters):
        return parameters.get("input", "")


def test_native_async_tool_supports_direct_await_and_registry_execution():
    tool = _NativeAsyncTool("native_async", "echo asynchronously")
    registry = ToolRegistry()
    registry.register_tool(tool)
    assert asyncio.run(tool.run({"input": "direct"})) == "direct"
    result = asyncio.run(registry.aexecute_tool_result_with_params("native_async", {"input": "registered"}))
    assert result.status == "success"
    assert result.data == "registered"


@pytest.mark.parametrize("function_tool", [False, True])
@pytest.mark.parametrize("string_api", [False, True])
def test_sync_rejection_closes_coroutines_returned_by_wrappers(function_tool, string_api):
    registry = ToolRegistry()
    created, executed = [], []

    async def body():
        executed.append(True)
        return "done"

    def wrapper(value):
        coroutine = body()
        created.append(coroutine)
        return coroutine

    if function_tool:
        registry.register_function("wrapped", "wrapped coroutine", wrapper)
    else:
        tool = _EchoTool()
        tool.name = "wrapped"
        tool.run = wrapper
        registry.register_tool(tool)
    result = (registry.execute_tool("wrapped", "input") if string_api else
              registry.execute_tool_with_params("wrapped", {"input": "input"}))
    assert "async tool" in result
    assert executed == []
    assert len(created) == 1
    assert inspect.getcoroutinestate(created[0]) == inspect.CORO_CLOSED


@pytest.mark.parametrize("function_tool", [False, True])
@pytest.mark.parametrize("async_api", [False, True])
def test_user_interruption_propagates_through_tools_and_functions(function_tool, async_api):
    registry = ToolRegistry()

    def interrupt(value):
        raise AgentInterrupted("user stopped")

    if function_tool:
        registry.register_function("interrupt", "interrupt", interrupt)
    else:
        tool = _EchoTool()
        tool.name = "interrupt"
        tool.run = interrupt
        registry.register_tool(tool)
    with pytest.raises(AgentInterrupted, match="user stopped"):
        if async_api:
            asyncio.run(registry.aexecute_tool_result_with_params("interrupt", {}))
        else:
            registry.execute_tool_with_params("interrupt", {})


def test_async_function_cancellation_propagates_without_running_twice():
    calls = []

    async def cancel(value):
        calls.append(value)
        raise asyncio.CancelledError()

    registry = ToolRegistry()
    registry.register_function("cancel", "cancel", cancel)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(registry.aexecute_tool_result_with_params("cancel", {"input": "once"}))
    assert calls == ["once"]


@pytest.mark.parametrize("async_api", [False, True])
def test_function_rejects_non_string_input_before_invocation(async_api):
    registry = ToolRegistry()
    calls = []
    registry.register_function("text", "text input", lambda value: calls.append(value) or value)
    if async_api:
        result = asyncio.run(registry.aexecute_tool_result_with_params("text", {"input": {"unexpected": 1}}))
        assert result.error_code == "INVALID_ARGUMENT"
    else:
        assert "input must be a string" in registry.execute_tool_with_params("text", {"input": 42})
    assert calls == []


def test_function_error_text_is_normalized_like_class_tool_error_text():
    registry = ToolRegistry()
    registry.register_function("failed", "failed", lambda value: "Error: missing data")
    result = asyncio.run(registry.aexecute_tool_result_with_params("failed", {}))
    assert result.status == "error"
    assert result.error_code == "TOOL_REPORTED_ERROR"


def test_structured_tool_hook_preserves_error_metadata():
    expected = ToolResult.error("stale content", "STALE_SHA", True)
    tool = _EchoTool()
    tool.run_result = lambda parameters: expected
    registry = ToolRegistry()
    registry.register_tool(tool)
    assert asyncio.run(registry.aexecute_tool_result_with_params("echo", {})) is expected


def test_function_can_return_a_custom_awaitable():
    executions = []

    class Deferred:
        def __await__(self):
            async def resolve():
                executions.append("resolved")
                return {"answer": 42}
            return resolve().__await__()

    registry = ToolRegistry()
    registry.register_function("deferred", "deferred JSON result", lambda value: Deferred())
    result = asyncio.run(registry.aexecute_tool_result_with_params("deferred", {}))
    assert result.data == {"answer": 42}
    assert executions == ["resolved"]


def test_registry_owns_replaceable_executor():
    registry = ToolRegistry()
    assert isinstance(registry.executor, ToolExecutor)
    registry.register_tool(_EchoTool())

    assert registry.executor.tools is registry._tools
    assert registry.execute_tool_with_params("echo", {"value": "ok"}) == "ok"


def test_executor_supports_async_structured_results():
    registry = ToolRegistry()
    registry.register_tool(_AsyncTool())

    result = asyncio.run(
        registry.executor.aexecute_tool_result_with_params(
            "async_echo", {"value": "ok"},
        )
    )

    assert result.status == "success"
    assert result.data == "ok"


def test_executor_applies_capability_policy_before_lookup():
    policy = CapabilityPolicy(allowed_tools=[])
    executor = ToolExecutor({}, {}, policy)

    result = asyncio.run(
        executor.aexecute_tool_result_with_params("unknown", {}),
    )

    assert result.status == "blocked"
    assert result.error_code == "POLICY_BLOCKED"


def test_react_tool_trace_spans_are_emitted_at_execution_boundary(monkeypatch):
    registry = ToolRegistry()
    registry.register_tool(_EchoTool())
    runtime = SimpleNamespace(
        requires_todo_plan=False,
        todos=[],
        execution_budget=None,
        run_id="run-trace",
        control_decision=None,
    )
    events = []

    def record(kind, **payload):
        events.append((kind, payload))
        return "span-1" if kind == "tool_call" else None

    monkeypatch.setattr(tool_round_executor, "get_runtime", lambda: runtime)
    monkeypatch.setattr(tool_round_executor, "emit_trace", record)

    result = asyncio.run(ToolRoundExecutor(
        registry,
        agent_name="test",
    ).execute([
        {
            "id": "call-1",
            "function": {"name": "echo", "arguments": '{"value":"ok"}'},
        },
    ], step=1))

    assert result.details[0]["status"] == "success"
    assert [kind for kind, _ in events] == ["tool_call", "tool_result"]
    assert events[0][1]["tool_name"] == "echo"
    assert events[1][1]["span_id"] == "span-1"
