"""Tests for the optional orchestration plugin boundary."""

import asyncio

from app.agent_base.adapters.orchestration import (NoOpOrchestrator, load_orchestrator)
from app.agent_base.ports.orchestration import (OrchestrationRequest)
import app.agent_base.adapters.orchestration as core_orchestration


class _Settings:
    agent_orchestration_enabled = True
    agent_orchestrator_provider = "missing.module:create"


def test_loader_returns_noop_when_provider_is_missing():
    provider = load_orchestrator(llm=object(), settings=_Settings())
    assert isinstance(provider, NoOpOrchestrator)
    result = asyncio.run(provider.prepare(OrchestrationRequest("hello")))
    assert result.context == ""


def test_loader_returns_noop_when_disabled():
    class DisabledSettings:
        agent_orchestration_enabled = False

    provider = load_orchestrator(llm=object(), settings=DisabledSettings())
    assert isinstance(provider, NoOpOrchestrator)


def test_loader_contains_provider_runtime_failure(monkeypatch):
    class BrokenProvider:
        async def prepare(self, request):
            raise RuntimeError("provider unavailable")

    monkeypatch.setattr(
        core_orchestration,
        "_load_factory",
        lambda _: (lambda **kwargs: BrokenProvider()),
    )
    provider = load_orchestrator(llm=object(), settings=_Settings())
    result = asyncio.run(provider.prepare(OrchestrationRequest("hello")))
    assert result.phase == "unavailable"
    assert result.excluded_tools == ("route_architecture", "explore_architecture")
    assert "provider unavailable" in result.metadata["architecture_scheduling_reason"]
