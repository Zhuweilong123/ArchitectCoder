"""Tests for the provider-neutral tracing boundary."""

from types import SimpleNamespace

from app.trace.tracing import (
    NoOpTraceProvider,
    TraceSession,
    TraceSessionRequest,
    emit_trace,
    load_trace,
)


class _Sink:
    trace_id = "fake-trace"
    path = "fake.trace"

    def __init__(self):
        self.events = []
        self.closed = False

    def start(self, **kwargs):
        self.events.append(("start", kwargs))

    def close(self):
        self.closed = True

    def error(self, **kwargs):
        self.events.append(("error", kwargs))

    def llm_request(self, **kwargs):
        self.events.append(("llm_request", kwargs))
        return "span-1"

    def llm_response(self, **kwargs):
        self.events.append(("llm_response", kwargs))


class _Provider:
    def __init__(self, expected_session="provider-session"):
        self.expected_session = expected_session
        self.sink = _Sink()

    def create(self, request: TraceSessionRequest):
        assert request.session_id == self.expected_session
        return self.sink


def test_trace_session_uses_provider_and_routes_llm_hook():
    provider = _Provider()

    with TraceSession(
        session_id="provider-session",
        user_message="hello",
        provider=provider,
    ) as tracer:
        assert tracer.trace_id == "fake-trace"
        assert emit_trace(
            "llm_request",
            model="test-model",
            messages=[{"role": "user", "content": "hello"}],
        ) == "span-1"
        emit_trace("llm_response", span_id="span-1", content="ok")

    assert provider.sink.closed is True
    assert [event[0] for event in provider.sink.events] == [
        "start", "llm_request", "llm_response",
    ]


def test_disabled_trace_loads_noop_provider():
    provider = load_trace(settings=SimpleNamespace(agent_trace_enabled=False))
    assert isinstance(provider, NoOpTraceProvider)
    assert provider.create(TraceSessionRequest(session_id="disabled")).trace_id == ""


def test_trace_provider_factory_is_pluggable(monkeypatch):
    module_name = "test_trace_provider_plugin"
    plugin = SimpleNamespace(create=lambda **kwargs: _Provider("plugin-session"))
    monkeypatch.setitem(__import__("sys").modules, module_name, plugin)
    settings = SimpleNamespace(
        agent_trace_enabled=True,
        agent_trace_provider=f"{module_name}:create",
    )

    provider = load_trace(settings=settings)
    sink = provider.create(TraceSessionRequest(session_id="plugin-session"))
    assert sink.trace_id == "fake-trace"
