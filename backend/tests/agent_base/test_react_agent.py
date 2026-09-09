"""ReActAgent 单元测试（Mock LLM，无需真实 API）+ Hook 机制测试。"""
import asyncio
import json

import pytest

from app.agent_base.agents.react_agent import ReActAgent
from app.agent_base.core.hooks import (
    get_hooks, HookEvent, HookContext, AgentRuntime, set_runtime, reset_runtime,
    HookAction, HookDecision, TruncateHook,
)
from app.agent_base.core.policy import ExecutionBudget
from app.agent_base.core.exceptions import AgentInterrupted
from app.agent_base.tools.base import Tool, ToolParameter
from app.agent_base.tools.registry import ToolRegistry
from app.agent_base.tools.my_tools.todo_tools import TodoWriteTool
from app.services.context_manager import ContextBudget, ContextBudgetManager


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


class BatchTodoLLM:
    """First response creates a plan and uses a business tool in one batch."""

    def __init__(self):
        self.count = 0

    async def ainvoke_with_tools(self, messages, tools, tool_choice="auto", **kwargs):
        self.count += 1
        if self.count == 1:
            return {
                "content": "working",
                "tool_calls": [
                    {"id": "todo", "type": "function", "function": {
                        "name": "todo_write",
                        "arguments": json.dumps({"todos": [
                            {"content": "inspect", "status": "completed"},
                            {"content": "echo", "status": "in_progress"},
                            {"content": "verify", "status": "pending"},
                        ]}),
                    }},
                    {"id": "echo", "type": "function", "function": {
                        "name": "echo", "arguments": json.dumps({"text": "x"}),
                    }},
                ],
            }
        return {"content": "完成", "tool_calls": None}


