"""Stable memory port owned by the Agent core.

Concrete stores, retrieval algorithms, and extraction prompts live behind this
boundary.  The core can therefore run without the optional memory package and
without making an interactive LLM call for memory work.
"""

from __future__ import annotations

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
    scope_context: dict[str, str] = field(default_factory=dict)


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
    conversation_history: tuple[dict[str, str], ...] = ()
    terminal_status: str = "completed"
    resources: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class MemoryArchiveResult:
    stored_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MemoryEventRequest:
    """Host evidence, independent of the concrete resource/store implementation."""

    project_id: str
    event_type: str
    tool_steps: tuple[dict[str, Any], ...] = ()
    resources: tuple[dict[str, Any], ...] = ()
    memory_ids: tuple[str, ...] = ()
    reason: str = ""
    run_id: str = ""
    trace_id: str = ""
    event_id: str = ""


@dataclass(frozen=True)
class MemoryEventResult:
    affected_count: int = 0
    resources: tuple[dict[str, Any], ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


class MemoryPort(Protocol):
    async def recall(self, request: MemoryRecallRequest) -> MemoryRecallResult:
        """Return bounded prompt-ready context and opaque memory IDs."""

    async def archive(self, request: MemoryArchiveRequest) -> MemoryArchiveResult:
        """Persist a bounded task summary asynchronously."""

    async def reinforce(self, memory_ids: tuple[str, ...], project_id: str = "") -> None:
        """Explicit confirmation; recall alone must never call this method."""

    async def observe(self, request: MemoryEventRequest) -> MemoryEventResult:
        """Record evidence and reassess affected knowledge (optional capability)."""


class NoOpMemory:
    """Zero-cost fallback used when memory is disabled or unavailable."""

    async def recall(self, request: MemoryRecallRequest) -> MemoryRecallResult:
        return MemoryRecallResult()

    async def archive(self, request: MemoryArchiveRequest) -> MemoryArchiveResult:
        return MemoryArchiveResult()

    async def reinforce(self, memory_ids: tuple[str, ...], project_id: str = "") -> None:
        return None

    async def observe(self, request: MemoryEventRequest) -> MemoryEventResult:
        return MemoryEventResult()

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

    async def observe(self, request: MemoryEventRequest) -> MemoryEventResult:
        observe = getattr(self.provider, "observe", None)
        if observe is None:
            return MemoryEventResult(metadata={"skipped": "unsupported"})
        try:
            result = await observe(request)
            if not isinstance(result, MemoryEventResult):
                raise TypeError("memory observe returned an invalid result")
            return result
        except Exception:
            logger.warning("[Memory] provider observe failed; continuing", exc_info=True)
            return MemoryEventResult(metadata={"degraded": True})

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
    """Compatibility hook; actual loading remains owned by PluginManager."""
    from .plugins import PluginManager

    return PluginManager._load_factory(provider)


def load_memory(*, llm, settings, **kwargs) -> MemoryPort:
    """Load memory through the central extension manager."""
    from .plugins import get_plugin_manager

    instance = get_plugin_manager().load_optional(
        "memory",
        settings=settings,
        kwargs={"llm": llm, **kwargs},
        factory_loader=_load_factory,
    )
    if instance is None:
        return NoOpMemory()
    return _ResilientMemory(instance)
