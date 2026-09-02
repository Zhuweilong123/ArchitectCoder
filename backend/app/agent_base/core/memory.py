"""Stable memory port owned by the Agent core.

Concrete stores, retrieval algorithms, and extraction prompts live behind this
boundary.  The core can therefore run without the optional memory package and
without making an interactive LLM call for memory work.
"""

from __future__ import annotations

import importlib
import inspect
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MemoryRecallRequest:
    project_id: str
    query: str
    top_k: int = 3
    max_tokens: int = 500


@dataclass(frozen=True)
class MemoryRecallResult:
    context_block: str = ""
    memory_ids: tuple[str, ...] = ()
    token_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MemoryArchiveRequest:
    project_id: str
    user_message: str
    final_answer: str
    tool_steps: tuple[dict[str, Any], ...] = ()
    run_id: str = ""
    trace_id: str = ""


@dataclass(frozen=True)
class MemoryArchiveResult:
    stored_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


class MemoryPort(Protocol):
    async def recall(self, request: MemoryRecallRequest) -> MemoryRecallResult:
        """Return bounded prompt-ready context and opaque memory IDs."""

    async def archive(self, request: MemoryArchiveRequest) -> MemoryArchiveResult:
        """Persist a bounded task summary asynchronously."""

    async def reinforce(self, memory_ids: tuple[str, ...], project_id: str = "") -> None:
        """Mark recalled memories as used, if the provider supports it."""


class NoOpMemory:
    """Zero-cost fallback used when memory is disabled or unavailable."""

    async def recall(self, request: MemoryRecallRequest) -> MemoryRecallResult:
        return MemoryRecallResult()

    async def archive(self, request: MemoryArchiveRequest) -> MemoryArchiveResult:
        return MemoryArchiveResult()

    async def reinforce(self, memory_ids: tuple[str, ...], project_id: str = "") -> None:
        return None

    async def aclose(self) -> None:
        return None


class _ResilientMemory:
    """Contain provider failures so memory never breaks the Agent response."""

    def __init__(self, provider: MemoryPort):
        self.provider = provider

    async def recall(self, request: MemoryRecallRequest) -> MemoryRecallResult:
        try:
            result = await self.provider.recall(request)
            if not isinstance(result, MemoryRecallResult):
                raise TypeError("memory recall returned an invalid result")
            return result
        except Exception:
            logger.warning("[Memory] provider recall failed; using empty context", exc_info=True)
            return MemoryRecallResult(metadata={"degraded": True})

    async def archive(self, request: MemoryArchiveRequest) -> MemoryArchiveResult:
        try:
            result = await self.provider.archive(request)
            if not isinstance(result, MemoryArchiveResult):
                raise TypeError("memory archive returned an invalid result")
            return result
        except Exception:
            logger.warning("[Memory] provider archive failed; continuing without memory", exc_info=True)
            return MemoryArchiveResult(metadata={"degraded": True})

    async def reinforce(self, memory_ids: tuple[str, ...], project_id: str = "") -> None:
        try:
            await self.provider.reinforce(memory_ids, project_id=project_id)
        except Exception:
            logger.warning("[Memory] provider reinforce failed", exc_info=True)

    async def aclose(self) -> None:
        close = getattr(self.provider, "aclose", None)
        if close is None:
            close = getattr(self.provider, "close", None)
        if close is None:
            return
        try:
            result = close()
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.warning("[Memory] provider close failed", exc_info=True)


def _load_factory(provider: str):
    module_name, separator, attribute = provider.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("memory provider must use 'module:factory' syntax")
    module = importlib.import_module(module_name)
    factory = getattr(module, attribute)
    if not callable(factory):
        raise TypeError(f"memory provider is not callable: {provider}")
    return factory


def load_memory(*, llm, settings, **kwargs) -> MemoryPort:
    """Load an optional memory provider without a core import dependency."""

    if not getattr(settings, "agent_memory_enabled", True):
        return NoOpMemory()
    provider = str(
        getattr(settings, "agent_memory_provider", "memory_system.provider:create")
        or ""
    ).strip()
    if not provider or provider.lower() in {"none", "noop", "disabled"}:
        return NoOpMemory()
    try:
        factory = _load_factory(provider)
        instance = factory(llm=llm, settings=settings, **kwargs)
        if not all(callable(getattr(instance, name, None)) for name in ("recall", "archive", "reinforce")):
            raise TypeError("memory provider must expose recall/archive/reinforce")
        return _ResilientMemory(instance)
    except Exception:
        logger.warning("[Memory] provider unavailable; using no-op", exc_info=True)
        return NoOpMemory()
