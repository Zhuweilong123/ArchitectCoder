import asyncio
import json
from types import SimpleNamespace

import pytest

from app.agent_base.agents.react_agent import ReActAgent
from app.agent_base.core.hooks import AgentRuntime, reset_runtime, set_runtime
from app.agent_base.evidence import (
    EvidenceLedger, record_runtime_verification, update_checkpoint_evidence,
)
from app.agent_base.outcome import RunOutcome
from app.agent_base.tools.my_tools.foundation_tools import (
    ApplyChangesTool, RunProgramTool, RunTaskTool,
)
from app.agent_base.tools.registry import ToolRegistry
from app.agent_base.tools.result import command_result
from app.services.agent_execution import (
    _finalize_terminal_checkpoint, _should_archive_task_memory,
)


def test_actual_mutations_drive_evidence_and_memory(tmp_path):
    registry = ToolRegistry()
    registry.register_tool(ApplyChangesTool(str(tmp_path)))
    create = {"changes": [{"op": "create", "path": "a.py", "content": "a = 1\n"}]}
    result = asyncio.run(registry.aexecute_tool_result_with_params("apply_changes", create))
    detail = {"name": "apply_changes", **result.to_dict()}
    assert result.changes[0].after_hash
    assert _should_archive_task_memory("completed", [detail])
    record = EvidenceLedger().record(call_id="1", tool_name="apply_changes", arguments=create,
                                     observation=result.text, effects=result.effects())
    assert "create file=" in record.render()
    assert record.pending_edit
    noop = {"changes": [{"op": "replace", "path": "a.py", "content": "a = 1\n"}]}
    result = asyncio.run(registry.aexecute_tool_result_with_params("apply_changes", noop))
    assert result.status == "success"
    assert not result.changes
    assert not _should_archive_task_memory("completed", [result.to_dict()])


def test_failed_batch_does_not_report_mutation(tmp_path):
    tool = ApplyChangesTool(str(tmp_path))
    result = tool.run_result({"changes": [
        {"op": "create", "path": "a.py", "content": "a = 1"},
        {"op": "replace", "path": "missing.py", "content": "no"},
    ]})
    assert result.status == "error"
    assert not result.changes
    assert not (tmp_path / "a.py").exists()


def test_process_status_does_not_depend_on_output_wording():
    result = command_result("python -m pytest", "src", 0, "Error: example output")
    assert result.status == "success"
    assert result.verification.passed
    failed = command_result("pytest", "src", 1, "All done")
    assert not failed.verification.passed
    assert failed.execution.exit_code == 1
    assert command_result("echo pytest", "src", 0, "pytest").verification is None


def test_verification_accumulates_and_retries_replace_only_same_scope():
    checkpoint = {"changed_files": ["previous.py"]}
    failed = command_result("pytest tests/a.py", "src", 1, "failed")
    other = command_result("pytest tests/b.py", "src", 0, "passed")
    update_checkpoint_evidence(checkpoint, [failed.to_dict(), other.to_dict()])
    update_checkpoint_evidence(checkpoint, [])
    assert len(checkpoint["verification_results"]) == 2
    assert not checkpoint["verification_results"][0]["passed"]
    assert checkpoint["changed_files"] == ["previous.py"]
    retry = command_result("pytest tests/a.py", "src", 0, "passed")
    update_checkpoint_evidence(checkpoint, [retry.to_dict()])
    assert len(checkpoint["verification_results"]) == 2
    assert all(item["passed"] for item in checkpoint["verification_results"])


def test_passing_full_suite_supersedes_prior_test_failures_only():
    focused = command_result("python -m pytest test/a.py", "test", 1, "failed")
    lint = command_result("ruff check", "src", 1, "failed")
    other = command_result("python -m pytest test/b.py", "test", 0, "passed")
    full = command_result("python -m pytest -q", "test", 0, "all passed")
    checks = {}
    checkpoint = {}
    for tool, args, result in (
        ("run_program", {"program": "python", "args": ["-m", "pytest", "test/a.py"]}, focused),
        ("run_program", {"program": "ruff", "args": ["check"]}, lint),
        ("run_program", {"program": "python", "args": ["-m", "pytest", "test/b.py"]}, other),
    ):
        record_runtime_verification(checks, result.verification, tool, args)
        update_checkpoint_evidence(checkpoint, [{
            "name": tool, "arguments": args, **result.to_dict(),
        }])
    assert checks[("test", focused.verification.scope)] is False
    assert any(not item["passed"] and item["kind"] == "test"
               for item in checkpoint["verification_results"])

    args = {"program": "python", "args": ["-m", "pytest", "-q"]}
    record_runtime_verification(checks, full.verification, "run_program", args)
    update_checkpoint_evidence(checkpoint, [{
        "name": "run_program", "arguments": args, **full.to_dict(),
    }])
    assert checks == {
        ("lint", lint.verification.scope): False,
        ("test", full.verification.scope): True,
    }
    assert [(item["kind"], item["passed"])
            for item in checkpoint["verification_results"]] == [
        ("lint", False), ("test", True),
    ]


