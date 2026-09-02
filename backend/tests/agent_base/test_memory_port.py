"""Tests for the optional, dynamically loaded memory port."""

import asyncio

import app.agent_base.core.memory as core_memory
from app.agent_base.core.memory import (
    MemoryArchiveRequest,
    MemoryArchiveResult,
    MemoryRecallRequest,
    MemoryRecallResult,
    NoOpMemory,
    load_memory,
)


class _Settings:
    agent_memory_enabled = True
    agent_memory_provider = "missing.module:create"


def test_loader_returns_noop_when_disabled_or_missing():
    class DisabledSettings:
        agent_memory_enabled = False

    assert isinstance(load_memory(llm=object(), settings=DisabledSettings()), NoOpMemory)
    assert isinstance(load_memory(llm=object(), settings=_Settings()), NoOpMemory)


def test_loader_contains_provider_failures(monkeypatch):
    class BrokenProvider:
        async def recall(self, request):
            raise RuntimeError("backend unavailable")

        async def archive(self, request):
            raise RuntimeError("backend unavailable")

        async def reinforce(self, memory_ids, project_id=""):
            raise RuntimeError("backend unavailable")

    monkeypatch.setattr(
        core_memory,
        "_load_factory",
        lambda _: (lambda **kwargs: BrokenProvider()),
    )
    provider = load_memory(llm=object(), settings=_Settings())

    recalled = asyncio.run(provider.recall(MemoryRecallRequest("p", "query")))
    archived = asyncio.run(provider.archive(MemoryArchiveRequest("p", "u", "a")))
    asyncio.run(provider.reinforce(("m-1",), project_id="p"))

    assert recalled == MemoryRecallResult(metadata={"degraded": True})
    assert archived == MemoryArchiveResult(metadata={"degraded": True})


def test_loader_keeps_provider_contract_and_result_types(monkeypatch):
    class FakeProvider:
        async def recall(self, request):
            return MemoryRecallResult(context_block="evidence", memory_ids=("m-1",))

        async def archive(self, request):
            return MemoryArchiveResult(stored_count=2)

        async def reinforce(self, memory_ids, project_id=""):
            return None

    monkeypatch.setattr(
        core_memory,
        "_load_factory",
        lambda _: (lambda **kwargs: FakeProvider()),
    )
    provider = load_memory(llm=object(), settings=_Settings())

    assert asyncio.run(provider.recall(MemoryRecallRequest("p", "query"))).context_block == "evidence"
    assert asyncio.run(provider.archive(MemoryArchiveRequest("p", "u", "a"))).stored_count == 2
