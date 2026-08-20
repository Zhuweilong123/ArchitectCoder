"""todo_write 与 spawn_subagent 单元测试。"""
import asyncio
import json

from app.agent_base.core.hooks import (
    AgentRuntime, HookContext, HookEvent, get_runtime,
    set_runtime, reset_runtime, _todo_reminder_hook,
)
from app.agent_base.tools.my_tools.todo_tools import TodoWriteTool
from app.agent_base.tools.my_tools.subagent_tool import SpawnSubagentTool


def test_todo_write_updates_runtime():
    runtime = AgentRuntime()
    token = set_runtime(runtime)
    try:
        tool = TodoWriteTool()
        result = tool.run({"todos": [{"content": "step 1", "status": "pending"}]})
        assert "Updated 1" in result
        assert runtime.todos == [{"content": "step 1", "status": "pending"}]
        assert runtime.rounds_since_todo == 0
    finally:
        reset_runtime(token)


def test_todo_write_rejects_invalid_status():
    token = set_runtime(AgentRuntime())
    try:
        tool = TodoWriteTool()
        result = tool.run({"todos": [{"content": "x", "status": "weird"}]})
        assert "invalid status" in result
    finally:
        reset_runtime(token)


def test_todo_reminder_hook_injects_after_three_rounds():
    runtime = AgentRuntime()
    runtime.todos = [{"content": "step 1", "status": "pending"}]
    token = set_runtime(runtime)
    try:
        messages = []
        for _ in range(3):
            _todo_reminder_hook(HookContext(
                event=HookEvent.LLM_BEFORE, agent_name="t", messages=messages,
            ))
        # 第 3 轮触发时注入 reminder 并清零计数
        assert any("<reminder>" in m.get("content", "") for m in messages)
        assert runtime.rounds_since_todo == 0
    finally:
        reset_runtime(token)


def test_todo_reminder_hook_skips_when_no_open_todos():
    runtime = AgentRuntime()
    runtime.todos = [{"content": "done", "status": "completed"}]
    token = set_runtime(runtime)
    try:
        messages = []
        for _ in range(5):
            _todo_reminder_hook(HookContext(
                event=HookEvent.LLM_BEFORE, agent_name="t", messages=messages,
            ))
        assert not any("<reminder>" in m.get("content", "") for m in messages)
    finally:
        reset_runtime(token)


class _MockLLM:
    """前 1 轮返回 tool_call，之后返回 summary；记录 model 覆盖。"""

    def __init__(self):
        self.count = 0
        self.last_model = None

    async def ainvoke_with_tools(self, messages, tools, tool_choice="auto", **kwargs):
        self.count += 1
        self.last_model = kwargs.get("model")
        if self.count == 1:
            return {
                "content": "",
                "tool_calls": [{
                    "id": "c1", "type": "function",
                    "function": {"name": "glob", "arguments": json.dumps({"pattern": "*.py"})},
                }],
            }
        return {"content": "summary text", "tool_calls": None}


def test_spawn_subagent_returns_summary_and_uses_sub_model(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("x", encoding="utf-8")

    llm = _MockLLM()
    tool = SpawnSubagentTool(
        llm=llm, sub_agent_model="sub-model", source_dir=str(src),
    )

    result = asyncio.run(tool._execute({"description": "find files"}))
    assert "summary text" in result
    assert llm.last_model == "sub-model"


def test_spawn_subagent_requires_description(tmp_path):
    llm = _MockLLM()
    tool = SpawnSubagentTool(llm=llm, sub_agent_model="sub-model", source_dir=str(tmp_path))

    result = asyncio.run(tool._execute({"description": ""}))
    assert "description is required" in result
