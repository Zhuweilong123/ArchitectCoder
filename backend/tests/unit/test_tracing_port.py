"""Tests for the provider-neutral tracing boundary."""

from types import SimpleNamespace
import json
from pathlib import Path

from app.agent_base.adapters.tracing import NoOpTraceProvider, load_trace
from app.runtime.trace_session import TraceSession
from app.agent_base.host_api.tracing import TraceSessionRequest
from app.agent_base.core.observability import emit_trace


def test_jsonl_event_merges_run_metadata_and_preserves_recorder_identity(tmp_path):
    from extensions.trace.chat_trace import ChatTraceLogger
    sink = ChatTraceLogger("metadata", log_dir=str(tmp_path))
    sink.set_run_id("bound-run")
    events = [
        sink.event("default"),
        sink.event("explicit", run_id="child-run", trace_id="caller-trace", plan_id="plan-1"),
        sink.event("empty", run_id=""),
    ]
    stored = [json.loads(line) for line in Path(sink.path).read_text(encoding="utf-8").splitlines()]
    assert stored == events
    assert [event["run_id"] for event in stored] == ["bound-run", "child-run", "bound-run"]
    assert all(event["trace_id"] == sink.trace_id for event in stored)
    assert stored[1]["plan_id"] == "plan-1"
    assert sink.run_id == "bound-run"


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

    def tool_call(self, **kwargs):
        self.events.append(("tool_call", kwargs))
        return "tool-1"

    def tool_result(self, **kwargs):
        self.events.append(("tool_result", kwargs))


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
            request_context={"scope": "single_llm_request", "tool_schema_mode": "full"},
        ) == "span-1"
        emit_trace("llm_response", span_id="span-1", content="ok")
        assert emit_trace(
            "tool_call",
            step=1,
            tool_name="read_file",
            arguments={"path": "a.py"},
        ) == "tool-1"
        emit_trace(
            "tool_result",
            span_id="tool-1",
            tool_name="read_file",
            observation="ok",
            duration_ms=12.5,
        )

    assert provider.sink.closed is True
    assert [event[0] for event in provider.sink.events] == [
        "start", "llm_request", "llm_response", "tool_call", "tool_result",
    ]
    assert provider.sink.events[1][1]["request_context"]["tool_schema_mode"] == "full"
    assert provider.sink.events[3][1]["tool_name"] == "read_file"
    assert provider.sink.events[4][1]["observation"] == "ok"


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


def test_provider_without_fork_keeps_its_background_factory(monkeypatch):
    from app.agent_base.adapters.tracing import _ResilientTraceProvider
    provider = _ResilientTraceProvider(_Provider())
    assert provider.fork(_Sink()) is provider


def test_provider_fork_failure_degrades_without_breaking_background_work():
    from app.agent_base.adapters.tracing import _ResilientTraceProvider

    class BrokenProvider(_Provider):
        def fork(self, sink):
            raise OSError("storage unavailable")

    child = _ResilientTraceProvider(BrokenProvider()).fork(_Sink())
    assert isinstance(child, NoOpTraceProvider)
    assert not child.create(TraceSessionRequest(session_id="background")).path


def test_attached_provider_failure_does_not_create_unrelated_trace():
    from app.agent_base.adapters.tracing import _ResilientTraceProvider, NoOpTraceSink
    class BrokenProvider(_Provider):
        def attach(self, sink, **kwargs):
            raise OSError("storage unavailable")
    provider = _ResilientTraceProvider(BrokenProvider())
    assert isinstance(provider.attach(_Sink(), run_id="r", task_id="t", owner="test"), NoOpTraceSink)
    assert _ResilientTraceProvider(_Provider()).attach(_Sink()) is None


def test_replay_ignores_background_model_even_with_agent_span():
    from extensions.trace.replay import _step_level_events
    foreground = {"event_type": "llm_response", "span_path": "DevAgent", "content": "reply"}
    background = {**foreground, "content": "memory", "background_task_id": "task"}
    assert _step_level_events([foreground, background], "llm_response") == [foreground]
