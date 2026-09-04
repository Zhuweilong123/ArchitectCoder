"""P0/P1 基础设施回归：trace 生命周期、任务隔离与 agent 预算。"""

import asyncio
import json

from app.agent_base.agents.react_agent import ReActAgent
from app.agent_base.tools.base import Tool, ToolParameter
from app.agent_base.tools.registry import ToolRegistry
from app.agent_base.tools.task_system import _default_dirs
from extensions.trace.chat_trace import (
    ChatTraceLogger,
    EVT_SESSION_END,
    EVT_TASK_SUMMARY,
)
from extensions.trace.trace_reader import reconstruct_history, summarize_trace


class _Echo(Tool):
    def __init__(self):
        super().__init__("echo", "echo")

    def get_parameters(self):
        return [ToolParameter(name="text", type="string", description="text")]

    def run(self, parameters):
        return parameters.get("text", "")


class _LoopLLM:
    async def ainvoke_with_tools(self, messages, tools, **kwargs):
        return {
            "content": "call",
            "tool_calls": [{
                "id": "c1", "type": "function",
                "function": {"name": "echo", "arguments": json.dumps({"text": "x"})},
            }],
        }


def test_trace_close_writes_session_end(tmp_path, monkeypatch):
    monkeypatch.setattr("extensions.trace.chat_trace._chat_log_dir", lambda: str(tmp_path))
    tracer = ChatTraceLogger("trace-test")
    tracer.start()
    tracer.close()
    events = [json.loads(line) for line in (tmp_path / "trace_trace-test.jsonl").read_text(encoding="utf-8").splitlines()]
    assert events[-1]["event_type"] == EVT_SESSION_END


