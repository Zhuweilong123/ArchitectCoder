import asyncio
import json
from pathlib import Path

from app.agent_base.core.background_tasks import submit_background
from app.trace.tracing import TraceSession, emit_trace
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


def test_online_background_has_independent_trace_after_foreground_closes(tmp_path):
    async def run():
        gate = asyncio.Event()
        session = TraceSession(session_id="online", sink=ChatTraceLogger("online", log_dir=str(tmp_path)),
                               trace_dir=str(tmp_path))
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
        assert not any(r["event_type"] == "llm_response" for r in foreground)
        assert any(r["event_type"] == "llm_response" for r in background)
        result = next(r for r in background if r["event_type"] == "background_result")
        assert result["source_trace_id"] == sink.trace_id
        assert result["run_id"] == "online-run"
        assert result["usage_complete"] and result["usage"]["total_tokens"] == 123
        assert background[-1]["event_type"] == "session_end"
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
