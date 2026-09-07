import asyncio
import json

import pytest

from app.agent_base.agents.react_agent import ReActAgent
from app.agent_base.evidence import EvidenceLedger, update_checkpoint_evidence
from app.agent_base.outcome import RunOutcome
from app.agent_base.tools.my_tools.foundation_tools import ApplyChangesTool, RunTaskTool
from app.agent_base.tools.registry import ToolRegistry
from app.agent_base.tools.result import command_result
from app.services.agent_execution import _should_archive_task_memory


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
    ("productive_step_limit", "partial"), ("model_answer", "completed"),
])
def test_outcome_is_independent_of_answer_language(reason, status):
    for answer in ("完成", "All done", "解释 token 预算和时间预算"):
        assert RunOutcome.from_stop(reason, answer).status == status


def test_react_publishes_stop_cause_before_final_yield():
    class LLM:
        async def ainvoke_with_tools(self, **kwargs):
            return {"content": "完成", "tool_calls": None, "usage": {"total_tokens": 1}}

    async def execute():
        agent = ReActAgent("test", LLM(), ToolRegistry(), max_total_tokens=10,
                           token_finalization_reserve_tokens=5)
        stream = agent._arun_with_fc_stream("task", initial_token_usage=6)
        try:
            progress = await anext(stream)
            assert progress.outcome.status == "budget_exceeded"
            assert progress.outcome.total_tokens == 7
            assert agent.last_context_report["token_budget_used"] == 7
        finally:
            await stream.aclose()
    asyncio.run(execute())
