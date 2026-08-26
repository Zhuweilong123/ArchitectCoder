"""ReActAgent 单元测试（Mock LLM，无需真实 API）+ Hook 机制测试。"""
import asyncio
import json

import pytest

from app.agent_base.agents.react_agent import ReActAgent
from app.agent_base.core.hooks import (
    get_hooks, HookEvent, HookContext, AgentRuntime, set_runtime, reset_runtime,
    TruncateHook,
)
from app.agent_base.core.exceptions import AgentInterrupted
from app.agent_base.tools.base import Tool, ToolParameter
from app.agent_base.tools.registry import ToolRegistry


class EchoTool(Tool):
    def __init__(self):
        super().__init__(name="echo", description="回显输入文本")

    def get_parameters(self):
        return [ToolParameter(name="text", type="string", description="要回显的文本")]

    def run(self, parameters):
        return f"回显: {parameters.get('text', '')}"


class MockLLM:
    """前 ``rounds`` 次返回 tool_call，之后返回纯文本终止循环。"""

    def __init__(self, rounds=2):
        self.rounds = rounds
        self.count = 0

    async def ainvoke_with_tools(self, messages, tools, tool_choice="auto", **kwargs):
        self.count += 1
        if self.count <= self.rounds:
            return {
                "content": f"Step {self.count}",
                "tool_calls": [{
                    "id": f"c{self.count}", "type": "function",
                    "function": {
                        "name": "echo",
                        "arguments": json.dumps({"text": f"round_{self.count}"}),
                    },
                }],
            }
        return {"content": "完成", "tool_calls": None}


def _registry():
    reg = ToolRegistry()
    reg.register_tool(EchoTool())
    return reg


async def _collect(agent):
    events = []
    async for p in agent.arun_stream("echo test"):
        events.append(p)
    return events


def _first_tool_detail(events):
    """取第一个含 tool_calls_detail 的进度快照里的首条工具详情。"""
    for e in events:
        if e.tool_calls_detail:
            return e.tool_calls_detail[0]
    return None


def test_react_agent_fc_loop_executes_tools():
    llm = MockLLM(rounds=2)
    agent = ReActAgent("Test", llm, _registry(), max_steps=10)

    events = asyncio.run(_collect(agent))

    assert llm.count == 3  # 2 次工具调用 + 1 次最终
    assert events[-1].is_final is True
    assert events[-1].final_answer == "完成"


def test_interrupt_hook_stops():
    llm = MockLLM(rounds=10)
    agent = ReActAgent("Test", llm, _registry(), max_steps=10)

    async def _run():
        token = set_runtime(AgentRuntime(stop_check=lambda: llm.count >= 2))
        try:
            with pytest.raises(AgentInterrupted):
                async for _ in agent.arun_stream("echo several times"):
                    pass
        finally:
            reset_runtime(token)

    asyncio.run(_run())
    assert llm.count == 2  # 第 2 轮 LLM 调用后的 tool_before 中断


def test_truncate_hook_replaces_fed_observation():
    llm = MockLLM(rounds=1)
    agent = ReActAgent("Test", llm, _registry(), max_steps=10)

    truncator = TruncateHook(10)
    get_hooks().register(HookEvent.TOOL_AFTER, truncator, priority=200)
    try:
        events = asyncio.run(_collect(agent))
    finally:
        get_hooks().unregister(HookEvent.TOOL_AFTER, truncator)

    detail = _first_tool_detail(events)
    assert detail is not None
    assert detail["fed_truncated"] is True
    # 正文被切到 10 字符，另加显式截断标记（标记有意超出 max_chars）
    assert 10 < detail["fed_length"] <= 10 + 120


def test_truncate_hook_appends_explicit_marker():
    """截断必须留下标记 — 否则模型会把腰斩内容当成完整内容，
    进而基于不存在的文本构造 edit_file 的 old_string。"""
    hook = TruncateHook(10)
    ctx = HookContext(
        event=HookEvent.TOOL_AFTER, agent_name="Test",
        tool_name="read_file", tool_output="x" * 500,
    )
    out = hook(ctx)

    assert out.startswith("x" * 10)
    assert "x" * 11 not in out          # 正文确实只保留了 max_chars
    assert "truncated" in out
    assert "500" in out                 # 如实报告了原始长度


def test_truncate_hook_passes_through_short_output():
    """未超长时返回 None（放行原文），不应误加标记。"""
    hook = TruncateHook(10)
    ctx = HookContext(
        event=HookEvent.TOOL_AFTER, agent_name="Test",
        tool_name="read_file", tool_output="short",
    )
    assert hook(ctx) is None


def test_veto_hook_blocks_tool():
    llm = MockLLM(rounds=1)
    agent = ReActAgent("Test", llm, _registry(), max_steps=10)

    def veto(ctx: HookContext):
        return "blocked by policy"

    get_hooks().register(HookEvent.TOOL_BEFORE, veto, priority=200)
    try:
        events = asyncio.run(_collect(agent))
    finally:
        get_hooks().unregister(HookEvent.TOOL_BEFORE, veto)

    detail = _first_tool_detail(events)
    assert detail is not None
    assert detail["observation"] == "blocked by policy"
    assert detail["fed_truncated"] is False