class BudgetLLM:
    def __init__(self):
        self.count = 0

    async def ainvoke_with_tools(self, messages, tools, tool_choice="auto", **kwargs):
        self.count += 1
        return {"content": "not finished", "tool_calls": None,
                "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}}


class SoftBudgetLLM:
    def __init__(self):
        self.count = 0
        self.requests = []

    async def ainvoke_with_tools(self, messages, tools, tool_choice="auto", **kwargs):
        self.count += 1
        self.requests.append(messages)
        return {
            "content": "continue",
            "tool_calls": [{
                "id": f"soft-{self.count}", "type": "function",
                "function": {"name": "echo", "arguments": json.dumps({"text": "x"})},
            }],
            "usage": {"prompt_tokens": 35, "completion_tokens": 5, "total_tokens": 40},
        }


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
    agent = ReActAgent("Test", llm, _registry())

    events = asyncio.run(_collect(agent))

    assert llm.count == 3  # 2 次工具调用 + 1 次最终
    assert events[-1].is_final is True
    assert events[-1].final_answer == "完成"


def test_react_progress_exposes_structured_tool_evidence():
    agent = ReActAgent("Test", MockLLM(rounds=1), _registry())

    events = asyncio.run(_collect(agent))

    detail = _first_tool_detail(events)
    assert detail is not None
    assert detail["evidence"]["id"] == "E1"
    assert detail["evidence"]["tool_name"] == "echo"
    assert "duration_ms" in detail


def test_usage_budget_keeps_a_text_final_answer_from_the_current_response():
    llm = BudgetLLM()
    agent = ReActAgent("Test", llm, _registry(), max_total_tokens=10)

    events = asyncio.run(_collect(agent))

    assert llm.count == 1
    assert events[-1].is_final is True
    assert events[-1].final_answer == "not finished"
    assert agent.last_context_report["token_budget_used"] == 10
    assert agent.last_context_report["token_budget_stop_reason"] == "model_answer"


def test_usage_budget_executes_current_tool_calls_before_stopping():
    class ToolAtLimitLLM:
        async def ainvoke_with_tools(self, messages, tools, tool_choice="auto", **kwargs):
            return {
                "content": "",
                "tool_calls": [{
                    "id": "at-limit", "type": "function",
                    "function": {"name": "echo", "arguments": json.dumps({"text": "kept"})},
                }],
                "usage": {"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10},
            }

    agent = ReActAgent("Test", ToolAtLimitLLM(), _registry(), max_total_tokens=10)

    events = asyncio.run(_collect(agent))

    assert events[-1].is_final is True
    assert events[-1].actions == ["echo"]
    assert "已执行本轮已返回的工具调用" in events[-1].final_answer


def test_budget_reserve_requests_a_tool_free_final_answer():
    class ReserveLLM:
        def __init__(self):
            self.requests = []

        async def ainvoke_with_tools(self, messages, tools, tool_choice="auto", **kwargs):
            self.requests.append((tools, tool_choice))
            if len(self.requests) == 1:
                return {
                    "content": "working",
                    "tool_calls": [{
                        "id": "work", "type": "function",
                        "function": {"name": "echo", "arguments": json.dumps({"text": "done"})},
                    }],
                    "usage": {"prompt_tokens": 70, "completion_tokens": 20, "total_tokens": 90},
                }
            return {
                "content": "任务已完成，已执行 echo。",
                "tool_calls": None,
                "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
            }

    llm = ReserveLLM()
    agent = ReActAgent(
        "Test", llm, _registry(), max_total_tokens=100,
        token_finalization_reserve_tokens=20,
    )

    events = asyncio.run(_collect(agent))

    assert events[-1].final_answer == "任务已完成，已执行 echo。"
    assert llm.requests[1] == ([], "none")


def test_budget_finalization_blocks_textual_tool_markup():
    class MarkupLLM:
        def __init__(self):
            self.requests = []

        async def ainvoke_with_tools(self, messages, tools, tool_choice="auto", **kwargs):
            self.requests.append((tools, tool_choice))
            if len(self.requests) == 1:
                return {
                    "content": "working",
                    "tool_calls": [{
                        "id": "work", "type": "function",
                        "function": {"name": "echo", "arguments": json.dumps({"text": "done"})},
                    }],
                    "usage": {"total_tokens": 90},
                }
            return {
                "content": (
                    "Need one more read.\n"
                    "<｜｜DSML｜｜tool_calls><｜｜DSML｜｜invoke name=\"read_file\">"
                    "ignored</｜｜DSML｜｜invoke></｜｜DSML｜｜tool_calls>"
                ),
                "tool_calls": None,
                "usage": {"total_tokens": 1},
            }

    agent = ReActAgent(
        "Test", MarkupLLM(), _registry(), max_total_tokens=100,
        token_finalization_reserve_tokens=20,
    )

    events = asyncio.run(_collect(agent))

    assert "DSML" not in events[-1].final_answer
    assert "未执行" in events[-1].final_answer
    assert agent.last_context_report["token_budget_stop_reason"] == "reserve_finalization"
    assert agent.last_context_report["finalization_textual_tool_markup_blocked"] is True


def test_legacy_step_argument_does_not_limit_open_fc_loop():
    class StepLimitLLM:
        def __init__(self):
            self.requests = []

        async def ainvoke_with_tools(self, messages, tools, tool_choice="auto", **kwargs):
            self.requests.append((tools, tool_choice, kwargs.get("max_tokens")))
            count = len(self.requests)
            if count <= 2:
                return {
                    "content": "working",
                    "tool_calls": [{
                        "id": f"work-{count}", "type": "function",
                        "function": {"name": "echo", "arguments": json.dumps({"text": str(count)})},
                    }],
                }
            return {"content": "completed two work steps", "tool_calls": None}

    llm = StepLimitLLM()
    agent = ReActAgent(
        "Test", llm, _registry(), final_summary_max_tokens=321,
    )

    events = asyncio.run(_collect(agent))

    assert len(llm.requests) == 3
    assert llm.requests[-1][0] != []
    assert llm.requests[-1][1] == "auto"
    assert llm.requests[-1][2] is None
    assert events[-1].step == 3
    assert events[-1].final_answer == "completed two work steps"
    assert agent.last_context_report["convergence_policy"]["open_ended_loop"] is True


def test_soft_budget_instructs_the_next_step_to_converge():
    llm = SoftBudgetLLM()
    agent = ReActAgent(
        "Test", llm, _registry(), max_total_tokens=100,
        token_finalization_reserve_tokens=1,
    )

    events = asyncio.run(_collect(agent))

    assert llm.count == 3
    assert "token" in events[-1].final_answer
    assert any(
        message.get("role") == "system" and "Token budget warning" in message.get("content", "")
        for message in llm.requests[2]
    )


def test_open_fc_loop_finalizes_after_repeated_non_progressing_action():
    class RepeatingLLM:
        def __init__(self):
            self.count = 0

        async def ainvoke_with_tools(self, messages, tools, tool_choice="auto", **kwargs):
            self.count += 1
            return {
                "content": "stuck",
                "tool_calls": [{
                    "id": f"repeat-{self.count}", "type": "function",
                    "function": {"name": "echo", "arguments": json.dumps({"text": "same"})},
                }],
            }

    llm = RepeatingLLM()
    agent = ReActAgent(
        "Test", llm, _registry(), max_total_tokens=1000,
        convergence_max_stalled_rounds=3,
        convergence_max_recovery_rounds=2,
        convergence_repeat_action_threshold=3,
    )

    events = asyncio.run(_collect(agent))

    assert events[-1].is_final is True
    assert agent.last_context_report["token_budget_stop_reason"] in {
        "repeated_action", "convergence_stalled",
    }
    assert llm.count < 10


def test_context_compaction_is_reported_to_observers():
    llm = MockLLM(rounds=9)
    agent = ReActAgent(
        "Test", llm, _registry(),
        context_budget=ContextBudgetManager(ContextBudget(
            max_context_tokens=300,
            output_reserve_tokens=10,
            max_history_tokens=20,
            compaction_trigger_ratio=0.1,
        )),
    )
    reports = []
    agent.on_context_compacted = reports.append

    events = asyncio.run(_collect(agent))

    assert events[-1].final_answer == "完成"
    assert any(report["dropped_messages"] > 0 for report in reports)


def test_todo_then_business_tool_in_same_batch_is_not_false_blocked():
    llm = BatchTodoLLM()
    registry = _registry()
    registry.register_tool(TodoWriteTool())
    agent = ReActAgent("Test", llm, registry)
    runtime_token = set_runtime(AgentRuntime(requires_todo_plan=True))
    try:
        events = asyncio.run(_collect(agent))
    finally:
        reset_runtime(runtime_token)

    details = [d for event in events for d in event.tool_calls_detail]
    assert any(d["name"] == "echo" and d["status"] == "success" for d in details)


def test_acceptance_contract_blocks_premature_final_answer():
    llm = MockLLM(rounds=0)
    agent = ReActAgent("Test", llm, _registry())
    runtime_token = set_runtime(AgentRuntime(requires_acceptance_todos=True))
    try:
        events = asyncio.run(_collect(agent))
    finally:
        reset_runtime(runtime_token)

    assert llm.count == 4
    assert events[0].is_final is False
    assert events[-1].is_final is True
    assert "required todo plan" in events[-1].final_answer


def test_task_plan_blocks_other_tools_until_todo_exists():
    llm = MockLLM(rounds=1)
    agent = ReActAgent("Test", llm, _registry())
    runtime_token = set_runtime(AgentRuntime(requires_todo_plan=True))
    try:
        events = asyncio.run(_collect(agent))
    finally:
        reset_runtime(runtime_token)

    detail = _first_tool_detail(events)
    assert detail is not None
    assert "Call todo_write first" in detail["observation"]


def test_interrupt_hook_stops():
    llm = MockLLM(rounds=10)
    agent = ReActAgent("Test", llm, _registry())

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
    agent = ReActAgent("Test", llm, _registry())

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
    agent = ReActAgent("Test", llm, _registry())

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


def test_execution_budget_policy_is_owned_by_hook():
    budget = ExecutionBudget(max_tool_calls=2, max_run_seconds=60, max_total_tokens=1000)
    runtime = AgentRuntime(execution_budget=budget)
    runtime_token = set_runtime(runtime)
    try:
        get_hooks().trigger(
            HookEvent.RUN_START,
            HookContext(event=HookEvent.RUN_START, agent_name="Test", runtime=runtime),
        )
        ctx = HookContext(
            event=HookEvent.TOOL_BEFORE,
            agent_name="Test",
            tool_name="read_file",
            tool_input={"path": "src/a.py"},
            runtime=runtime,
        )
        assert get_hooks().trigger(HookEvent.TOOL_BEFORE, ctx) is None
        assert get_hooks().trigger(HookEvent.TOOL_BEFORE, ctx) is None
        blocked = get_hooks().trigger(HookEvent.TOOL_BEFORE, ctx)
    finally:
        reset_runtime(runtime_token)

    assert isinstance(blocked, HookDecision)
    assert blocked.action == HookAction.VETO
    assert blocked.reason == "tool_call_limit"
    assert budget.tool_call_count == 2


def test_hook_registry_emit_broadcasts_without_short_circuiting():
    calls = []

    def first(ctx: HookContext):
        calls.append("first")
        return HookDecision(action=HookAction.RECOVER, reason="recover")

    def second(ctx: HookContext):
        calls.append("second")
        return None

    get_hooks().register(HookEvent.RUN_END, first, priority=200)
    get_hooks().register(HookEvent.RUN_END, second, priority=100)
    try:
        results = get_hooks().emit(
            HookEvent.RUN_END,
            HookContext(event=HookEvent.RUN_END, agent_name="Test"),
        )
    finally:
        get_hooks().unregister(HookEvent.RUN_END, first)
        get_hooks().unregister(HookEvent.RUN_END, second)

    assert calls == ["first", "second"]
    assert len(results) == 1
