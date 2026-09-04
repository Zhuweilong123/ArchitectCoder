"""Stable tracing port owned by the Agent core.

The core only depends on the trace contracts in this module.  Storage,
serialization, retention and query implementations are loaded as providers.
The default provider is the existing JSONL implementation, so this boundary
can be introduced without changing the current trace format.
"""

from __future__ import annotations

import contextvars
import logging
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TraceSessionRequest:
    """Metadata used to create one logical trace stream."""

    session_id: str
    user_message: str = ""
    project_file: str = ""
    source_dir: str = ""
    test_dir: str = ""
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


class TraceQueryPort(Protocol):
    """Optional read-side capability exposed by a trace provider."""

    def list_traces(self) -> list[dict]: ...

    def read_trace(self, session_id: str) -> dict | None: ...

    def summarize_trace(self, session_id: str) -> dict | None: ...

    def reconstruct_history(self, session_id: str) -> list[dict] | None: ...


class NoOpTraceSink:
    """Zero-cost sink used when tracing is disabled or unavailable."""

    trace_id = ""
    path = ""

    def set_run_id(self, run_id: str) -> None:
        return None

    def start(self, **kwargs: Any) -> None:
        return None

    def close(self) -> None:
        return None

    def event(self, event_type: str, **payload: Any) -> dict:
        return {"event_type": event_type, **payload}

    def llm_request(self, **kwargs: Any) -> str:
        return ""

    def llm_response(self, **kwargs: Any) -> None:
        return None

    def agent_step(self, **kwargs: Any) -> None:
        return None

    def tool_call(self, **kwargs: Any) -> str:
        return ""

    def tool_result(self, **kwargs: Any) -> None:
        return None

    def review_request(self, **kwargs: Any) -> None:
        return None

    def review_response(self, **kwargs: Any) -> None:
        return None

    def done(self, **kwargs: Any) -> None:
        return None

    def error(self, **kwargs: Any) -> None:
        return None


class NoOpTraceProvider:
    def create(self, request: TraceSessionRequest) -> TraceSink:
        return NoOpTraceSink()

    def query(self) -> TraceQueryPort:
        return NoOpTraceQuery()


class NoOpTraceQuery:
    def list_traces(self) -> list[dict]:
        return []

    def read_trace(self, session_id: str) -> dict | None:
        return None

    def summarize_trace(self, session_id: str) -> dict | None:
        return None

    def reconstruct_history(self, session_id: str) -> list[dict] | None:
        return None


class _ResilientTraceSink:
    """Keep a provider failure from breaking the Agent execution path."""

    def __init__(self, sink: TraceSink):
        self._sink = sink

    @property
    def trace_id(self) -> str:
        return str(getattr(self._sink, "trace_id", "") or "")

    @property
    def path(self) -> str:
        return str(getattr(self._sink, "path", "") or "")

    def __getattr__(self, name: str):
        target = getattr(self._sink, name)
        if not callable(target):
            return target

        def safe_call(*args, **kwargs):
            try:
                return target(*args, **kwargs)
            except Exception:
                logger.warning("[Trace] provider operation %s failed", name, exc_info=True)
                if name in {"llm_request", "tool_call"}:
                    return ""
                if name == "event":
                    event_type = args[0] if args else kwargs.get("event_type", "unknown")
                    return {"event_type": event_type, **kwargs}
                return None

        return safe_call


class _ResilientTraceProvider:
    def __init__(self, provider: TraceProvider):
        self.provider = provider

    def create(self, request: TraceSessionRequest) -> TraceSink:
        try:
            sink = self.provider.create(request)
            if sink is None:
                raise TypeError("trace provider returned no sink")
            return _ResilientTraceSink(sink)
        except Exception:
            logger.warning("[Trace] provider could not create sink; using no-op", exc_info=True)
            return NoOpTraceSink()

    def query(self) -> TraceQueryPort:
        try:
            factory = getattr(self.provider, "query", None)
            query = factory() if callable(factory) else factory
            if query is None:
                return NoOpTraceQuery()
            return _ResilientTraceQuery(query)
        except Exception:
            logger.warning("[Trace] provider query is unavailable; using no-op", exc_info=True)
            return NoOpTraceQuery()


class _ResilientTraceQuery:
    def __init__(self, query: TraceQueryPort):
        self.query = query

    def __getattr__(self, name: str):
        target = getattr(self.query, name)

        def safe_call(*args, **kwargs):
            try:
                return target(*args, **kwargs)
            except Exception:
                logger.warning("[Trace] query operation %s failed", name, exc_info=True)
                if name == "list_traces":
                    return []
                return None

        return safe_call


def _load_factory(provider: str):
    """Compatibility hook; actual loading remains owned by PluginManager."""
    from app.agent_base.core.plugins import PluginManager

    return PluginManager._load_factory(provider)


def load_trace(*, settings=None, **kwargs) -> TraceProvider:
    """Load trace through the central extension manager."""
    from app.agent_base.core.plugins import get_plugin_manager

    instance = get_plugin_manager().load(
        "trace",
        settings=settings,
        kwargs=kwargs,
        factory_loader=_load_factory,
    )
    if instance is None:
        return NoOpTraceProvider()
    return _ResilientTraceProvider(instance)


