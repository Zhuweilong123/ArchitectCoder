"""P0/P1 基础设施回归：trace 生命周期、任务隔离与 agent 预算。"""

import asyncio
import json

from app.agent_base.agents.react_agent import ReActAgent
from app.agent_base.tools.base import Tool, ToolParameter
from app.agent_base.tools.registry import ToolRegistry
from app.agent_base.tools.task_system import _default_dirs
from app.services.chat_trace import ChatTraceLogger, EVT_SESSION_END
from app.services.trace_reader import reconstruct_history, summarize_trace


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
    monkeypatch.setattr("app.services.chat_trace._chat_log_dir", lambda: str(tmp_path))
    tracer = ChatTraceLogger("trace-test")
    tracer.start()
    tracer.close()
    events = [json.loads(line) for line in (tmp_path / "trace_trace-test.jsonl").read_text(encoding="utf-8").splitlines()]
    assert events[-1]["event_type"] == EVT_SESSION_END


def test_compacted_context_checkpoint_is_restored_from_trace(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.chat_trace._chat_log_dir", lambda: str(tmp_path))
    monkeypatch.setattr("app.services.trace_reader._trace_dir", lambda: str(tmp_path))
    tracer = ChatTraceLogger("checkpoint-test")
    tracer.start()
    tracer.user_message("old question")
    tracer.context_compacted(summary="keep the SQLite decision", dropped_messages=2)
    tracer.done(answer="old answer")
    tracer.close()

    history = reconstruct_history("checkpoint-test")
    assert history[0] == {
        "role": "summary",
        "content": "keep the SQLite decision",
    }
    assert history[-1] == {"role": "assistant", "content": "old answer"}


def test_trace_summary_aggregates_prompt_and_runtime_counters_without_content(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.chat_trace._chat_log_dir", lambda: str(tmp_path))
    monkeypatch.setattr("app.services.trace_reader._trace_dir", lambda: str(tmp_path))
    tracer = ChatTraceLogger("summary-test")
    tracer.start()
    tracer.user_message("run a task")
    tracer.event(
        "prompt_context",
        prompt_version="devagent-3.1",
        static_prompt={
            "chars": 100, "estimated_tokens": 25,
            "candidate_savings_chars": 40, "candidate_savings_tokens": 10,
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
    tracer.tool_result(span_id=tool_span, tool_name="read_file", observation="ok")
    tracer.done(answer="final answer")
    tracer.close()

    summary = summarize_trace("summary-test")

    assert summary["turns"] == 1
    assert summary["llm_usage"] == {
        "prompt_tokens": 25, "completion_tokens": 5, "total_tokens": 30,
    }
    assert summary["tool_calls"] == 1
    assert summary["prompt"] == {
        "builds": 1,
        "versions": {"devagent-3.1": 1},
        "chars": 160,
        "estimated_tokens": 40,
        "candidate_savings_chars": 40,
        "candidate_savings_tokens": 10,
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
