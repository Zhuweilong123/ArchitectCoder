"""Host-owned trace and background resource lifetimes."""

from __future__ import annotations

from app.agent_base.host_api.data import JsonObject
from app.agent_base.host_api.tracing import TraceHookPayload
from app.runtime.trace_bridge import TraceHookBridge
from app.agent_base.host_api.tracing import BackgroundRegistryPort

from app.agent_base.host_api.tracing import TraceSessionRequest, TraceSink, TraceProvider
from app.agent_base.adapters.tracing import load_trace
from app.agent_base.core.observability import (
    set_current_trace_sink, reset_current_trace_sink,
    push_trace_hook, pop_trace_hook,
)


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
        design_dir: str = "",
        trace_dir: str = "",
        env_snapshot: JsonObject | None = None,
        provider: TraceProvider | None = None,
        sink: TraceSink | None = None,
        background_timeout_seconds: float | None = None,
    ):
        self._request = TraceSessionRequest(
            session_id=session_id,
            user_message=user_message,
            project_file=project_file,
            source_dir=source_dir,
            test_dir=test_dir,
            design_dir=design_dir,
            trace_dir=trace_dir,
            env_snapshot=env_snapshot,
        )
        self._provider = provider
        self._tracer = sink
        self._bridge = None
        self._sink_token = None
        self.background_timeout_seconds = background_timeout_seconds
        self.background_registry: BackgroundRegistryPort | None = None
        self._background_token = None
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cached_tokens": 0}
        self.llm_requests = 0
        self.llm_responses = 0
        self.usage_responses = 0
        self._output_store_token = None

    @property
    def tracer(self) -> TraceSink:
        if self._tracer is None:
            raise RuntimeError("TraceSession not entered")
        return self._tracer

    def _observe_trace(self, kind: str, payload: TraceHookPayload) -> None:
        from app.agent_base.core.background_tasks import observe_background_llm
        if kind == "llm_request":
            observe_background_llm(kind)
            self.llm_requests += 1
        elif kind == "llm_response":
            observe_background_llm(kind, payload.get("usage"))
            self.llm_responses += 1
            usage = payload.get("usage") or {}
            if usage.get("total_tokens") is not None:
                self.usage_responses += 1
            for key in self.usage:
                self.usage[key] += int(usage.get(key) or 0)

    def _make_bridge(self) -> TraceHookBridge:
        return TraceHookBridge(self.tracer, self._observe_trace)

    def __enter__(self):
        from app.runtime.tool_outputs import ToolOutputStore, bind_tool_output_store
        from app.agent_base.core.background_tasks import BackgroundTaskRegistry, bind_background_registry
        if self._tracer is None:
            self._provider = self._provider or load_trace()
            self._tracer = self._provider.create(self._request)
        self._tracer.start(
            user_message=self._request.user_message,
            project_file=self._request.project_file,
            source_dir=self._request.source_dir,
            test_dir=self._request.test_dir,
            design_dir=self._request.design_dir,
            env_snapshot=self._request.env_snapshot,
        )
        self._bridge = self._make_bridge()
        self._sink_token = set_current_trace_sink(self._tracer)
        push_trace_hook(self._bridge)
        self.background_registry = BackgroundTaskRegistry(
            self._background_trace,
        )
        self._background_token = bind_background_registry(self.background_registry)
        self._output_store_token = bind_tool_output_store(ToolOutputStore())
        return self._tracer

    async def _background_trace(self, record, run):
        """Background writes share the stream, with an independent recorder lifetime."""
        return await background_trace(record, run, provider=self._provider, parent_sink=self.tracer)

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if self._bridge is not None:
                pop_trace_hook(self._bridge)
            if self._sink_token is not None:
                reset_current_trace_sink(self._sink_token)
                self._sink_token = None
            if exc_type is not None and self._tracer is not None:
                self._tracer.error(
                    event_type="exception",
                    message=f"{exc_type.__name__}: {exc_val}",
                )
        finally:
            if self._output_store_token is not None:
                from app.runtime.tool_outputs import reset_tool_output_store
                reset_tool_output_store(self._output_store_token)
                self._output_store_token = None
            if self._background_token is not None:
                from app.agent_base.core.background_tasks import reset_background_registry
                reset_background_registry(self._background_token)
                self._background_token = None
            if self._tracer is not None:
                self._tracer.close()
        return False

    async def __aenter__(self):
        return self.__enter__()

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        try:
            if self.background_timeout_seconds is not None and self.background_registry is not None:
                try:
                    await self.background_registry.drain(self.background_timeout_seconds)
                finally:
                    self.tracer.event("background_settled", tasks=[r.to_dict() for r in self.background_registry.records])
        finally:
            self.__exit__(exc_type, exc_val, exc_tb)
        return False


async def background_trace(record, run, *, trace_dir="", provider=None, sink=None, parent_sink=None):
    """Record background work in an attached stream, or standalone without a parent."""
    if sink is None and parent_sink is not None:
        provider = provider or load_trace()
        attach = getattr(provider, "attach", None)
        if callable(attach):
            sink = attach(parent_sink, run_id=record.run_id, task_id=record.task_id, owner=record.owner)
        if sink is None:
            fork = getattr(provider, "fork", None)
            if callable(fork):
                provider = fork(parent_sink)
    child = TraceSession(
        session_id=f"background_{record.task_id}", trace_dir=trace_dir, provider=provider, sink=sink,
        env_snapshot={"source_run_id": record.run_id, "source_trace_id": record.source_trace_id,
                      "owner": record.owner, "task_id": record.task_id},
    )
    with child as sink:
        sink.set_run_id(record.run_id)
        record.metadata.update(trace_id=sink.trace_id, trace_path=sink.path)
        sink.event("background_started", task_id=record.task_id, owner=record.owner, status="running")
        try:
            return await run()
        finally:
            record.metadata.update(usage=dict(child.usage), usage_complete=(
                child.llm_requests == child.llm_responses == child.usage_responses
            ))
            sink.event("background_result", **record.to_dict())
