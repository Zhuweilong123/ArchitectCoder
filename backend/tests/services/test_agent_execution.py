"""Regression tests for the transport-neutral Agent execution service."""

import asyncio

import app.services.agent_execution as agent_execution
from app.agent_base.agents.react_agent import ReActProgress
from app.agent_base.core.orchestration import OrchestrationPreparation
from app.agent_base.tools.registry import ToolRegistry


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
    assert sent[-1] == {"event": "done", "result": "hello"}
