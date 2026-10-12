"""One event-to-sink adapter for chat, evaluations and background runs."""
from __future__ import annotations
from collections.abc import Callable
from typing import Unpack
from app.agent_base.host_api.tracing import TraceHookPayload, TraceSink
from app.agent_base.core.observability import current_trace_spans

class TraceHookBridge:
    def __init__(self, sink: TraceSink, observer: Callable[[str, TraceHookPayload], None] | None = None) -> None:
        self._sink = sink
        self._observer = observer

    def __call__(self, kind: str, **payload: Unpack[TraceHookPayload]) -> str | None:
        sink = self._sink
        span_path = '/'.join(current_trace_spans())
        if kind in ('llm_request', 'llm_response') and self._observer is not None:
            self._observer(kind, payload)
        if kind == 'event':
            sink.event(payload.get('event_type') or 'custom_trace_event',
                       payload=payload.get('payload') or {}, span_path=span_path)
        elif kind == 'llm_request':
            return sink.llm_request(provider=payload.get('provider', 'unknown'),
                model=payload.get('model', ''), messages=payload.get('messages', []),
                temperature=payload.get('temperature'), max_tokens=payload.get('max_tokens'),
                tools=payload.get('tools'), tool_choice=payload.get('tool_choice'),
                response_format=payload.get('response_format'), timeout=payload.get('timeout'),
                request_context=payload.get('request_context'), span_path=span_path)
        elif kind == 'llm_response':
            sink.llm_response(span_id=payload.get('span_id', ''), content=payload.get('content') or '',
                tool_calls=payload.get('tool_calls'), usage=payload.get('usage'),
                error=payload.get('error', ''), duration_ms=payload.get('duration_ms', 0.0), span_path=span_path)
        elif kind == 'tool_call':
            return sink.tool_call(step=payload.get('step') or 0, tool_name=payload.get('tool_name', ''),
                arguments=payload.get('arguments') or {}, parent_span_id=payload.get('parent_span_id', ''), span_path=span_path)
        elif kind == 'tool_result':
            sink.tool_result(span_id=payload.get('span_id', ''), tool_name=payload.get('tool_name', ''),
                observation=payload.get('observation', ''), duration_ms=payload.get('duration_ms') or 0.0,
                error=payload.get('error', ''), fed_truncated=payload.get('fed_truncated', False),
                fed_length=payload.get('fed_length') or 0, evidence=payload.get('evidence'), span_path=span_path)
        return None
