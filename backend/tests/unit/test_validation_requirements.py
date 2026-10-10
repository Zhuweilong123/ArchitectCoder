import asyncio
import json

import pytest

from app.agent_base.core.hooks import AgentRuntime, set_runtime, reset_runtime
from app.agent_base.host_api.validation import CheckResult, ValidationReport
from app.agent_base.host_api.contexts import ContractGateContext
from app.agent_base.tools.my_tools.todo_tools import TodoWriteTool
from app.agent_base.tools.my_tools.foundation_tools import RunTaskTool
from app.agent_base.tools.review import ReviewManager, SubmitUmlReviewTool
from app.services.design_validation import validate_project_diagrams
from app.validation.requirements import enforce_requirements
from extensions.design_contract.gate import DefaultContractGate
from extensions.design_contract.provider import DesignContractProvider


REQUIRED = [{"rule_id": "sequence.source", "diagram_name": "Flow"}]
DIAGRAM = {"name": "Flow", "diagram_type": "sequence"}


def test_source_requirement_blocks_missing_evidence_but_layout_remains_lightweight():
    assert not validate_project_diagrams([DIAGRAM]).has_errors
    report = validate_project_diagrams([DIAGRAM], requirements=REQUIRED)
    assert report.has_errors
    assert report.diagnostics[-1].code == "VALIDATION_REQUIREMENT_UNMET"
    assert report.checks[-2].status == "not_applicable"


@pytest.mark.parametrize("status,coverage_met,accepted", [
    ("checked", False, True), ("partial", False, False), ("partial", True, True),
    ("not_applicable", True, False), ("unavailable", True, False), ("unsupported", True, False),
])
def test_policy_compares_registered_coverage_without_diagram_specific_logic(status, coverage_met, accepted):
    report = ValidationReport((CheckResult("future.rule", "diagrams[0]", status, coverage_met=coverage_met),))
    result = enforce_requirements(report, [DIAGRAM], [{"rule_id": "future.rule", "diagram_name": "Flow"}])
    assert result.has_errors is not accepted


@pytest.mark.parametrize("requirements", [
    [{"rule_id": "unknown", "diagram_name": "Flow"}],
    [{"rule_id": "schema", "diagram_name": "missing"}],
])
def test_unknown_rule_or_target_does_not_satisfy_task(requirements):
    assert validate_project_diagrams([DIAGRAM], requirements=requirements).has_errors


def test_todo_requirements_accumulate_and_invalid_updates_are_atomic():
    runtime = AgentRuntime()
    token = set_runtime(runtime)
    try:
        tool = TodoWriteTool()
        todos = [{"content": "verify flow", "status": "in_progress"}]
        assert tool.run({"todos": todos, "validation_requirements": REQUIRED}).startswith("Updated")
        assert tool.run({"todos": todos, "validation_requirements": []}).startswith("Updated")
        assert runtime.policy_metadata["validation_requirements"] == REQUIRED
        assert tool.run({"todos": todos, "validation_requirements": [{"rule_id": "source"}]}).startswith("Error:")
        assert runtime.policy_metadata["validation_requirements"] == REQUIRED
    finally:
        reset_runtime(token)


def test_validate_and_review_reject_unsatisfied_required_check(tmp_path):
    token = set_runtime(AgentRuntime(policy_metadata={"validation_requirements": REQUIRED}))
    try:
        project = tmp_path / "design.umlproj"
        project.write_text(json.dumps({"diagrams": [DIAGRAM]}), encoding="utf-8")
        tool = RunTaskTool(workspace_root=str(tmp_path))
        result = asyncio.run(tool.run({"task": "validate", "target": str(project)}))
        assert "VALIDATION_REQUIREMENT_UNMET" in result
        manager = ReviewManager()
        review = SubmitUmlReviewTool(manager, workspace_root=str(tmp_path), project_file=str(project))
        result = asyncio.run(review._execute({}))
        assert "VALIDATION_REQUIREMENT_UNMET" in result
        assert not manager.has_pending()
    finally:
        reset_runtime(token)


@pytest.mark.parametrize("contract_enabled", [True, False])
def test_no_edits_and_disabled_contract_do_not_bypass_declared_task_checks(tmp_path, contract_enabled):
    project = tmp_path / "design.umlproj"
    project.write_text(json.dumps({"diagrams": [DIAGRAM]}), encoding="utf-8")
    async def emit(event):
        return True
    context = ContractGateContext(
        {"workspace_root": str(tmp_path), "project_file": str(project)}, (), emit,
        contract_enabled=contract_enabled, validation_requirements=tuple(REQUIRED))
    decision = asyncio.run(DefaultContractGate(collector=DesignContractProvider()).evaluate(context))
    assert not decision.allowed
    assert decision.result.status == "block"
    assert "VALIDATION_REQUIREMENT_UNMET" in {v.code for v in decision.result.violations}


def test_host_enforces_requirements_without_a_contract_plugin(tmp_path):
    from app.agent_base.host_api.execution import ExecutionRequest, ExecutionSlots
    from app.services.task_validation import ensure_task_validation
    project = tmp_path / "design.umlproj"
    project.write_text(json.dumps({"diagrams": [DIAGRAM]}), encoding="utf-8")
    request = ExecutionRequest(ExecutionSlots.CHECK, data={"workspace_manifest": {
        "workspace_root": str(tmp_path), "project_file": str(project)}})
    asyncio.run(ensure_task_validation(request, REQUIRED))
    assert not request.allowed
    assert request.stop_reason == "validation_requirements_unmet"
    assert request.checkpoint["validation_requirements"] == REQUIRED


def test_fallback_review_cannot_publish_or_commit_missing_required_coverage(tmp_path):
    from app.services.agent_execution import _request_fallback_uml_review
    from app.services.task_validation import ensure_task_validation
    from app.agent_base.host_api.execution import ExecutionRequest, ExecutionSlots
    project = tmp_path / "design.umlproj"
    project.write_text(json.dumps({"diagrams": [DIAGRAM]}), encoding="utf-8")
    manager = ReviewManager()
    manager.baseline = []
    runtime = AgentRuntime(policy_metadata={"validation_requirements": REQUIRED})
    token = set_runtime(runtime)
    try:
        async def send(event):
            pytest.fail("Invalid candidate must not be published for review")
        result = asyncio.run(_request_fallback_uml_review(
            review_manager=manager, uml_review_seen=False, project_file=str(project),
            workspace_root=str(tmp_path), trace_log=None, send=send, fallback_review_runs={}, run_id="test"))
        assert not result and not manager.has_pending()
        request = ExecutionRequest(ExecutionSlots.CHECK)
        asyncio.run(ensure_task_validation(request, REQUIRED,
            review_failure=runtime.policy_metadata["review_validation_failure"]))
        assert not request.allowed
    finally:
        reset_runtime(token)


def test_failed_read_only_validation_task_retains_resumable_requirements():
    from types import SimpleNamespace
    from app.services.chat_session import _latest_resumable_run, _resume_prompt
    checkpoint = {"validation_requirements": REQUIRED, "stop_reason": "validation_requirements_unmet"}
    record = SimpleNamespace(status="partial", metadata={"checkpoint": checkpoint})
    store = SimpleNamespace(list=lambda **kwargs: [record])
    assert _latest_resumable_run("session", store_factory=lambda: store) == (record, checkpoint)
    prompt = _resume_prompt(checkpoint, "continue")
    assert "sequence.source" in prompt and "Flow" in prompt
