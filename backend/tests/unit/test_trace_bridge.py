"""Shared instrumentation retains correlation, isolation and session accounting."""
import asyncio
import json
from pathlib import Path
from app.agent_base.core.observability import emit_trace, trace_span, push_trace_hook, pop_trace_hook
from app.runtime.trace_bridge import TraceHookBridge
from app.runtime.trace_session import TraceSession
from extensions.trace.chat_trace import ChatTraceLogger


def events(sink):
    return [json.loads(line) for line in Path(sink.path).read_text(encoding='utf-8').splitlines()]


def emit_round():
    request = emit_trace('llm_request', provider='test', model='test', messages=[], temperature=0.2)
    tool = emit_trace('tool_call', step=1, tool_name='read_file', arguments={'path': 'src.py'}, parent_span_id=request)
    emit_trace('tool_result', span_id=tool, tool_name='read_file', observation='code', fed_truncated=True, fed_length=2)
    emit_trace('llm_response', span_id=request, content='answer', usage={'prompt_tokens': 4, 'completion_tokens': 1, 'total_tokens': 5})
    emit_trace('event', event_type='provider_event', payload={'value': 1})
    return request, tool


def test_chat_bridge_and_trace_session_forward_the_same_correlated_events(tmp_path):
    chat = ChatTraceLogger('chat-bridge', log_dir=str(tmp_path))
    chat.start()
    bridge = TraceHookBridge(chat)
    push_trace_hook(bridge)
    try:
        with trace_span('root'):
            request, tool = emit_round()
    finally:
        pop_trace_hook(bridge)
        chat.close()
    session = TraceSession(session_id='evaluation-bridge', sink=ChatTraceLogger('evaluation-bridge', log_dir=str(tmp_path)))
    with session as sink, trace_span('root'):
        emit_round()
    rows = events(chat)
    call = next(row for row in rows if row['event_type'] == 'tool_call')
    result = next(row for row in rows if row['event_type'] == 'tool_result')
    assert call['parent_span_id'] == request
    assert result['span_id'] == tool and result['fed_length'] == 2
    assert next(row for row in rows if row['event_type'] == 'provider_event')['payload'] == {'value': 1}
    wanted = {'llm_request', 'llm_response', 'tool_call', 'tool_result', 'provider_event'}
    assert [row['event_type'] for row in rows if row['event_type'] in wanted] == [row['event_type'] for row in events(sink) if row['event_type'] in wanted]
    assert all(row['span_path'] == 'root' for row in rows if row['event_type'] in wanted)
    assert session.usage['total_tokens'] == 5
    assert session.llm_requests == session.llm_responses == session.usage_responses == 1


def test_concurrent_trace_sessions_do_not_share_bridge_state(tmp_path):
    async def run(name):
        session = TraceSession(session_id=name, sink=ChatTraceLogger(name, log_dir=str(tmp_path)))
        async with session as sink:
            with trace_span(name):
                span = emit_trace('llm_request', model=name, messages=[])
                await asyncio.sleep(0)
                emit_trace('llm_response', span_id=span, content=name, usage={'total_tokens': 1})
        return events(sink)
    async def both():
        return await asyncio.gather(run('first'), run('second'))
    first, second = asyncio.run(both())
    for rows, name in [(first, 'first'), (second, 'second')]:
        response = next(row for row in rows if row['event_type'] == 'llm_response')
        assert response['content'] == response['span_path'] == name
        assert {row['session_id'] for row in rows} == {name}


def test_recorder_error_cannot_escape_into_agent_execution():
    class BrokenSink:
        def llm_request(self, **kwargs):
            raise OSError('recorder unavailable')
    bridge = TraceHookBridge(BrokenSink())
    push_trace_hook(bridge)
    try:
        assert emit_trace('llm_request', model='test', messages=[]) is None
    finally:
        pop_trace_hook(bridge)
