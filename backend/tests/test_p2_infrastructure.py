"""P2 治理基础设施回归。"""

import asyncio

from app.agent_base.tools.base import Tool
from app.agent_base.tools.registry import ToolRegistry
from app.agent_base.tools.result import ToolResult
from app.services.agent_metrics import AgentMetrics


class _StructuredTool(Tool):
    def __init__(self):
        super().__init__("structured", "structured result")

    def get_parameters(self):
        return []

    def run(self, parameters):
        return ToolResult.success({"value": 42})


class _LegacyErrorTool(Tool):
    def __init__(self):
        super().__init__("legacy_error", "legacy string error")

    def get_parameters(self):
        return []

    def run(self, parameters):
        return "Error: command is unavailable"


def test_registry_preserves_structured_tool_result():
    registry = ToolRegistry()
    registry.register_tool(_StructuredTool())

    async def run():
        return await registry.aexecute_tool_result_with_params("structured", {})

    result = asyncio.run(run())
    assert result.status == "success"
    assert result.data == {"value": 42}
    assert '"value": 42' in result.text


def test_registry_normalizes_legacy_error_text_to_structured_error():
    registry = ToolRegistry()
    registry.register_tool(_LegacyErrorTool())

    result = asyncio.run(registry.aexecute_tool_result_with_params("legacy_error", {}))

    assert result.status == "error"
    assert result.error_code == "TOOL_REPORTED_ERROR"


def test_agent_metrics_snapshot_and_reset():
    metrics = AgentMetrics()
    metrics.record_tool("read_file", "success", 12.5)
    metrics.record_run("success")
    metrics.record_prompt(120, prompt_version="devagent-3.1", compacted_tokens=8)
    snapshot = metrics.snapshot()
    assert snapshot["tool_calls_total"] == 1
    assert snapshot["tool_calls_success"] == 1
    assert snapshot["runs_success"] == 1
    assert snapshot["tool_latency_total_ms"] == 12.5
    assert snapshot["prompt_builds_total"] == 1
    assert snapshot["prompt_tokens_total"] == 120
    assert snapshot["prompt_compacted_tokens_total"] == 8
    assert snapshot["prompt_version_devagent-3.1_total"] == 1
    metrics.reset()
    assert metrics.snapshot() == {"tool_latency_total_ms": 0.0}
