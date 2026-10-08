import asyncio
import json
from pathlib import Path

from app.agent_base.core.background_tasks import submit_background
from app.runtime.trace_session import TraceSession
from app.agent_base.core.observability import emit_trace
from extensions.trace.chat_trace import ChatTraceLogger


def rows(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]


def test_evaluation_settles_background_response_before_trace_closes(tmp_path):
    async def run():
        session = TraceSession(session_id="eval-background", sink=ChatTraceLogger(
            "eval-background", log_dir=str(tmp_path)), background_timeout_seconds=1)
        async with session as sink:
            async def work():
                span = emit_trace("llm_request", model="fake", messages=[])
                await asyncio.sleep(0.01)
                emit_trace("llm_response", span_id=span, content="saved", usage={"total_tokens": 321})
                (tmp_path / "saved.txt").write_text("saved", encoding="utf-8")
            submit_background(work(), owner="example", run_id="run-1", source_trace_id=sink.trace_id)
        assert (tmp_path / "saved.txt").exists()
        data = rows(sink.path)
        assert [r["event_type"] for r in data][-1] == "session_end"
        assert any(r["event_type"] == "llm_response" and r["usage"]["total_tokens"] == 321 for r in data)
        result = next(r for r in data if r["event_type"] == "background_settled")["tasks"][0]
        assert result["status"] == "completed"
        assert result["usage_complete"] and result["usage"]["total_tokens"] == 321
    asyncio.run(run())


def test_online_background_appends_to_session_after_foreground_closes(tmp_path):
    async def run():
        gate = asyncio.Event()
        session = TraceSession(session_id="online", sink=ChatTraceLogger("online", log_dir=str(tmp_path)))
        async with session as sink:
            async def work():
                span = emit_trace("llm_request", model="fake", messages=[])
                await gate.wait()
                emit_trace("llm_response", span_id=span, content="saved", usage={"total_tokens": 123})
            task = submit_background(work(), owner="example", run_id="online-run", source_trace_id=sink.trace_id)
            await asyncio.sleep(0)
        assert not task.done()
        gate.set()
        await task
        foreground = rows(sink.path)
        record = session.background_registry.records[0]
        background = rows(record.metadata["trace_path"])
        assert Path(record.metadata["trace_path"]).parent == Path(sink.path).parent
        assert record.metadata["trace_path"] == sink.path
        assert record.metadata["trace_id"] == sink.trace_id
        assert list(tmp_path.glob("trace_*.jsonl")) == [Path(sink.path)]
        assert any(r["event_type"] == "llm_response" for r in foreground)
        assert all(r["session_id"] == "online" and r["trace_id"] == sink.trace_id for r in background)
        assert sum(r["event_type"] == "session_start" for r in background) == 1
        assert sum(r["event_type"] == "session_end" for r in background) == 1
        response = next(r for r in background if r["event_type"] == "llm_response")
        assert response["background_task_id"] == record.task_id
        assert response["background_owner"] == "example"
        result = next(r for r in background if r["event_type"] == "background_result")
        assert result["source_trace_id"] == sink.trace_id
        assert result["run_id"] == "online-run"
        assert result["usage_complete"] and result["usage"]["total_tokens"] == 123
        assert background[-1]["event_type"] == "background_result"
    asyncio.run(run())


def test_evaluation_timeout_cancels_and_drains_before_releasing_resources(tmp_path):
    async def run():
        released = asyncio.Event()
        session = TraceSession(session_id="cancel", sink=ChatTraceLogger("cancel", log_dir=str(tmp_path)),
                               background_timeout_seconds=0.01)
        async with session as sink:
            async def work():
                emit_trace("llm_request", model="fake", messages=[])
                try:
                    await asyncio.Event().wait()
                finally:
                    released.set()
            task = submit_background(work(), owner="example")
        assert released.is_set() and task.cancelled()
        result = next(r for r in rows(sink.path) if r["event_type"] == "background_settled")["tasks"][0]
        assert result["status"] == "cancelled"
        assert result["usage_complete"] is False
    asyncio.run(run())


def test_eval_result_and_snapshot_include_delayed_background_work(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app.agent_base.agents.react_agent import ReActProgress
    from extensions.evals.models import EvalCase
    from extensions.evals.runner import EvalRunner

    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "input.txt").write_text("input", encoding="utf-8")
    monkeypatch.setattr("extensions.evals.runner.evaluation_root", lambda: tmp_path / "artifacts")

    async def factory(workspace, case):
        class Agent:
            llm = SimpleNamespace(model="fake")

            async def arun_stream(self, prompt):
                async def work():
                    span = emit_trace("llm_request", model="fake", messages=[])
                    await asyncio.sleep(0.02)
                    emit_trace("llm_response", span_id=span, content="saved", usage={"total_tokens": 456})
                    (workspace / "background.txt").write_text("saved", encoding="utf-8")
                submit_background(work(), owner="example", run_id="eval-source")
                yield ReActProgress(step=1, thought="done", is_final=True, final_answer="done")
        return Agent()

    case = EvalCase(id="delayed-background", prompt="inspect", fixture=str(fixture),
                    checkers=[{"type": "file_exists", "path": "input.txt"}])
    result = asyncio.run(EvalRunner(tmp_path / "results.jsonl", trace_dir=tmp_path / "traces").run_case(case, factory))

    assert result.passed
    assert result.total_tokens == 456
    assert result.metadata["background_total_tokens_known"] == 456
    assert result.metadata["background_usage_complete"]
    assert result.metadata["background_tasks"][0]["status"] == "completed"
    assert (Path(result.workspace) / "background.txt").read_text(encoding="utf-8") == "saved"


