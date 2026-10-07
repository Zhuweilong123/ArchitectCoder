"""Public memory capability contract owned by the memory extension.

Concrete stores, retrieval algorithms, and extraction prompts live behind this
boundary. The host exchanges typed values with this capability while lifecycle
policy and persistence remain private to the extension.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


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
