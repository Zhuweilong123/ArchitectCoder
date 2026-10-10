"""Regression tests for the transport-neutral Agent execution service."""

import asyncio
import pytest
from types import SimpleNamespace

import app.services.agent_execution as agent_execution
from app.services.agent_execution import _sync_checkpoint_outcome
from app.agent_base.agents.react_agent import ReActProgress
from app.agent_base.adapters.analysis import ReadOnlyAnalysisAdapter
from app.agent_base.host_api.contract_checks import ContractCheckResult, ContractViolation
from app.agent_base.host_api.contexts import ContractFailureAnalysisContext
from extensions.design_contract.analysis import (ModelContractFailureAnalyzer, build_contract_failure_report)
from app.agent_base.host_api.orchestration import OrchestrationPreparation
from app.agent_base.tools.registry import ToolRegistry


def test_checkpoint_outcome_is_normalized_after_post_model_failure():
    checkpoint = {
        "outcome": {
            "status": "completed",
            "stop_reason": "model_answer",
            "final_answer": "源码修改完成",
            "total_tokens": 17,
        }
    }
    _sync_checkpoint_outcome(
        checkpoint,
        status="partial",
        stop_reason="contract_check_failed",
        final_answer="设计契约校验阻止提交",
        preserve_as="pre_gate_outcome",
    )
    assert checkpoint["outcome"] == {
        "status": "partial",
        "stop_reason": "contract_check_failed",
        "final_answer": "设计契约校验阻止提交",
        "total_tokens": 17,
    }
    assert checkpoint["pre_gate_outcome"]["stop_reason"] == "model_answer"


class _FakeAgent:
    def __init__(self):
        self.llm = object()
        self.tool_registry = ToolRegistry()
        self.last_run_checkpoint = {}
        self.last_context_report = {}
        self.change_set = None
        self.task_summaries = []

    async def arun_stream(self, user_message, *, context, **kwargs):
        self.received_message = user_message
        self.received_context = context
        self.received_kwargs = kwargs
        yield ReActProgress(step=1, is_final=True, final_answer="hello")

    def append_task_summary(self, summary):
        self.task_summaries.append(summary)


class _FakeOrchestrator:
    async def prepare(self, request):
        return OrchestrationPreparation()