# The hook stack is coroutine-local.  This is the only global state the core
# owns; concrete providers never need to know about other sessions.
_TRACE_HOOK_STACK: contextvars.ContextVar[tuple] = contextvars.ContextVar(
    "trace_hook_stack", default=()
)
_TRACE_SPANS: contextvars.ContextVar[list[str]] = contextvars.ContextVar(
    "trace_spans", default=[]
)


def push_trace_hook(handler) -> None:
    _TRACE_HOOK_STACK.set((*_TRACE_HOOK_STACK.get(), handler))


def pop_trace_hook(handler) -> None:
    stack = _TRACE_HOOK_STACK.get()
    if stack and stack[-1] is handler:
        _TRACE_HOOK_STACK.set(stack[:-1])


def set_trace_hook(handler=None):
    """Compatibility helper; new code should use TraceSession."""
    if handler is None:
        _TRACE_HOOK_STACK.set(())
    else:
        push_trace_hook(handler)


def get_trace_hook():
    stack = _TRACE_HOOK_STACK.get()
    return stack[-1] if stack else None


def emit_trace(kind: str, *args, **kwargs):
    """Safely emit an instrumentation callback from core code."""
    handler = get_trace_hook()
    if handler is None:
        return None
    try:
        return handler(kind, *args, **kwargs)
    except Exception:
        logger.exception("[Trace] hook(%s) failed", kind)
        return None


class trace_span:
    """Coroutine-local span path used to associate nested LLM calls."""

    def __init__(self, name: str):
        self._name = name
        self._token = None

    def __enter__(self):
        spans = list(_TRACE_SPANS.get())
        spans.append(self._name)
        self._token = _TRACE_SPANS.set(spans)
        return self

    def __exit__(self, *args):
        if self._token is not None:
            _TRACE_SPANS.reset(self._token)
            self._token = None

    async def __aenter__(self):
        return self.__enter__()

    async def __aexit__(self, *args):
        self.__exit__(*args)


def current_trace_spans() -> list[str]:
    return list(_TRACE_SPANS.get())


class TraceSession:
    """Provider-neutral trace lifecycle context."""

    def __init__(
        self,
        *,
        session_id: str,
        user_message: str = "",
        project_file: str = "",
        source_dir: str = "",
        test_dir: str = "",
        env_snapshot: dict[str, Any] | None = None,
        provider: TraceProvider | None = None,
        sink: TraceSink | None = None,
    ):
        self._request = TraceSessionRequest(
            session_id=session_id,
            user_message=user_message,
            project_file=project_file,
            source_dir=source_dir,
            test_dir=test_dir,
            env_snapshot=env_snapshot,
        )
        self._provider = provider
        self._tracer = sink
        self._bridge = None

    @property
    def tracer(self) -> TraceSink:
        if self._tracer is None:
            raise RuntimeError("TraceSession not entered")
        return self._tracer

    def _make_bridge(self):
        tracer = self.tracer

        def bridge(kind: str, *args, **kwargs):
            span_path = "/".join(current_trace_spans())
            if kind == "llm_request":
                return tracer.llm_request(
                    provider=kwargs.get("provider", "unknown"),
                    model=kwargs.get("model", ""),
                    messages=kwargs.get("messages", []),
                    temperature=kwargs.get("temperature"),
                    max_tokens=kwargs.get("max_tokens"),
                    tools=kwargs.get("tools"),
                    tool_choice=kwargs.get("tool_choice"),
                    response_format=kwargs.get("response_format"),
                    timeout=kwargs.get("timeout"),
                    span_path=span_path,
                )
            if kind == "llm_response":
                tracer.llm_response(
                    span_id=kwargs.get("span_id", ""),
                    content=kwargs.get("content", ""),
                    tool_calls=kwargs.get("tool_calls"),
                    usage=kwargs.get("usage"),
                    error=kwargs.get("error", ""),
                    duration_ms=kwargs.get("duration_ms", 0.0),
                    span_path=span_path,
                )
            return None

        return bridge

    def __enter__(self):
        if self._tracer is None:
            provider = self._provider or load_trace()
            self._tracer = provider.create(self._request)
        self._tracer.start(
            user_message=self._request.user_message,
            project_file=self._request.project_file,
            source_dir=self._request.source_dir,
            test_dir=self._request.test_dir,
            env_snapshot=self._request.env_snapshot,
        )
        self._bridge = self._make_bridge()
        push_trace_hook(self._bridge)
        return self._tracer

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if self._bridge is not None:
                pop_trace_hook(self._bridge)
            if exc_type is not None and self._tracer is not None:
                self._tracer.error(
                    event_type="exception",
                    message=f"{exc_type.__name__}: {exc_val}",
                )
        finally:
            if self._tracer is not None:
                self._tracer.close()
        return False

    async def __aenter__(self):
        return self.__enter__()

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return self.__exit__(exc_type, exc_val, exc_tb)


__all__ = [
    "NoOpTraceProvider",
    "NoOpTraceSink",
    "TraceProvider",
    "TraceQueryPort",
    "TraceSession",
    "TraceSessionRequest",
    "TraceSink",
    "current_trace_spans",
    "emit_trace",
    "get_trace_hook",
    "load_trace",
    "pop_trace_hook",
    "push_trace_hook",
    "set_trace_hook",
    "trace_span",
]
