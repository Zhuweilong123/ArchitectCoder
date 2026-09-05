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


def test_acceptance_todo_requires_criteria_and_verification():
    runtime = AgentRuntime(requires_acceptance_todos=True)
    token = set_runtime(runtime)
    try:
        tool = TodoWriteTool()
        assert "3 to 5" in tool.run({"todos": [{"content": "only", "status": "pending"}]})
        missing_verification = [
            {"content": "inspect", "status": "completed", "kind": "analysis", "acceptance": "scope known"},
            {"content": "edit", "status": "pending", "kind": "execution", "acceptance": "change applied"},
            {"content": "review", "status": "pending", "kind": "analysis", "acceptance": "risks listed"},
        ]
        assert "verification" in tool.run({"todos": missing_verification})
        valid = [
            {"content": "inspect", "status": "completed", "kind": "analysis", "acceptance": "scope known"},
            {"content": "edit", "status": "in_progress", "kind": "execution", "acceptance": "change applied"},
            {"content": "test", "status": "pending", "kind": "verification", "acceptance": "focused test passes"},
        ]
        assert "Updated 3" in tool.run({"todos": valid})
        assert runtime.todos == valid
    finally:
        reset_runtime(token)


def test_lightweight_task_plan_requires_three_to_five_items():
    runtime = AgentRuntime(requires_todo_plan=True)
    token = set_runtime(runtime)
    try:
        tool = TodoWriteTool()
        assert "3 to 5" in tool.run({"todos": []})
        valid = [
            {"content": f"item {index}", "status": "pending"}
            for index in range(3)
        ]
        assert "Updated 3" in tool.run({"todos": valid})
        too_many = [
            {"content": f"item {index}", "status": "pending"}
            for index in range(6)
        ]
        assert "3 to 5" in tool.run({"todos": too_many})
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


def test_spawn_subagent_returns_summary_without_overriding_parent_model(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("x", encoding="utf-8")

    llm = _MockLLM()
    tool = SpawnSubagentTool(llm=llm, source_dir=str(src))

    result = asyncio.run(tool._execute({"description": "find files"}))
    assert "summary text" in result
    assert llm.last_model is None


def test_spawn_subagent_requires_description(tmp_path):
    llm = _MockLLM()
    tool = SpawnSubagentTool(llm=llm, source_dir=str(tmp_path))

    result = asyncio.run(tool._execute({"description": ""}))
    assert "description is required" in result


# ── toolkit 动态工具包 ──────────────────────────────────────

def _build_spawn(tmp_path):
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    return SpawnSubagentTool(llm=_MockLLM(), source_dir=str(src))


def test_spawn_subagent_builds_all_supported_toolkits(tmp_path):
    tool = _build_spawn(tmp_path)
    expected = {"standard", "read_only", "kg_analysis", "strategy"}
    assert set(tool.sub_registries.keys()) == expected
    assert set(tool.system_prompts.keys()) == expected


def test_standard_toolkit_full_editing(tmp_path):
    tool = _build_spawn(tmp_path)
    names = tool.sub_registries["standard"].list_tools()
    assert {"read_file", "write_file", "edit_file", "glob", "bash", "skill"} <= set(names)
    # 安全不变量：子代理永不递归、永不经由委派绕过审核
    assert "spawn_subagent" not in names
    assert "submit_uml_review" not in names


def test_read_only_toolkit_no_writes(tmp_path):
    tool = _build_spawn(tmp_path)
    names = tool.sub_registries["read_only"].list_tools()
    assert names == ["read_file"]
    assert not ({"get_project_map", "find_nodes", "expand_neighbors"} & set(names))
    assert not ({"write_file", "edit_file", "bash"} & set(names))


def test_kg_analysis_toolkit_no_writes(tmp_path):
    tool = _build_spawn(tmp_path)
    names = tool.sub_registries["kg_analysis"].list_tools()
    assert set(names) == {"read_file", "skill"}
    assert not ({"get_project_map", "find_nodes", "expand_neighbors"} & set(names))
    assert not ({"write_file", "edit_file", "bash"} & set(names))


def test_strategy_toolkit_is_read_only_and_can_be_single_use(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    tool = SpawnSubagentTool(
        llm=_MockLLM(), source_dir=str(src),
        toolkits=("strategy",), max_steps=6, single_use=True,
    )
    names = tool.sub_registries["strategy"].list_tools()
    assert set(names) == {"read_file", "skill"}
    assert not ({"get_project_map", "find_nodes", "expand_neighbors"} & set(names))
    assert not ({"write_file", "edit_file", "bash", "glob"} & set(names))
    schema = tool.to_openai_schema()
    assert schema["function"]["parameters"]["properties"]["toolkit"]["enum"] == ["strategy"]

    runtime_token = set_runtime(AgentRuntime())
    try:
        assert "summary text" in asyncio.run(tool._execute({"description": "plan"}))
        assert "only once" in asyncio.run(tool._execute({"description": "plan again"}))
    finally:
        reset_runtime(runtime_token)


def test_spawn_subagent_defaults_to_standard_and_forwards_toolkit(tmp_path):
    """_execute 默认 standard；未知 toolkit 回退 standard；合法 toolkit 走对应 registry。"""
    src = tmp_path / "src"
    src.mkdir()
    llm = _MockLLM()
    tool = SpawnSubagentTool(llm=llm, source_dir=str(src))
    # _MockLLM 第一轮调 glob → 只有 standard 有 glob；kg_analysis 没有，会直接收尾
    result = asyncio.run(tool._execute({"description": "summarize files", "toolkit": "standard"}))
    assert "summary text" in result
    assert llm.last_model is None


def test_spawn_subagent_stops_at_independent_token_budget(tmp_path):
    class _BudgetLLM(_MockLLM):
        async def ainvoke_with_tools(self, messages, tools, tool_choice="auto", **kwargs):
            self.count += 1
            return {
                "content": "",
                "tool_calls": [{
                    "id": f"c{self.count}",
                    "type": "function",
                    "function": {"name": "glob", "arguments": json.dumps({"pattern": "*.py"})},
                }],
                "usage": {"total_tokens": 60},
            }

    llm = _BudgetLLM()
    tool = SpawnSubagentTool(
        llm=llm, source_dir=str(tmp_path), max_steps=10, max_total_tokens=100,
    )

    result = asyncio.run(tool._execute({"description": "find files"}))

    assert "budget exceeded" in result
    assert tool.last_token_usage == 120
    assert llm.count == 2
