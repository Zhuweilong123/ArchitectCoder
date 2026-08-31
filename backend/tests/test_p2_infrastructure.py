"""P2 治理基础设施回归。"""

from types import SimpleNamespace

import asyncio

from app.agent_base.tools.base import Tool
from app.agent_base.tools.registry import ToolRegistry
from app.agent_base.tools.result import ToolResult
from app.services.agent_metrics import AgentMetrics
from app.services.model_router import choose_model


class _StructuredTool(Tool):
    def __init__(self):
        super().__init__("structured", "structured result")

    def get_parameters(self):
        return []

    def run(self, parameters):
        return ToolResult.success({"value": 42})


def test_registry_preserves_structured_tool_result():
    registry = ToolRegistry()
    registry.register_tool(_StructuredTool())

    async def run():
        return await registry.aexecute_tool_result_with_params("structured", {})

    result = asyncio.run(run())
    assert result.status == "success"
    assert result.data == {"value": 42}
    assert '"value": 42' in result.text


def test_model_router_selects_flash_for_simple_and_pro_for_complex():
    settings = SimpleNamespace(
        deepseek_model="pro-model",
        deepseek_model_flash="flash-model",
    )
    assert choose_model("你好", settings).model == "flash-model"
    assert choose_model("请分析 UML 架构并修复代码", settings).model == "pro-model"


def test_agent_metrics_snapshot_and_reset():
    metrics = AgentMetrics()
    metrics.record_tool("read_file", "success", 12.5)
    metrics.record_run("success")
    snapshot = metrics.snapshot()
    assert snapshot["tool_calls_total"] == 1
    assert snapshot["tool_calls_success"] == 1
    assert snapshot["runs_success"] == 1
    assert snapshot["tool_latency_total_ms"] == 12.5
    metrics.reset()
    assert metrics.snapshot() == {"tool_latency_total_ms": 0.0}

