"""Run-scoped background work; no dependency on a particular extension."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)
_active_tasks: set[asyncio.Task] = set()
_registry: ContextVar[BackgroundTaskRegistry | None] = ContextVar("background_registry", default=None)
_record: ContextVar[BackgroundTaskRecord | None] = ContextVar("background_record", default=None)


@dataclass
class BackgroundTaskRecord:
    owner: str
    run_id: str
    source_trace_id: str
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = "pending"
    duration_ms: float = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    task: asyncio.Task | None = field(default=None, repr=False)

    def to_dict(self):
        return {"task_id": self.task_id, "owner": self.owner, "run_id": self.run_id,
                "source_trace_id": self.source_trace_id, "status": self.status,
                "duration_ms": self.duration_ms, **self.metadata}


class BackgroundTaskRegistry:
    def __init__(self, wrapper: Callable | None = None):
        self.records: list[BackgroundTaskRecord] = []
        self.wrapper = wrapper

    def submit(self, work: Awaitable, *, owner: str, run_id: str = "", source_trace_id: str = ""):
        record = BackgroundTaskRecord(owner, run_id, source_trace_id)
        self.records.append(record)
        from app.trace.tracing import emit_trace
        emit_trace("event", event_type="background_scheduled", payload={
            "task_id": record.task_id, "owner": owner, "source_run_id": run_id,
            "source_trace_id": source_trace_id, "status": "pending",
        })

        async def run():
            started = time.monotonic()
            token = _record.set(record)
            record.metadata.update(usage={"prompt_tokens": 0, "completion_tokens": 0,
                                          "total_tokens": 0, "cached_tokens": 0},
                                   llm_requests=0, llm_responses=0, usage_responses=0)
            try:
                result = await work
                metadata = getattr(result, "metadata", {}) or {}
                record.status = "degraded" if metadata.get("degraded") else "completed"
                return result
            except asyncio.CancelledError:
                record.status = "cancelled"
                raise
            except Exception as exc:
                record.status = "failed"
                record.metadata["error_type"] = type(exc).__name__
                logger.warning("Background task %s failed", owner, exc_info=True)
            finally:
                record.duration_ms = round((time.monotonic() - started) * 1000, 1)
                record.metadata["usage_complete"] = (
                    record.metadata["llm_requests"] == record.metadata["llm_responses"]
                    == record.metadata["usage_responses"]
                )
                _record.reset(token)

        async def execute():
            if self.wrapper is not None:
                return await self.wrapper(record, run)
            return await run()

        record.task = asyncio.create_task(execute(), name=f"background:{owner}:{run_id}")
        _active_tasks.add(record.task)
        def done(task):
            _active_tasks.discard(task)
            if record.status == "pending":
                record.status = "cancelled" if task.cancelled() else "failed"
                record.metadata["usage_complete"] = False
                if hasattr(work, "close"):
                    work.close()
            if not task.cancelled():
                task.exception()  # Consume wrapper/setup failures too.
        record.task.add_done_callback(done)
        return record.task

    async def drain(self, timeout_seconds: float):
        deadline = time.monotonic() + max(0, timeout_seconds)
        try:
            while True:
                tasks = [r.task for r in self.records if r.task is not None and not r.task.done()]
                if not tasks:
                    return
                _, pending = await asyncio.wait(tasks, timeout=max(0, deadline - time.monotonic()))
                if pending:
                    await self._cancel_and_drain()
                    return
        except asyncio.CancelledError:
            await self._cancel_and_drain()
            raise

    async def _cancel_and_drain(self):
        tasks = [r.task for r in self.records if r.task is not None and not r.task.done()]
        for task in tasks:
            task.cancel()
        # Repeated owner cancellation must not release workspace resources early.
        cleanup = asyncio.gather(*tasks, return_exceptions=True)
        interrupted = False
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                interrupted = True
        if interrupted:
            raise asyncio.CancelledError


def bind_background_registry(registry):
    return _registry.set(registry)


def reset_background_registry(token):
    _registry.reset(token)


def submit_background(work: Awaitable, *, owner: str, run_id: str = "", source_trace_id: str = ""):
    registry = _registry.get()
    if registry is None:
        from app.trace.tracing import background_trace
        registry = BackgroundTaskRegistry(background_trace)
    return registry.submit(work, owner=owner, run_id=run_id, source_trace_id=source_trace_id)


def observe_background_llm(kind: str, usage=None):
    record = _record.get()
    if record is None:
        return
    if kind == "llm_request":
        record.metadata["llm_requests"] += 1
    elif kind == "llm_response":
        record.metadata["llm_responses"] += 1
        usage = usage or {}
        if usage.get("total_tokens") is not None:
            record.metadata["usage_responses"] += 1
        for key in record.metadata["usage"]:
            record.metadata["usage"][key] += int(usage.get(key) or 0)
