from app.agent_base.core.hooks import AgentRuntime, reset_runtime, set_runtime
from app.services.agent_chat_ws import (
    _checkpoint_answer, _is_status_query, _requires_todo_plan,
    _latest_persisted_checkpoint, _select_tools_for_message,
    _should_archive_task_memory, _terminal_checkpoint_status,
    _todo_progress_state, DevPromptBuilder,
)


class _Registry:
    def __init__(self, names):
        self._names = names

    def list_tools(self):
        return list(self._names)


TOOLS = [
    "read_file", "glob", "search_text", "write_file", "edit_file", "bash", "delete_path", "todo_write",
    "skill", "submit_uml_review", "get_project_map", "find_nodes",
    "compare_design_code", "spawn_subagent",
]


def _route(message):
    return _select_tools_for_message(_Registry(TOOLS), message)


def test_clear_greeting_is_tool_free_but_ambiguous_request_fails_open():
    assert _route("hello") == []
    assert _route("please look at this") is None


def test_code_route_keeps_every_core_execution_tool():
    route = _route("fix the failing pytest case in the source code")
    assert {"read_file", "glob", "search_text", "write_file", "edit_file", "bash", "delete_path"} <= set(route)
    assert "skill" not in route


def test_design_route_adds_helpers_without_hiding_write_capability():
    route = _route("update the UML class diagram")
    assert {"read_file", "glob", "search_text", "write_file", "edit_file", "bash", "delete_path"} <= set(route)
    assert {"skill", "submit_uml_review", "get_project_map", "find_nodes"} <= set(route)


def test_uml_query_uses_graph_tools_only():
    route = _route("我们的项目中有几个组件，列出他们的名字")
    assert route == ["get_project_map", "find_nodes"]


def test_bounded_uml_rename_does_not_expose_shell_or_full_skill():
    route = _route("将uml组件图中的组件名都加上Element后缀")
    assert set(route) == {
        "read_file", "edit_file", "search_text", "submit_uml_review",
        "get_project_map", "find_nodes",
    }


def test_cross_domain_request_keeps_core_and_design_helpers():
    route = _route("sync the UML design with the source code")
    assert {"read_file", "glob", "search_text", "write_file", "edit_file", "bash", "delete_path"} <= set(route)
    assert {"skill", "submit_uml_review", "get_project_map", "find_nodes"} <= set(route)
    assert {"todo_write", "spawn_subagent"} <= set(route)


def test_planning_route_requires_design_code_and_coordination_signals():
    assert "spawn_subagent" not in _route("fix the source code test")
    route = _route("migrate the UML design and source code, then verify the tests")
    assert {"todo_write", "spawn_subagent"} <= set(route)


def test_todo_progress_state_uses_runtime_as_the_authoritative_snapshot():
    runtime_token = set_runtime(AgentRuntime(
        todos=[{
            "content": "run focused test",
            "status": "in_progress",
            "kind": "verification",
            "acceptance": "test passes",
            "untrusted": "not exposed",
        }],
        requires_acceptance_todos=True,
        strategy_subagent_used=True,
    ))
    try:
        state = _todo_progress_state()
    finally:
        reset_runtime(runtime_token)

    assert state == {
        "todos": [{
            "content": "run focused test",
            "status": "in_progress",
            "kind": "verification",
            "acceptance": "test passes",
        }],
        "planning_mode": True,
        "strategy_advised": True,
    }


def test_explicit_read_only_intent_is_the_hard_safety_boundary():
    route = _route("read-only UML design review")
    assert {"read_file", "glob", "skill", "submit_uml_review"} <= set(route)
    assert "todo_write" in route
    assert not {"write_file", "edit_file", "bash"} & set(route)
    assert "delete_path" not in route


def test_todo_plan_is_default_for_non_chat_tasks_only():
    assert _requires_todo_plan("hello") is False
    assert _requires_todo_plan("rename every component") is True


def test_status_followup_uses_checkpoint_language():
    assert _is_status_query("上面的任务执行完了吗") is True
    answer = _checkpoint_answer({
        "status": "stopped",
        "completed_items": ["读取设计"],
        "pending_items": ["运行测试"],
        "changed_files": ["src/a.py"],
        "verification": [],
        "stop_reason": "user requested stop",
    })
    assert "任务状态：stopped" in answer
    assert "运行测试" in answer
    assert "src/a.py" in answer


def test_terminal_checkpoint_status_never_calls_budget_stop_completed():
    assert _terminal_checkpoint_status("已达到 token 预算（100000），已停止继续调用工具。", []) == (
        "budget_exceeded", "token budget exceeded",
    )
    assert _terminal_checkpoint_status("完成", [{"status": "pending"}]) == (
        "partial", "task checklist has pending items",
    )
    assert _terminal_checkpoint_status("完成", []) == ("completed", None)


def test_memory_archive_skips_queries_and_partial_tasks():
    details = [{"name": "find_nodes"}]
    assert not _should_archive_task_memory("列出 UML 组件", "completed", details)
    assert not _should_archive_task_memory("同步 UML 设计和源码", "budget_exceeded", details)
    assert _should_archive_task_memory("同步 UML 设计和源码", "completed", details)


def test_prompt_builder_reports_dynamic_sections_without_content():
    builder = DevPromptBuilder()

    import asyncio
    context = asyncio.run(builder.build_context("", "src", "tests", "run pytest"))

    assert "Source directory: src" in context
    assert builder.static_prompt_report["estimated_tokens"] > 0
    assert builder.last_context_report["sections"]["workspace"]["chars"] > 0
    assert "memory" not in builder.last_context_report["sections"]


def test_prompt_builder_omits_workspace_for_greeting_and_uml_query():
    import asyncio

    builder = DevPromptBuilder()
    assert asyncio.run(builder.build_context("project.umlproj", "src", "tests", "你好")) == ""
    assert asyncio.run(builder.build_context("project.umlproj", "src", "tests", "列出 UML 组件")) == ""


def test_static_prompt_is_fixed_31_and_retains_verification_and_uml_rules():
    builder = DevPromptBuilder()

    assert builder.prompt_version == "3.1"
    assert "instead of redesigning" in builder.system_prompt
    assert "focused existing test early" in builder.system_prompt
    assert "Do not modify UML unless requested" in builder.system_prompt
    assert "known canonical .umlproj" in builder.system_prompt
    assert set(builder.static_prompt_report) == {"chars", "estimated_tokens"}


def test_latest_persisted_checkpoint_reads_run_metadata(monkeypatch):
    class _Record:
        def __init__(self, metadata):
            self.metadata = metadata

    class _Store:
        def list(self, *, limit, session_id):
            assert limit == 20
            assert session_id == "session-1"
            return [_Record({"checkpoint": {"status": "succeeded"}})]

    monkeypatch.setattr("app.services.agent_chat_ws.get_run_store", lambda: _Store())
    assert _latest_persisted_checkpoint("session-1") == {"status": "succeeded"}
