"""Coroutine-local event routing and execution spans."""

from __future__ import annotations

import contextvars
import logging

from app.agent_base.host_api.tracing import TraceSink

logger = logging.getLogger(__name__)

# The hook stack is coroutine-local.  This is the only global state the core
# owns; concrete providers never need to know about other sessions.
_TRACE_HOOK_STACK: contextvars.ContextVar[tuple] = contextvars.ContextVar(
    "trace_hook_stack", default=()
)
_TRACE_SPANS: contextvars.ContextVar[list[str]] = contextvars.ContextVar(
    "trace_spans", default=[]
)
_ACTIVE_TRACE_SINK: contextvars.ContextVar[TraceSink | None] = contextvars.ContextVar(
    "active_trace_sink", default=None
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


def current_trace_sink() -> TraceSink | None:
    """Return only the trace belonging to the current coroutine's session."""
    return _ACTIVE_TRACE_SINK.get()


def set_current_trace_sink(sink: TraceSink):
    """Bind a trace sink for runtimes that manage its lifecycle directly."""
    return _ACTIVE_TRACE_SINK.set(sink)


def reset_current_trace_sink(token) -> None:
    _ACTIVE_TRACE_SINK.reset(token)


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
