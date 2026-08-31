"""P0/P1 基础设施回归：trace 生命周期、任务隔离与 agent 预算。"""

import asyncio
import json

from app.agent_base.agents.react_agent import ReActAgent
from app.agent_base.tools.base import Tool, ToolParameter
from app.agent_base.tools.registry import ToolRegistry
from app.agent_base.tools.task_system import _default_dirs
from app.services.chat_trace import ChatTraceLogger, EVT_SESSION_END


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
