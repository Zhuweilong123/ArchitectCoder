"""Loading, fallback and validation adapters for optional capabilities."""

from __future__ import annotations
import inspect
import logging

from extensions.memory.plugin_api import (
    MemoryRecallRequest, MemoryRecallResult, MemoryArchiveRequest, MemoryArchiveResult, MemoryEventRequest, MemoryEventResult, MemoryPort,
)

logger = logging.getLogger(__name__)

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
    from app.agent_base.core.plugins import PluginManager

    return PluginManager._load_factory(provider)


def load_memory(*, llm, settings, **kwargs) -> MemoryPort:
    """Load memory through the central extension manager."""
    from app.agent_base.core.plugins import get_plugin_manager

    instance = get_plugin_manager().load_optional(
        "memory",
        settings=settings,
        kwargs={"llm": llm, **kwargs},
        factory_loader=_load_factory,
    )
    if instance is None:
        return NoOpMemory()
    return _ResilientMemory(instance)
