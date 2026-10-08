"""Host-owned trace and background resource lifetimes."""

from __future__ import annotations

from typing import Any

from app.agent_base.host_api.tracing import TraceSessionRequest, TraceSink, TraceProvider
from app.agent_base.adapters.tracing import load_trace
from app.agent_base.core.observability import (
    current_trace_spans, set_current_trace_sink, reset_current_trace_sink,
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
        env_snapshot: dict[str, Any] | None = None,
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
        self.background_registry = None
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

    def _make_bridge(self):
        tracer = self.tracer

        def bridge(kind: str, *args, **kwargs):
            span_path = "/".join(current_trace_spans())
            if kind == "event":
                payload = kwargs.get("payload")
                tracer.event(
                    str(kwargs.get("event_type") or "custom_trace_event"),
                    payload=payload if isinstance(payload, dict) else {},
                    span_path=span_path,
                )
                return None
            if kind == "llm_request":
                from app.agent_base.core.background_tasks import observe_background_llm
                observe_background_llm(kind)
                self.llm_requests += 1
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
                    request_context=kwargs.get("request_context"),
                    span_path=span_path,
                )
            if kind == "llm_response":
                from app.agent_base.core.background_tasks import observe_background_llm
                observe_background_llm(kind, kwargs.get("usage"))
                self.llm_responses += 1
                usage = kwargs.get("usage") or {}
                if usage.get("total_tokens") is not None:
                    self.usage_responses += 1
                for key in self.usage:
                    self.usage[key] += int(usage.get(key) or 0)
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
            if kind == "tool_call":
                return tracer.tool_call(
                    step=int(kwargs.get("step") or 0),
                    tool_name=kwargs.get("tool_name", ""),
                    arguments=kwargs.get("arguments") if isinstance(kwargs.get("arguments"), dict) else {},
                    parent_span_id=kwargs.get("parent_span_id", ""),
                    span_path=span_path,
                )
            if kind == "tool_result":
                tracer.tool_result(
                    span_id=kwargs.get("span_id", ""),
                    tool_name=kwargs.get("tool_name", ""),
                    observation=str(kwargs.get("observation", "")),
                    duration_ms=float(kwargs.get("duration_ms") or 0.0),
                    error=kwargs.get("error", ""),
                    fed_truncated=bool(kwargs.get("fed_truncated", False)),
                    fed_length=int(kwargs.get("fed_length") or 0),
                    evidence=kwargs.get("evidence") if isinstance(kwargs.get("evidence"), dict) else None,
                    span_path=span_path,
                )
            return None

        return bridge

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
        provider = self._provider or load_trace()
        attach = getattr(provider, "attach", None)
        if callable(attach):
            sink = attach(self.tracer, run_id=record.run_id, task_id=record.task_id, owner=record.owner)
            if sink is not None:
                return await background_trace(record, run, sink=sink)
        fork = getattr(provider, "fork", None)
        if callable(fork):
            provider = fork(self.tracer)
        return await background_trace(record, run, provider=provider)

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


async def background_trace(record, run, *, trace_dir="", provider=None, sink=None):
    """Record background work in an attached stream, or standalone without a parent."""
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
