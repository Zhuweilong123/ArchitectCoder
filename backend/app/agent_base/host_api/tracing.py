"""Host-owned contracts shared with trace providers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypedDict, Unpack
from types import TracebackType
from collections.abc import Sequence
from .data import JsonObject, JsonValue, ModelMessage, ModelToolCall, TokenUsage, ToolSchema, ConversationMessage


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
    env_snapshot: JsonObject | None = None


class TraceSink(Protocol):
    """Recorder for one logical trace stream.

    Event-specific methods remain part of the stable port because the Agent
    runtime needs correlation IDs for LLM and tool spans.  A provider may
    persist them as JSONL, OTLP spans, database rows, or another format.
    """

    trace_id: str
    path: str

    def set_run_id(self, run_id: str) -> None: ...

    def start(self, *, user_message: str = '', project_file: str = '', source_dir: str = '', test_dir: str = '', workspace_root: str = '', design_dir: str = '', env_snapshot: JsonObject | None = None) -> None: ...

    def user_message(self, message: str, *, project_file: str = '', source_dir: str = '', test_dir: str = '', workspace_root: str = '', design_dir: str = '') -> None: ...

    def task_summary(self, *, summary: str, status: str = "", tool_call_count: int = 0, turn: int | None = None) -> None: ...

    def context_compacted(self, *, summary: str, dropped_messages: int, dropped_tokens: int, reason: str, triggered_by: list[str], tool_call_count: int, token_budget_used: int, context_target_tokens: int) -> None: ...

    def close(self) -> None: ...

    def event(self, event_type: str, **payload: JsonValue) -> JsonObject: ...

    def llm_request(self, *, provider: str, model: str, messages: list[ModelMessage], temperature: float | None, max_tokens: int | None, tools: list[ToolSchema] | None = None, tool_choice: str | None = None, response_format: JsonObject | None = None, timeout: float | None = None, request_context: JsonObject | None = None, span_id: str = '', span_path: str = '') -> str: ...

    def llm_response(self, *, span_id: str, content: str, tool_calls: list[ModelToolCall] | None = None, usage: TokenUsage | None = None, error: str = '', duration_ms: float = 0.0, span_path: str = '') -> None: ...

    def agent_step(self, *, step: int, thought: str = '', actions: list[str] | None = None, is_final: bool = False) -> None: ...

    def tool_call(self, *, step: int, tool_name: str, arguments: JsonObject, parent_span_id: str = '', span_path: str = '') -> str: ...

    def tool_result(self, *, span_id: str, tool_name: str, observation: str, duration_ms: float = 0.0, error: str = '', fed_truncated: bool = False, fed_length: int = 0, evidence: JsonObject | None = None, span_path: str = '') -> None: ...

    def review_request(self, *, review_id: int, review_type: str, title: str, question: str, content: str = '', metadata: JsonObject | None = None) -> None: ...

    def review_response(self, *, review_id: int, response: str, review_type: str = '', decision: str = '', feedback: str = '', candidate_recovery: bool = False) -> None: ...

    def done(self, *, answer: str, runtime: JsonObject | None = None) -> None: ...

    def error(self, *, event_type: str, message: str) -> None: ...


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
    ) -> JsonObject: ...


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

    def list_traces(self) -> list[JsonObject]: ...

    def read_trace(self, session_id: str, trace_type: str | None = None) -> JsonObject | None: ...

    def summarize_trace(self, session_id: str) -> JsonObject | None: ...

    def reconstruct_history(self, session_id: str) -> list[ConversationMessage] | None: ...


class BackgroundRecordPort(Protocol):
    def to_dict(self) -> JsonObject: ...

class BackgroundRegistryPort(Protocol):
    async def drain(self, timeout_seconds: float = 5.0) -> None: ...

    @property
    def records(self) -> Sequence[BackgroundRecordPort]: ...

class TraceSessionPort(Protocol):
    background_registry: BackgroundRegistryPort | None
    async def __aenter__(self) -> TraceSink: ...
    async def __aexit__(self, exc_type: type[BaseException] | None, exc_val: BaseException | None,
                        exc_tb: TracebackType | None) -> bool: ...

class TraceHookPayload(TypedDict, total=False):
    provider: str
    model: str
    messages: list[ModelMessage]
    temperature: float | None
    max_tokens: int | None
    tools: list[ToolSchema] | None
    tool_choice: str | None
    response_format: JsonObject | None
    timeout: float | None
    request_context: JsonObject | None
    span_id: str
    content: str | None
    tool_calls: list[ModelToolCall] | None
    usage: TokenUsage | None
    error: str
    duration_ms: float
    step: int
    tool_name: str
    arguments: JsonObject
    parent_span_id: str
    observation: str
    fed_truncated: bool
    fed_length: int
    evidence: JsonObject | None
    event_type: str
    payload: JsonObject

class TraceHookPort(Protocol):
    def __call__(self, kind: str, **payload: Unpack[TraceHookPayload]) -> str | None: ...
