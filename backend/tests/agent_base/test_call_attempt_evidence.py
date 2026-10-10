"""Failed queries cannot acquire a fabricated successful retry record."""

import asyncio
import json

from app.agent_base.agents.react_runtime.tool_round_executor import ToolRoundExecutor
from app.agent_base.execution_summary import build_task_execution_summary
from app.agent_base.tools.base import Tool, ToolParameter
from app.agent_base.tools.registry import ToolRegistry
from app.agent_base.tools.result import ToolResult


class QueryTool(Tool):
    def __init__(self):
        super().__init__(name="query", description="Query fixture")
        self.failed = False

    def get_parameters(self):
        return [ToolParameter(name="path", type="string", description="Target")]

    def run(self, parameters):
        return self.run_result(parameters).text

    def run_result(self, parameters):
        if not self.failed:
            self.failed = True
            return ToolResult.error("Error: scan incomplete", "SEARCH_IO_ERROR", True)
        return ToolResult.success("found")


def call(path, call_id):
    return {"id": call_id, "type": "function", "function": {
        "name": "query", "arguments": json.dumps({"path": path}),
    }}


def test_alternative_query_is_separate_from_successful_exact_retry():
    registry = ToolRegistry()
    registry.register_tool(QueryTool())
    executor = ToolRoundExecutor(registry, agent_name="Test")
    failed = asyncio.run(executor.execute([call("workspace", "failed")], step=1))
    alternative = asyncio.run(executor.execute([call("engine", "alternative")], step=2))
    details = failed.details + alternative.details
    assert alternative.details[0]["execution_evidence"]["call_attempt"]["exact_retry_of"] is None
    summary = build_task_execution_summary(details, {}, "completed")
    assert "no recorded retry with the same tool and arguments" in summary
    retry = asyncio.run(executor.execute([call("workspace", "retried")], step=3))
    assert retry.details[0]["execution_evidence"]["call_attempt"]["exact_retry_of"] == "failed"
    assert '"exact_retry_of": "failed"' in retry.tool_results[0]["content"]
    summary = build_task_execution_summary(details + retry.details, {}, "completed")
    assert "exact retries=[3] success" in summary
    assert "exact retries=[2]" not in summary


def test_attempt_records_do_not_leak_to_a_new_task_executor():
    registry = ToolRegistry()
    registry.register_tool(QueryTool())
    previous = ToolRoundExecutor(registry, agent_name="Test")
    asyncio.run(previous.execute([call("workspace", "old")], step=1))
    current = ToolRoundExecutor(registry, agent_name="Test")
    result = asyncio.run(current.execute([call("workspace", "new")], step=2))
    assert result.details[0]["execution_evidence"]["call_attempt"]["exact_retry_of"] is None


def test_calls_requested_together_are_not_recovery_retries():
    registry = ToolRegistry()
    registry.register_tool(QueryTool())
    executor = ToolRoundExecutor(registry, agent_name="Test")
    result = asyncio.run(executor.execute([
        call("workspace", "first"), call("workspace", "second"),
    ], step=1))
    assert result.details[1]["execution_evidence"]["call_attempt"]["exact_retry_of"] is None
    summary = build_task_execution_summary(result.details, {}, "completed")
    assert "no recorded retry with the same tool and arguments" in summary
