"""Nested execution intervals, independent of public phase and business state."""
from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass
class Operation:
    operation_id: str
    parent_operation_id: str
    operation_kind: str
    scope: str
    run_id: str
    stage: str
    plugin: str = ""
    interface_id: str = ""
    contribution_id: str = ""
    status: str = "completed"


_current: ContextVar[Operation | None] = ContextVar("plugin_operation", default=None)


def current_operation():
    return _current.get()


def _record(operation, status, **details):
    try:
        from app.trace.tracing import current_trace_sink
        from .hooks import get_hooks
        sink = current_trace_sink()
        if sink is not None:
            sink.event("operation", operation_id=operation.operation_id, parent_operation_id=operation.parent_operation_id,
                       operation_kind=operation.operation_kind, scope=operation.scope, run_id=operation.run_id,
                       stage=operation.stage, plugin=operation.plugin, interface_id=operation.interface_id,
                       contribution_id=operation.contribution_id, status=status, plan_id=get_hooks().plan_id, **details)
    except Exception:
        pass  # Observability cannot replace the original execution outcome.


@contextmanager
def operation_scope(kind, *, run_id="", stage="", scope="run", plugin="", interface_id="", contribution_id="", parent_operation_id=None):
    parent = current_operation()
    operation = Operation(uuid.uuid4().hex, parent_operation_id if parent_operation_id is not None else parent.operation_id if parent else "", kind,
                          scope, run_id or (parent.run_id if parent else ""),
                          stage or (parent.stage if parent else "run_start"), plugin, interface_id, contribution_id)
    token = _current.set(operation)
    started = time.monotonic()
    _record(operation, "running")
    details = {}
    try:
        yield operation
    except BaseException as exc:
        from .exceptions import AgentInterrupted
        if not (isinstance(exc, GeneratorExit) and kind == "run" and operation.status in {"completed", "failed", "cancelled"}):
            operation.status = "cancelled" if isinstance(exc, (asyncio.CancelledError, GeneratorExit, AgentInterrupted)) else "failed"
        details = {"error_type": type(exc).__name__, "error_message": str(exc)}
        raise
    finally:
        _record(operation, operation.status, duration_ms=round((time.monotonic() - started) * 1000, 3), **details)
        _current.reset(token)
