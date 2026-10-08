"""Host-owned contracts shared with trace providers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class TraceSessionRequest:
    """Metadata used to create one logical trace stream."""

    session_id: str
    user_message: str = ""
    project_file: str = ""
    source_dir: str = ""
    test_dir: str = ""
    design_dir: str = ""
    trace_dir: str = ""
    env_snapshot: dict[str, Any] | None = None


class TraceSink(Protocol):
    """Recorder for one logical trace stream.

    Event-specific methods remain part of the stable port because the Agent
    runtime needs correlation IDs for LLM and tool spans.  A provider may
    persist them as JSONL, OTLP spans, database rows, or another format.
    """

    trace_id: str
    path: str

    def set_run_id(self, run_id: str) -> None: ...

    def start(self, **kwargs: Any) -> None: ...

    def close(self) -> None: ...

    def event(self, event_type: str, **payload: Any) -> dict: ...

    def llm_request(self, **kwargs: Any) -> str: ...

    def llm_response(self, **kwargs: Any) -> None: ...

    def agent_step(self, **kwargs: Any) -> None: ...

    def tool_call(self, **kwargs: Any) -> str: ...

    def tool_result(self, **kwargs: Any) -> None: ...

    def review_request(self, **kwargs: Any) -> None: ...

    def review_response(self, **kwargs: Any) -> None: ...

    def done(self, **kwargs: Any) -> None: ...

    def error(self, **kwargs: Any) -> None: ...


class TraceProvider(Protocol):
    """Factory for trace sinks."""

    def create(self, request: TraceSessionRequest) -> TraceSink: ...

    async def replay(
        self,
        session_id: str,
        *,
        mode: str = "mock",
        until_turn: int | None = None,
        tool_policy: str = "readonly",
    ) -> dict[str, Any]: ...


class TraceReplayExhausted(RuntimeError):
    """The recorded trace cannot satisfy the requested replay sequence."""


class TraceForkPort(Protocol):
    """Optional provider capability for inheriting a recorder's storage policy."""

    def fork(self, sink: TraceSink) -> TraceProvider: ...


class TraceAttachmentPort(Protocol):
    """Optional independent writer for background events in an existing stream."""

    def attach(self, sink: TraceSink, *, run_id: str, task_id: str, owner: str) -> TraceSink: ...


class TraceQueryPort(Protocol):
    """Optional read-side capability exposed by a trace provider."""

    def list_traces(self) -> list[dict]: ...

    def read_trace(self, session_id: str, trace_type: str | None = None) -> dict | None: ...

    def summarize_trace(self, session_id: str) -> dict | None: ...

    def reconstruct_history(self, session_id: str) -> list[dict] | None: ...