def test_waiting_owner_cancellation_still_drains_background(tmp_path):
    async def run():
        started, cleanup_started, allow_cleanup = asyncio.Event(), asyncio.Event(), asyncio.Event()
        session = TraceSession(session_id="owner-cancel", sink=ChatTraceLogger("owner-cancel", log_dir=str(tmp_path)),
                               background_timeout_seconds=1)

        async def owner():
            async with session:
                async def work():
                    started.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        cleanup_started.set()
                        await allow_cleanup.wait()
                submit_background(work(), owner="example")
        task = asyncio.create_task(owner())
        await started.wait()
        task.cancel()
        await cleanup_started.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        allow_cleanup.set()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert session.background_registry.records[0].status == "cancelled"
        assert rows(session.tracer.path)[-1]["event_type"] == "session_end"
    asyncio.run(run())


def test_background_starts_after_close_and_keeps_original_run(tmp_path):
    async def run():
        session = TraceSession(session_id="late", sink=ChatTraceLogger("late", log_dir=str(tmp_path)))
        with session as sink:
            sink.set_run_id("original")
            async def work():
                span = emit_trace("llm_request", model="fake", messages=[])
                emit_trace("llm_response", span_id=span, content="archived", usage={"total_tokens": 7})
            task = submit_background(work(), owner="memory_archive", run_id="original", source_trace_id=sink.trace_id)
            sink.set_run_id("next-run")
        # The task has not even begun until after the foreground writer closes.
        await task
        data = rows(sink.path)
        background = [r for r in data if r.get("background_task_id")]
        assert all(r["run_id"] == "original" for r in background)
        assert any(r["event_type"] == "llm_request" for r in background)
        assert any(r["event_type"] == "llm_response" for r in background)
        assert data[-1]["event_type"] == "background_result"
        assert sink.run_id == "next-run"
        assert len(list(tmp_path.glob("trace_*.jsonl"))) == 1
    asyncio.run(run())


def test_background_without_session_keeps_standalone_recorder(tmp_path, monkeypatch):
    from app.agent_base.adapters.tracing import _ResilientTraceProvider
    from extensions.trace.chat_trace import JsonlTraceProvider
    monkeypatch.setattr("app.runtime.trace_session.load_trace", lambda: _ResilientTraceProvider(JsonlTraceProvider(str(tmp_path))))
    async def run():
        async def work():
            span = emit_trace("llm_request", model="fake", messages=[])
            emit_trace("llm_response", span_id=span, content="saved")
        await submit_background(work(), owner="standalone")
    asyncio.run(run())
    files = list(tmp_path.glob("trace_background_*.jsonl"))
    assert len(files) == 1
    assert rows(files[0])[-1]["event_type"] == "session_end"


def test_directly_bound_chat_sink_keeps_background_in_original_session(tmp_path, monkeypatch):
    from app.agent_base.adapters.tracing import _ResilientTraceProvider
    from app.agent_base.core.observability import set_current_trace_sink, reset_current_trace_sink
    from extensions.trace.chat_trace import JsonlTraceProvider
    provider = _ResilientTraceProvider(JsonlTraceProvider(str(tmp_path)))
    monkeypatch.setattr("app.runtime.trace_session.load_trace", lambda: provider)

    async def run():
        # Match online chat: direct sink binding, no background registry.
        from app.agent_base.host_api.tracing import TraceSessionRequest
        sink = provider.create(TraceSessionRequest(session_id="online-direct"))
        sink.start()
        sink.set_run_id("first-run")
        token = set_current_trace_sink(sink)
        async def work():
            span = emit_trace("llm_request", model="fake", messages=[])
            emit_trace("llm_response", span_id=span, content="archived", usage={"total_tokens": 13})
        try:
            first = submit_background(work(), owner="memory_archive", run_id="first-run", source_trace_id=sink.trace_id)
            sink.set_run_id("second-run")
            second = submit_background(work(), owner="memory_archive", run_id="second-run", source_trace_id=sink.trace_id)
        finally:
            reset_current_trace_sink(token)
            sink.close()
        # Both tasks begin after the originating connection's sink is unbound.
        await asyncio.gather(first, second)
        data = rows(sink.path)
        assert list(tmp_path.glob("trace_*.jsonl")) == [Path(sink.path)]
        assert all(row["session_id"] == "online-direct" for row in data)
        assert all(row["trace_id"] == sink.trace_id for row in data)
        results = [row for row in data if row["event_type"] == "background_result"]
        assert {row["run_id"] for row in results} == {"first-run", "second-run"}
        assert len({row["background_task_id"] for row in results}) == 2
        assert all(row["usage"]["total_tokens"] == 13 and row["usage_complete"] for row in results)
        assert sum(row["event_type"] == "session_start" for row in data) == 1
        assert sum(row["event_type"] == "session_end" for row in data) == 1
    asyncio.run(run())
