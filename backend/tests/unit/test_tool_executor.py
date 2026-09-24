"""Tests for the provider-neutral tool execution boundary."""

import asyncio
from types import SimpleNamespace

from app.agent_base.execution import ToolExecutor
from app.agent_base.agents.react_runtime import tool_round_executor
from app.agent_base.agents.react_runtime.tool_round_executor import ToolRoundExecutor
from app.agent_base.tools.base import Tool, ToolParameter
from app.agent_base.tools.registry import ToolRegistry
from app.agent_base.tools.result import ToolResult
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
