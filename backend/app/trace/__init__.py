"""Trace subsystem.

The stable runtime contracts and concrete adapters are co-located here.  The
legacy ``app.agent_base.core.tracing`` path is retained only as a compatibility
import for existing integrations.
"""

from .tracing import (
    NoOpTraceProvider,
    NoOpTraceSink,
    TraceProvider,
    TraceQueryPort,
    TraceSession,
    TraceSessionRequest,
    TraceSink,
    current_trace_spans,
    emit_trace,
    get_trace_hook,
    load_trace,
    pop_trace_hook,
    push_trace_hook,
    set_trace_hook,
    trace_span,
)

__all__ = [
    "NoOpTraceProvider", "NoOpTraceSink", "TraceProvider", "TraceQueryPort",
    "TraceSession", "TraceSessionRequest", "TraceSink", "current_trace_spans",
    "emit_trace", "get_trace_hook", "load_trace", "pop_trace_hook",
    "push_trace_hook", "set_trace_hook", "trace_span",
]