def test_trace_keeps_runtime_system_messages_and_strict_jsonl(tmp_path, monkeypatch):
    monkeypatch.setattr("extensions.trace.chat_trace._chat_log_dir", lambda: str(tmp_path))
    tracer = ChatTraceLogger("strict-json-test")
    tracer.llm_request(
        provider="test", model="test",
        messages=[
            {"role": "system", "content": "stable prompt"},
            {"role": "system", "content": "## Budget finalization\nDo not call tools."},
            {"role": "user", "content": "finish"},
        ],
        temperature=0.3, max_tokens=None,
    )
    tracer.event("numeric", value=float("nan"))

    events = [
        json.loads(line)
        for line in (tmp_path / "trace_strict-json-test.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    assert events[0]["system_prompt"] == "stable prompt"
    assert events[0]["messages"][0]["content"].startswith("## Budget finalization")
    assert events[0]["prompt_structure"] == {
        "total_messages": 3,
        "stable_system_prompt": True,
        "conversation_messages": 2,
        "role_sequence": ["system", "system", "user"],
        "task_execution_summary_count": 0,
        "conversation_checkpoint_count": 0,
        "tool_call_message_count": 0,
        "tool_result_message_count": 0,
    }
    assert events[1]["value"] is None


def test_compacted_context_checkpoint_is_restored_from_trace(tmp_path, monkeypatch):
    monkeypatch.setattr("extensions.trace.chat_trace._chat_log_dir", lambda: str(tmp_path))
    monkeypatch.setattr("extensions.trace.trace_reader._trace_dir", lambda: str(tmp_path))
    tracer = ChatTraceLogger("checkpoint-test")
    tracer.start()
    tracer.user_message("old question")
    tracer.context_compacted(
        summary="keep the SQLite decision", dropped_messages=2,
        reason="convergence", triggered_by=["tool_call_count"],
        tool_call_count=25, token_budget_used=96000, keep_recent_steps=3,
    )
    tracer.done(answer="old answer")
    tracer.close()

    history = reconstruct_history("checkpoint-test")
    assert history[0] == {
        "role": "summary",
        "content": "keep the SQLite decision",
    }
    assert history[-1] == {"role": "assistant", "content": "old answer"}
    events = [json.loads(line) for line in (tmp_path / "trace_checkpoint-test.jsonl").read_text(encoding="utf-8").splitlines()]
    checkpoint = next(event for event in events if event["event_type"] == "context_compacted")
    assert checkpoint["reason"] == "convergence"
    assert checkpoint["triggered_by"] == ["tool_call_count"]


def test_task_execution_summary_is_restored_from_trace(tmp_path, monkeypatch):
    monkeypatch.setattr("extensions.trace.chat_trace._chat_log_dir", lambda: str(tmp_path))
    monkeypatch.setattr("extensions.trace.trace_reader._trace_dir", lambda: str(tmp_path))
    tracer = ChatTraceLogger("task-summary-test")
    tracer.start()
    tracer.user_message("continue the task")
    tracer.task_summary(
        summary="## Task execution checkpoint\n- Status: partial\n- Pending: run pytest",
        status="partial",
        tool_call_count=3,
    )
    tracer.done(answer="部分完成")
    tracer.close()

    history = reconstruct_history("task-summary-test")

    summary_index = next(
        index for index, item in enumerate(history)
        if item.get("role") == "summary"
    )
    assert summary_index == 2
    assert "Status: partial" in history[summary_index]["content"]
    assert "Pending: run pytest" in history[summary_index]["content"]
    events = [
        json.loads(line)
        for line in (tmp_path / "trace_task-summary-test.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    event = next(item for item in events if item["event_type"] == EVT_TASK_SUMMARY)
    assert event["status"] == "partial"
    assert event["tool_call_count"] == 3


def test_task_execution_summaries_stay_with_their_task(tmp_path, monkeypatch):
    monkeypatch.setattr("extensions.trace.chat_trace._chat_log_dir", lambda: str(tmp_path))
    monkeypatch.setattr("extensions.trace.trace_reader._trace_dir", lambda: str(tmp_path))
    tracer = ChatTraceLogger("task-summary-association-test")
    tracer.start()
    tracer.user_message("first task")
    tracer.task_summary(
        summary="## Task execution checkpoint\n- Task: first task\n- Result: first result",
        status="completed",
        tool_call_count=1,
    )
    tracer.done(answer="first answer")
    tracer.user_message("second task")
    tracer.task_summary(
        summary="## Task execution checkpoint\n- Task: second task\n- Pending: second verification",
        status="partial",
        tool_call_count=2,
    )
    tracer.done(answer="second answer")
    tracer.close()

    history = reconstruct_history("task-summary-association-test")

    assert [item["role"] for item in history] == [
        "user", "assistant", "summary", "user", "assistant", "summary",
    ]
    assert "first task" in history[2]["content"]
    assert "second task" not in history[2]["content"]
    assert "second task" in history[5]["content"]
    assert "first task" not in history[5]["content"]


def test_trace_summary_aggregates_prompt_and_runtime_counters_without_content(tmp_path, monkeypatch):
    monkeypatch.setattr("extensions.trace.chat_trace._chat_log_dir", lambda: str(tmp_path))
    monkeypatch.setattr("extensions.trace.trace_reader._trace_dir", lambda: str(tmp_path))
    tracer = ChatTraceLogger("summary-test")
    tracer.start()
    tracer.user_message("run a task")
    tracer.event(
        "prompt_context",
        prompt_version="devagent-3.1",
        static_prompt={
            "chars": 100, "estimated_tokens": 25,
        },
        total_chars=60,
        estimated_tokens=15,
    )
    span_id = tracer.llm_request(
        provider="test", model="test", messages=[], temperature=0.3, max_tokens=None,
    )
    tracer.llm_response(
        span_id=span_id, content="ok",
        usage={"prompt_tokens": 25, "completion_tokens": 5, "total_tokens": 30},
    )
    tool_span = tracer.tool_call(step=1, tool_name="read_file", arguments={})
    tracer.tool_result(
        span_id=tool_span, tool_name="read_file", observation="ok", duration_ms=12.5,
    )
    tracer.done(answer="final answer")
    tracer.close()

    summary = summarize_trace("summary-test")

    assert summary["turns"] == 1
    assert summary["llm_usage"] == {
        "prompt_tokens": 25, "completion_tokens": 5, "total_tokens": 30,
    }
    assert summary["tool_calls"] == 1
    assert summary["runtime"] == {
        "llm_duration_ms": 0.0,
        "tool_duration_ms": 12.5,
        "max_tool_duration_ms": 12.5,
    }
    assert summary["prompt"] == {
        "builds": 1,
        "versions": {"devagent-3.1": 1},
        "chars": 160,
        "estimated_tokens": 40,
    }
    assert "final answer" not in summary


def test_task_dirs_are_scoped():
    task_a, worktree_a, _ = _default_dirs("session-a")
    task_b, worktree_b, _ = _default_dirs("session-b")
    assert task_a != task_b
    assert worktree_a != worktree_b


def test_agent_allowed_tools_and_budget():
    registry = ToolRegistry()
    registry.register_tool(_Echo())
    agent = ReActAgent("budget", _LoopLLM(), registry, max_steps=1,
                       max_tool_calls=0)

    async def run():
        events = []
        async for event in agent.arun_stream("test", allowed_tools=[]):
            events.append(event)
        return events

    events = asyncio.run(run())
    detail = events[0].tool_calls_detail[0]
    assert "not enabled" in detail["observation"]