def test_full_suite_success_allows_completed_outcome_after_focused_failure():
    focused = command_result("python -m pytest test/a.py", "test", 1, "failed")
    full = command_result("python -m pytest -q", "test", 0, "passed")
    checks = {}
    checkpoint = {}
    for args, result in (
        ({"program": "python", "args": ["-m", "pytest", "test/a.py"]}, focused),
        ({"program": "python", "args": ["-m", "pytest", "-q"]}, full),
    ):
        record_runtime_verification(checks, result.verification, "run_program", args)
        update_checkpoint_evidence(checkpoint, [{
            "name": "run_program", "arguments": args, **result.to_dict(),
        }])
    outcome = RunOutcome.from_stop(
        "model_answer", "done", verification_failed=any(not value for value in checks.values()),
    )
    assert outcome.status == "completed"
    assert all(item["passed"] for item in checkpoint["verification_results"])
    agent = SimpleNamespace(last_run_checkpoint=checkpoint)
    runtime_token = set_runtime(AgentRuntime(todos=[]))
    try:
        status, _ = _finalize_terminal_checkpoint(
            agent, outcome=outcome, run_id="run-1", task_id="task-1",
            request_summary="run tests", fallback_review_requested=False,
            review_manager=None,
        )
    finally:
        reset_runtime(runtime_token)
    assert status == "completed"
    assert agent.last_run_checkpoint["status"] == "completed"


def test_selected_test_run_does_not_hide_another_failure():
    failed = command_result("python -m pytest test/a.py", "test", 1, "failed")
    selected = command_result("python -m pytest -k other", "test", 0, "passed")
    checks = {}
    record_runtime_verification(
        checks, failed.verification, "run_program",
        {"program": "python", "args": ["-m", "pytest", "test/a.py"]},
    )
    record_runtime_verification(
        checks, selected.verification, "run_program",
        {"program": "python", "args": ["-m", "pytest", "-k", "other"]},
    )
    assert checks[("test", failed.verification.scope)] is False


def test_run_task_full_suite_replaces_focused_failure():
    failed = command_result("python -m pytest test/a.py", "test", 1, "failed")
    passed = command_result("python -m pytest", "test", 0, "passed")
    checks = {}
    record_runtime_verification(
        checks, failed.verification, "run_task",
        {"task": "test", "target": "test/a.py"},
    )
    record_runtime_verification(
        checks, passed.verification, "run_task", {"task": "test", "cwd": "test"},
    )
    assert checks == {("test", passed.verification.scope): True}


def test_run_task_without_target_runs_full_suite(tmp_path, monkeypatch):
    seen = {}
    test_dir = tmp_path / "test"
    test_dir.mkdir()

    async def fake_execute(self, params):
        seen.update(params)
        return command_result("python -m pytest", str(test_dir), 0, "1 passed")

    monkeypatch.setattr(RunProgramTool, "_execute_result", fake_execute)
    tool = RunTaskTool(str(tmp_path), test_dir=str(test_dir), workspace_root=str(tmp_path))
    result = asyncio.run(tool.run_result({"task": "test", "cwd": "test"}))
    assert result.status == "success"
    assert seen["args"] == ["-m", "pytest"]
    assert seen["cwd"] == str(test_dir)
    assert result.verification.passed


def test_validate_tool_returns_structured_verdict(tmp_path):
    (tmp_path / "ok.umlproj").write_text(json.dumps({"diagrams": [{"id": "d"}]}), encoding="utf-8")
    tool = RunTaskTool(str(tmp_path))
    result = asyncio.run(tool.run_result({"task": "validate", "target": "ok.umlproj"}))
    assert result.verification.kind == "validate"
    assert result.verification.passed
    result = asyncio.run(tool.run_result({"task": "validate", "target": "missing.umlproj"}))
    assert not result.verification.passed


@pytest.mark.parametrize("reason,status", [
    ("reserve_finalization", "budget_exceeded"), ("llm_timeout", "timed_out"),
    ("model_answer", "completed"),
])
def test_outcome_is_independent_of_answer_language(reason, status):
    for answer in ("完成", "All done", "解释 token 预算和时间预算"):
        assert RunOutcome.from_stop(reason, answer).status == status


def test_react_does_not_accumulate_initial_request_usage_into_next_request():
    class LLM:
        async def ainvoke_with_tools(self, **kwargs):
            return {"content": "完成", "tool_calls": None, "usage": {"total_tokens": 1}}

    async def execute():
        agent = ReActAgent("test", LLM(), ToolRegistry(), max_total_tokens=9,
                           emergency_max_total_tokens=10,
                           token_finalization_reserve_tokens=5)
        stream = agent._arun_with_fc_stream("task", initial_token_usage=10)
        try:
            progress = await anext(stream)
            assert progress.outcome.status == "completed"
            assert progress.outcome.total_tokens == 11
            assert agent.last_context_report["token_budget_used"] == 1
            assert agent.last_context_report["token_usage_total_observed"] == 11
        finally:
            await stream.aclose()
    asyncio.run(execute())
