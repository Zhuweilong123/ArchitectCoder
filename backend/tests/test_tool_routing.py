from app.agent_base.core.hooks import AgentRuntime, reset_runtime, set_runtime
from app.services.agent_chat_ws import (
    _checkpoint_answer, _latest_persisted_checkpoint,
    _should_archive_task_memory, _terminal_checkpoint_status,
    _todo_progress_state, DevPromptBuilder,
)


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


def test_checkpoint_answer_uses_structured_checkpoint_data():
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


def test_memory_archive_requires_completed_mutation_evidence():
    details = [{"name": "find_nodes"}]
    assert not _should_archive_task_memory("completed", details)
    assert not _should_archive_task_memory("budget_exceeded", [{"name": "edit_file", "status": "success"}])
    assert _should_archive_task_memory("completed", [{"name": "edit_file", "status": "success"}])


def test_prompt_builder_reports_dynamic_sections_without_content():
    builder = DevPromptBuilder()

    import asyncio
    context = asyncio.run(builder.build_context("", "src", "tests", "run pytest"))

    assert "Source directory: src" in context
    assert builder.static_prompt_report["estimated_tokens"] > 0
    assert builder.last_context_report["sections"]["workspace"]["chars"] > 0
    assert "memory" not in builder.last_context_report["sections"]


def test_prompt_builder_always_includes_project_workspace():
    import asyncio

    builder = DevPromptBuilder()
    context = asyncio.run(builder.build_context("project.umlproj", "src", "tests", "你好"))
    assert "Source directory: src" in context
    assert "Test directory: tests" in context
    assert "Current project file: project.umlproj" in context


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
