"""Regression tests for the transport-neutral Agent execution service."""

import asyncio
from types import SimpleNamespace

import app.services.agent_execution as agent_execution
from app.services.agent_execution import _sync_checkpoint_outcome
from app.agent_base.agents.react_agent import ReActProgress
from app.agent_base.core.contract_harness import ContractCheckResult, ContractViolation
from app.agent_base.core.contract_analysis import (
    ContractFailureAnalysisContext,
    ModelContractFailureAnalyzer,
)
from app.agent_base.core.orchestration import OrchestrationPreparation
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
                agent=agent,
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


def test_contract_failure_analysis_preserves_tool_schema_prefix_and_uses_normal_profile():
    agent = _SchemaAnalysisAgent()
    result = ContractCheckResult(check_id="check-2", status="block", project_id="demo")

    analysis = asyncio.run(
        ModelContractFailureAnalyzer().analyze(
            ContractFailureAnalysisContext(
                agent=agent,
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
    monkeypatch.setattr(
        agent_execution,
        "load_orchestrator",
        lambda **kwargs: _FakeOrchestrator(),
    )

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