class _AnalysisAgent:
    def __init__(self):
        self._history = []
        self.history = self._history
        self.calls = []

    def add_message(self, message):
        self.history.append(message)

    async def arun(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        return "失败分析：设计与源码不一致，变更已回滚。"


class _SchemaAnalysisAgent(_AnalysisAgent):
    def __init__(self):
        super().__init__()
        self._history_summary = ""
        self.llm = self
        self.tool_registry = self
        self.context_budget = self
        self.llm_timeout_seconds = 10
        self.requests = []

    def get_openai_specs(self):
        return [{"type": "function", "function": {"name": "apply_changes"}}]

    def get_openai_specs_for(self, allowed_tools):
        assert tuple(allowed_tools) == ("apply_changes",)
        return self.get_openai_specs()

    def _build_fc_system_prompt(self):
        return "stable system"

    def build_messages(self, system, history, prompt, *, history_summary, tools):
        return SimpleNamespace(messages=[
            {"role": "system", "content": system},
            *[{"role": "summary", "content": item.content} for item in history],
            {"role": "user", "content": prompt},
        ])

    async def ainvoke_with_tools(self, *, messages, tools, tool_choice, trace_context):
        self.requests.append({
            "messages": messages,
            "tools": tools,
            "tool_choice": tool_choice,
            "trace_context": trace_context,
        })
        return {"content": "失败分析：已使用稳定工具前缀。", "tool_calls": []}


def test_contract_failure_analysis_injects_report_for_fallback_adapter():
    agent = _AnalysisAgent()
    result = ContractCheckResult(
        check_id="check-1",
        status="block",
        project_id="demo",
        changed_paths=("src/example.py",),
        violations=(ContractViolation(
            code="missing_implementation",
            severity="error",
            message="设计类没有对应的源代码实现",
            path="design/demo.umlproj",
            design_entity_id="design_class:Example",
        ),),
    )

    analysis = asyncio.run(
        ModelContractFailureAnalyzer().analyze(
            ContractFailureAnalysisContext(
                invoke=ReadOnlyAnalysisAdapter(agent).invoke,
                result=result,
                run_id="run-1",
            )
        )
    )

    assert analysis.startswith("失败分析")
    assert len(agent.calls) == 1
    assert agent.calls[0][1]["allowed_tools"] == []
    assert agent.history[-1].role == "summary"
    assert "status: block" in agent.history[-1].content
    assert "missing_implementation" in agent.history[-1].content


def test_failure_report_preserves_errors_after_legacy_warnings():
    warnings = tuple(ContractViolation(code="SEQ_LEGACY_OPERANDS", severity="warning", message="legacy")
                     for _ in range(21))
    errors = tuple(ContractViolation(code="VALIDATION_REQUIREMENT_UNMET", severity="error", message=f"target-{i}")
                   for i in range(5))
    result = ContractCheckResult(check_id="mixed", status="block", project_id="demo",
                                 violations=warnings + errors)
    report = build_contract_failure_report(result, rollback_completed=None)
    assert "rollback: not_required" in report
    assert "violation_counts: errors=5; other=21" in report
    for i in range(5):
        assert f"target-{i}" in report
    assert report.index("severity=error") < report.index("severity=warning")
    assert "omitted severity=warning; code=SEQ_LEGACY_OPERANDS; count=6" in report


def test_failure_report_counts_omitted_errors():
    result = ContractCheckResult(check_id="many", status="block", project_id="demo",
        violations=tuple(ContractViolation(code="MISSING", severity="error", message=str(i)) for i in range(25)))
    report = build_contract_failure_report(result, rollback_completed=False)
    assert "rollback: not_completed" in report
    assert "omitted severity=error; code=MISSING; count=5" in report


def test_failure_analysis_does_not_assume_rollback_without_changes():
    requests = []
    async def invoke(request):
        requests.append(request)
        return "未执行修改，验收要求未完成。"
    result = ContractCheckResult(check_id="unchanged", status="block", project_id="demo")
    asyncio.run(ModelContractFailureAnalyzer().analyze(ContractFailureAnalysisContext(
        result=result, invoke=invoke, rollback_completed=None)))
    assert "rollback: not_required" in requests[0].evidence
    assert "has already been rolled back" not in requests[0].prompt


def test_contract_failure_analysis_preserves_tool_schema_prefix_and_uses_normal_profile():
    agent = _SchemaAnalysisAgent()
    result = ContractCheckResult(check_id="check-2", status="block", project_id="demo")

    analysis = asyncio.run(
        ModelContractFailureAnalyzer().analyze(
            ContractFailureAnalysisContext(
                invoke=ReadOnlyAnalysisAdapter(agent).invoke,
                result=result,
                run_id="run-2",
                allowed_tools=("apply_changes",),
            )
        )
    )

    assert analysis.startswith("失败分析")
    assert agent.requests[0]["tools"] == agent.get_openai_specs()
    assert agent.requests[0]["tool_choice"] == "auto"
    assert agent.requests[0]["trace_context"]["kind"] == "contract_failure_analysis"


def test_agent_execution_injects_enabled_tools_context(monkeypatch):
    """The normal execution path must reach the Agent stream without NameError."""
    agent = _FakeAgent()
    sent = []

    async def send(message):
        sent.append(message)
        return True

    monkeypatch.setattr(agent_execution, "get_settings", lambda: object())

    asyncio.run(agent_execution.handle_agent_execution(
        agent=agent,
        review_mgr=None,
        user_message="hello",
        send=send,
        stop_check=lambda: False,
    ))

    assert "## Tool policy" in agent.received_context
    assert sent[-1]["event"] == "done"
    assert sent[-1]["result"] == "hello"
    assert sent[-1]["checkpoint"]["status"] == "completed"


def test_contract_check_is_persisted_in_trace_with_authoritative_decision():
    class _Trace:
        def __init__(self):
            self.events = []

        def event(self, event_type, **payload):
            self.events.append((event_type, payload))

    trace = _Trace()
    result = ContractCheckResult(
        check_id="check-trace",
        status="pass",
        project_id="demo",
        changed_paths=("src/demo.py",),
        message="contract passed",
    )

    from app.agent_base.core.extension_context import ExtensionContext, extension_scope
    from app.agent_base.host_api.execution import ExecutionRequest
    from extensions.design_contract.execution import check
    from app.agent_base.host_api.contexts import ContractGateDecision

    async def evaluate(context):
        return ContractGateDecision(allowed=True, result=result)

    request = ExecutionRequest("execution.check", data={
        "workspace_manifest": {}, "changed_paths": (), "record_event": trace.event,
    }, capabilities={"emit": lambda event: None})
    session = ExtensionContext(providers={"design_contract": SimpleNamespace(
        gate=SimpleNamespace(evaluate=evaluate))}, metadata={"execution_settings": object()})
    with extension_scope(session):
        asyncio.run(check(SimpleNamespace(invocation=request)))

    assert trace.events == [("contract_check", {
        **result.to_dict(),
        "phase": "pre_commit",
        "allowed": True,
        "decision_message": "",
    })]


@pytest.mark.parametrize("allowed", [True, False])
@pytest.mark.parametrize("policy", ["custom", "contract"])
def test_generic_execution_policy_controls_real_commit_and_rollback(tmp_path, monkeypatch, allowed, policy):
    from app.agent_base.core import hooks, plugin_runtime
    from app.agent_base.core.hooks import HookRegistry
    from app.agent_base.host_api.lifecycle import HookEvent
    from app.agent_base.host_api.execution import ExecutionSlots
    from app.services.change_set import ChangeSet

    registry = HookRegistry()
    monkeypatch.setattr(hooks, "_registry", registry)
    monkeypatch.setattr(plugin_runtime, "_active", None)
    events = []
    path = tmp_path / "candidate.py"
    path.write_text("before", encoding="utf-8")

    class EditingAgent(_FakeAgent):
        async def arun_stream(self, *args, **kwargs):
            self.change_set.record(str(path), True, "before", "after")
            path.write_text("after", encoding="utf-8")
            yield ReActProgress(step=1, is_final=True, final_answer="edited")

    def check(ctx):
        ctx.invocation.allowed = allowed
        ctx.invocation.message = "policy rejected"
        ctx.invocation.stop_reason = "custom_check_failed"

    def complete(ctx):
        events.append((ctx.invocation.interface_id, path.read_text(encoding="utf-8")))

    registry.register(HookEvent.FINALIZE, check, mode="service", interface_id=ExecutionSlots.CHECK)
    def rejected(ctx):
        complete(ctx)

    registry.register(HookEvent.FINALIZE, complete, mode="service", interface_id=ExecutionSlots.COMMITTED)
    registry.register(HookEvent.FINALIZE, rejected, mode="service", interface_id=ExecutionSlots.REJECTED)
    agent = EditingAgent()
    agent.change_set = ChangeSet()
    if policy == "contract":
        from app.agent_base.core.extension_context import ExtensionContext
        from app.agent_base.host_api.contexts import ContractGateDecision

        async def evaluate(context):
            assert path.read_text(encoding="utf-8") == "after"
            assert context.changed_paths[0]["path"] == str(path.resolve())
            return ContractGateDecision(allowed=allowed, message="policy rejected", result=ContractCheckResult(
                check_id="slot-check", status="pass" if allowed else "block", project_id="test"))

        async def finalize(context, result):
            assert agent.change_set.status == "committed"
            events.append((ExecutionSlots.COMMITTED, path.read_text(encoding="utf-8")))

        async def analyze(context):
            assert context.rollback_completed
            events.append((ExecutionSlots.REJECTED, path.read_text(encoding="utf-8")))
            return "policy rejected"

        registry.clear()
        settings = SimpleNamespace(agent_design_contract_enabled=True)
        session = ExtensionContext(metadata={"execution_settings": settings})
        session.bind("design_contract", SimpleNamespace(
            gate=SimpleNamespace(evaluate=evaluate, finalize=finalize),
            analyzer=SimpleNamespace(analyze=analyze)), settings=settings)
        agent.extension_context = session
    monkeypatch.setattr(agent_execution, "CandidateArtifactStore", lambda settings: SimpleNamespace(capture=lambda *args: None))
    sent = []

    async def send(event):
        sent.append(event)
        return True

    asyncio.run(agent_execution.handle_agent_execution(agent, None, "edit", send, lambda: False))
    expected = "after" if allowed else "before"
    assert path.read_text(encoding="utf-8") == expected
    assert events == [(ExecutionSlots.COMMITTED if allowed else ExecutionSlots.REJECTED, expected)]
    assert sent[-1]["event"] == "done"
    if not allowed:
        assert sent[-1]["checkpoint"]["stop_reason"] == (
            "contract_check_failed" if policy == "contract" else "custom_check_failed")
