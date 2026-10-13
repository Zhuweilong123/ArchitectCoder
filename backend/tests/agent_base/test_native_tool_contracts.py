"""Native tool boundaries reject malformed arguments before executing tools."""

import asyncio
import json

import pytest

from app.agent_base.agents.react_runtime.tool_round_executor import ToolRoundExecutor
from app.agent_base.core.hooks import AgentRuntime, HookRegistry, reset_runtime, set_runtime
from app.agent_base.host_api.lifecycle import HookEvent
from app.agent_base.tools.registry import ToolRegistry


@pytest.fixture
def executor():
    calls = []
    after = []
    registry = ToolRegistry()
    registry.register_function("echo", "Return input", lambda value: calls.append(value) or value)
    hooks = HookRegistry()
    hooks.register(HookEvent.TOOL_AFTER, lambda ctx: after.append(ctx), mode="observer")
    token = set_runtime(AgentRuntime())
    try:
        yield ToolRoundExecutor(registry, agent_name="test", hooks=hooks), calls, after
    finally:
        reset_runtime(token)


def execute(executor, raw):
    return asyncio.run(executor.execute([{
        "id": "call-1", "type": "function",
        "function": {"name": "echo", "arguments": raw},
    }], step=1))


@pytest.mark.parametrize("raw", ["[]", "true", "null", "1", '"text"', "NaN", '{"input": NaN}', "{"])
def test_non_object_or_invalid_json_never_reaches_tool(executor, raw):
    runner, calls, after = executor
    result = execute(runner, raw)
    assert not calls
    detail = result.details[0]
    assert detail["arguments"] == raw
    assert detail["status"] == "blocked"
    assert detail["error_code"] == "INVALID_ARGUMENTS"
    assert detail["execution_evidence"]["call_attempt"]["status"] == "blocked"
    assert len(after) == 1
    assert after[0].tool_input is None
    assert after[0].error_code == "INVALID_ARGUMENTS"
    assert result.tool_results[0]["tool_call_id"] == "call-1"
    assert "JSON" in result.tool_results[0]["content"]


def test_valid_object_preserves_tool_result_and_effects(executor):
    runner, calls, after = executor
    result = execute(runner, json.dumps({"input": "hello"}))
    assert calls == ["hello"]
    assert result.tool_results == [{"tool_call_id": "call-1", "content": "hello"}]
    detail = result.details[0]
    assert detail["status"] == "success"
    assert detail["arguments"] == {"input": "hello"}
    assert detail["changes"] == []
    assert detail["execution"] is None
    assert detail["verification"] is None
    assert detail["evidence"]["effects"]["execution_evidence"] == detail["execution_evidence"]
    assert after[0].tool_input == {"input": "hello"}
