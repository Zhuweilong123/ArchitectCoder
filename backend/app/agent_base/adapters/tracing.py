"""Provider loading and fault isolation for optional tracing."""

from __future__ import annotations

import logging
from typing import Any

from app.agent_base.host_api.tracing import (
    TraceSessionRequest, TraceSink, TraceProvider, TraceQueryPort, TraceReplayExhausted,
)

logger = logging.getLogger(__name__)


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
    def attach(self, sink, **kwargs):
        return NoOpTraceSink()

    def fork(self, sink):
        return self

    def create(self, request: TraceSessionRequest) -> TraceSink:
        return NoOpTraceSink()

    async def replay(
        self,
        session_id: str,
        *,
        mode: str = "mock",
        until_turn: int | None = None,
        tool_policy: str = "readonly",
    ) -> dict[str, Any]:
        raise RuntimeError("trace provider is disabled")

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

    def fork(self, sink):
        try:
            fork = getattr(self.provider, "fork", None)
            if not callable(fork):
                return self
            provider = fork(sink)
            if provider is None:
                raise TypeError("trace provider returned no child provider")
            return _ResilientTraceProvider(provider)
        except Exception:
            logger.warning("[Trace] provider fork failed; using no-op", exc_info=True)
            return NoOpTraceProvider()

    def attach(self, sink, **kwargs):
        try:
            attach = getattr(self.provider, "attach", None)
            if not callable(attach):
                return None
            source = sink._sink if isinstance(sink, _ResilientTraceSink) else sink
            child = attach(source, **kwargs)
            if child is None:
                raise TypeError("trace provider returned no attached sink")
            return _ResilientTraceSink(child)
        except Exception:
            logger.warning("[Trace] provider attach failed; using no-op", exc_info=True)
            return NoOpTraceSink()

    def create(self, request: TraceSessionRequest) -> TraceSink:
        try:
            sink = self.provider.create(request)
            if sink is None:
                raise TypeError("trace provider returned no sink")
            return _ResilientTraceSink(sink)
        except Exception:
            logger.warning("[Trace] provider could not create sink; using no-op", exc_info=True)
            return NoOpTraceSink()

    async def replay(
        self,
        session_id: str,
        *,
        mode: str = "mock",
        until_turn: int | None = None,
        tool_policy: str = "readonly",
    ) -> dict[str, Any]:
        try:
            replay = getattr(self.provider, "replay")
            return await replay(
                session_id,
                mode=mode,
                until_turn=until_turn,
                tool_policy=tool_policy,
            )
        except TraceReplayExhausted:
            raise
        except Exception:
            logger.warning("[Trace] provider replay failed", exc_info=True)
            raise

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

    instance = get_plugin_manager().load_optional(
        "trace",
        settings=settings,
        kwargs=kwargs,
        factory_loader=_load_factory,
    )
    if instance is None:
        return NoOpTraceProvider()
    return _ResilientTraceProvider(instance)
