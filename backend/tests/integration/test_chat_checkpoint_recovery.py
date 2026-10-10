import asyncio
import json
from pathlib import Path

import pytest
from types import SimpleNamespace

from fastapi import WebSocketDisconnect

from app.agent_base.tools.review import ReviewManager
from app.services import chat_session


class _TraceLog:
    trace_id = "trace-recovery-test"

    def __init__(self):
        self.review_events = []

    def review_response(self, **kwargs):
        self.review_events.append(kwargs)


class _WebSocket:
    query_params = {"session_id": "checkpoint-recovery-test"}

    def __init__(self, messages):
        self.messages = iter(messages)
        self.sent = []

    async def receive_text(self):
        try:
            return next(self.messages)
        except StopIteration:
            raise WebSocketDisconnect()

    async def send_json(self, payload):
        self.sent.append(payload)


@pytest.mark.parametrize("action", ["accept", "reject", "repush", "stop", "cancel"])
@pytest.mark.parametrize("agent_lost", [False, True])
def test_durable_review_restores_across_session_recreation(tmp_path, monkeypatch, action, agent_lost):
    from app.services.run_state import RunStore, RunStatus
    from app.services.candidate_artifact import CandidateArtifactStore
    from app.services.change_set import ChangeSet
    from app.services.pending_review import suspend_review
    project = tmp_path / "candidate.txt"
    project.write_text("before", encoding="utf-8")
    changes = ChangeSet()
    changes.record(str(project), True, "before", "after")
    project.write_text("after", encoding="utf-8")
    store = RunStore(tmp_path / "runs.db")
    run = store.claim(store.create(kind="agent_chat", session_id="checkpoint-recovery-test").run_id, "old-owner")
    manager = ReviewManager(session_id="checkpoint-recovery-test")
    request = manager.submit("uml_diff", title="Original candidate", metadata={
        "diagrams": [{"name": "after"}], "original_diagrams": [{"name": "before"}]})
    artifacts = CandidateArtifactStore(root=tmp_path / "artifacts")
    checkpoint = suspend_review(request, {"run_id": run.run_id, "project_file": str(project),
        "workspace_root": str(tmp_path), "request_summary": "Original task"}, changes, artifacts)
    store.transition(run.run_id, RunStatus.WAITING_APPROVAL, owner_id="old-owner", metadata_patch={"checkpoint": checkpoint})
    starts = []
    agent = SimpleNamespace(llm=None, last_run_checkpoint={}, tool_registry=SimpleNamespace(get_tool=lambda name: None),
                            append_task_summary=lambda summary: None)
    recreated = ReviewManager(session_id="checkpoint-recovery-test")
    session = SimpleNamespace(agent=None if agent_lost else agent, review_mgr=recreated, progress=None, prompt_builder=None,
                              trace_log=_TraceLog(), run_owner=None, touch=lambda: None)
    monkeypatch.setattr(chat_session, "get_or_create", lambda _: session)
    monkeypatch.setattr(chat_session, "get_run_store", lambda: store)
    monkeypatch.setattr("app.services.pending_review.CandidateArtifactStore", lambda: artifacts)
    monkeypatch.setattr(chat_session, "_record_audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(chat_session.BaseAgentsLLM, "from_settings", lambda **kwargs: SimpleNamespace())
    async def create(*args, **kwargs):
        return agent, ReviewManager(session_id="checkpoint-recovery-test"), None
    monkeypatch.setattr(chat_session, "create_dev_agent", create)
    async def start(**kwargs):
        starts.append(kwargs)
    monkeypatch.setattr(chat_session, "_start_agent_chat_run", start)
    if action in {"accept", "reject"}:
        payload = {"type": "review_response", "review_id": request.id, "decision": action, "feedback": "comment"}
    elif action == "repush":
        payload = {"type": "chat", "message": "再给我推送一下审核"}
    elif action == "cancel":
        payload = {"type": "chat", "message": "取消审核"}
    else:
        payload = {"type": "stop"}
    websocket = _WebSocket([json.dumps(payload)])
    asyncio.run(chat_session.ChatSessionCoordinator(websocket).run())
    assert any(event.get("event") == "uml_review" and event["title"] == "Original candidate" for event in websocket.sent)
    assert project.read_text() == ("after" if action == "accept" else "before")
    assert len(starts) == (1 if action in {"accept", "reject"} else 0)
    if starts:
        assert starts[0]["parent_run_id"] == run.run_id
        assert starts[0]["resume_checkpoint"]["review_status"] == ("accepted" if action == "accept" else "rejected")
    else:
        assert store.get(run.run_id).status == ("canceled" if action == "cancel" else "waiting_approval")


@pytest.mark.parametrize("decision", ["accept", "reject"])
@pytest.mark.parametrize("wire_format", ["feedback", "legacy_comment", "json_response", "empty_feedback", "no_comment"])
def test_chat_followup_resumes_checkpoint_and_restores_candidate_only_after_design_accept(
    tmp_path, monkeypatch, decision, wire_format,
):
    source = tmp_path / "echo.py"
    source.write_text("class EchoSimulator: pass\n", encoding="utf-8")
    candidate_source = "class EchoSimulatorClass: pass\n"
    candidate_ref = {"artifact_id": "candidate-run-1"}
    restored = []

    review_mgr = ReviewManager(session_id="checkpoint-recovery-test")
    review = review_mgr.submit(
        "uml_diff",
        title="Review updated design",
        metadata={"candidate_recovery": candidate_ref},
    )

    class _Agent:
        llm = None
        last_run_checkpoint = {}

        def __init__(self):
            self.tool_registry = SimpleNamespace(get_tool=lambda _name: None)

    trace_log = _TraceLog()
    session = SimpleNamespace(
        agent=_Agent(), review_mgr=review_mgr, progress=None,
        prompt_builder=None, trace_log=trace_log, run_owner=None,
        touch=lambda: None,
    )

    class _LLM:
        @classmethod
        def from_settings(cls, **_kwargs):
            return cls()

    checkpoint = {
        "run_id": "run-blocked-1",
        "request_summary": "Rename EchoSimulator in source",
        "contract_enabled": True,
        "stop_reason": "contract_check_failed",
        "candidate_artifact": candidate_ref,
        "source_dir": str(tmp_path / "src"),
        "test_dir": str(tmp_path / "test"),
        "workspace_root": str(tmp_path),
        "design_dir": str(tmp_path / "design"),
    }
    record = SimpleNamespace(run_id="run-blocked-1", status="partial")
    start_args = {}

    async def fake_start_agent_chat_run(**kwargs):
        start_args.update(kwargs)
        # The candidate must remain rolled back while the new instruction is
        # being evaluated and before the pending design review is accepted.
        assert source.read_text(encoding="utf-8") == "class EchoSimulator: pass\n"

    def restore_candidate(reference):
        restored.append(reference)
        source.write_text(candidate_source, encoding="utf-8")

    review_mgr.candidate_restore_callback = restore_candidate
    monkeypatch.setattr(chat_session, "get_or_create", lambda _session_id: session)
    monkeypatch.setattr(chat_session, "_latest_resumable_run", lambda _session_id: (record, checkpoint))
    monkeypatch.setattr(
        chat_session, "_resolve_workspace_paths",
        lambda *_args, **_kwargs: (
            (
                str(tmp_path / "src"), str(tmp_path / "test"), "",
                str(tmp_path), str(tmp_path / "design"),
            ),
            "",
        ),
    )
    monkeypatch.setattr(chat_session, "_start_agent_chat_run", fake_start_agent_chat_run)
    monkeypatch.setattr(chat_session, "_compress_session_context", _async_noop)
    monkeypatch.setattr(chat_session.BaseAgentsLLM, "from_settings", _LLM.from_settings)
    monkeypatch.setattr(chat_session.agent_runtime, "release_run", lambda *_args: None)

    comment = "泊车时序图已审核，补充超时分支。\n保留现有调用顺序。"
    expected_feedback = "" if wire_format in {"empty_feedback", "no_comment"} else comment
    review_payload = {"type": "review_response", "review_id": review.id}
    if wire_format == "json_response":
        review_payload["response"] = json.dumps({"decision": decision, "feedback": comment}, ensure_ascii=False)
    else:
        review_payload["decision"] = decision
        if wire_format != "no_comment":
            review_payload["response"] = comment
        if wire_format == "feedback":
            review_payload["feedback"] = comment
        elif wire_format == "empty_feedback":
            review_payload["feedback"] = ""

    websocket = _WebSocket([
        json.dumps({"type": "chat", "message": "先评估旧候选是否符合新设计，不符合就重写"}, ensure_ascii=False),
        json.dumps(review_payload, ensure_ascii=False),
    ])
    async def scenario():
        future = review.future
        await chat_session.ChatSessionCoordinator(websocket).run()
        return await future

    tool_response = asyncio.run(scenario())

    assert start_args["resume_record"] is record
    assert start_args["resume_checkpoint"] is checkpoint
    assert start_args["raw_user_message"] == "先评估旧候选是否符合新设计，不符合就重写"
    assert "Latest user message (complete; follow this instruction)" in start_args["message"]
    assert "先评估旧候选是否符合新设计，不符合就重写" in start_args["message"]
    assert restored == ([candidate_ref] if decision == "accept" else [])
    assert source.read_text(encoding="utf-8") == (candidate_source if decision == "accept" else "class EchoSimulator: pass\n")
    assert trace_log.review_events[0]["candidate_recovery"] is True
    event = trace_log.review_events[0]
    assert event["decision"] == decision
    assert event["feedback"] == expected_feedback
    expected_result = {"decision": decision, "feedback": expected_feedback}
    assert json.loads(event["response"]) == expected_result
    assert json.loads(tool_response) == expected_result


async def _async_noop(*_args, **_kwargs):
    return None


def test_disconnect_reconnect_preserves_run_and_stop_reaches_original_task(tmp_path, monkeypatch):
    trace = _TraceLog()
    trace.error = lambda **_kwargs: None
    agent = SimpleNamespace(llm=None, last_run_checkpoint={},
                            tool_registry=SimpleNamespace(get_tool=lambda _name: None))
    session = SimpleNamespace(agent=agent, review_mgr=None, progress=None,
                              prompt_builder=None, trace_log=trace,
                              run_owner=None, touch=lambda: None)
    monkeypatch.setattr(chat_session, "get_or_create", lambda _id: session)
    monkeypatch.setattr(chat_session, "_latest_resumable_run", lambda _id: None)
    monkeypatch.setattr(chat_session, "_resolve_workspace_paths", lambda *_a, **_kw: (("", "", "", str(tmp_path), ""), ""))
    monkeypatch.setattr(chat_session, "_compress_session_context", _async_noop)
    monkeypatch.setattr(chat_session.BaseAgentsLLM, "from_settings", lambda **_kw: SimpleNamespace())
    starts, stopped = [], []

    async def scenario():
        ready = asyncio.Event()

        async def start(**kwargs):
            starts.append(kwargs)

            async def execute():
                ready.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    stopped.append(kwargs["stop_check"]())
                    raise

            return asyncio.create_task(execute())

        monkeypatch.setattr(chat_session, "_start_agent_chat_run", start)
        first = _WebSocket([json.dumps({"type": "chat", "message": "repair"})])
        await chat_session.ChatSessionCoordinator(first).run()
        await ready.wait()
        task = session.chat_connection.task
        assert not task.done()
        second = _WebSocket([
            json.dumps({"type": "chat", "message": "duplicate"}),
            json.dumps({"type": "stop"}),
        ])
        await chat_session.ChatSessionCoordinator(second).run()
        assert len(starts) == 1
        assert stopped == [True]
        assert task.cancelled()
        assert any(e.get("event") == "session_sync" and e["running"] for e in second.sent)
        assert any(e.get("event") == "stopped" for e in second.sent)

    asyncio.run(scenario())


def test_online_coordinator_background_stays_in_session_after_disconnect(tmp_path, monkeypatch):
    from app.agent_base.adapters.tracing import _ResilientTraceProvider
    from app.agent_base.core.background_tasks import submit_background
    from app.agent_base.core.observability import emit_trace
    from app.agent_base.host_api.tracing import TraceSessionRequest
    from extensions.trace.chat_trace import JsonlTraceProvider

    provider = _ResilientTraceProvider(JsonlTraceProvider(str(tmp_path)))
    sink = provider.create(TraceSessionRequest(session_id="checkpoint-recovery-test"))
    sink.start()
    agent = SimpleNamespace(llm=None, last_run_checkpoint={}, tool_registry=SimpleNamespace(get_tool=lambda _name: None))
    session = SimpleNamespace(agent=agent, review_mgr=None, progress=None, prompt_builder=None,
                              trace_log=sink, run_owner=None, touch=lambda: None)
    tasks = []
    monkeypatch.setattr(chat_session, "get_or_create", lambda _session_id: session)
    monkeypatch.setattr(chat_session, "_latest_resumable_run", lambda _session_id: None)
    monkeypatch.setattr(chat_session, "_resolve_workspace_paths", lambda *_a, **_kw: (("", "", "", str(tmp_path), ""), ""))
    monkeypatch.setattr(chat_session, "_compress_session_context", _async_noop)
    monkeypatch.setattr(chat_session.BaseAgentsLLM, "from_settings", lambda **_kw: SimpleNamespace())
    monkeypatch.setattr(chat_session.agent_runtime, "release_run", lambda *_a: None)
    monkeypatch.setattr("app.runtime.trace_session.load_trace", lambda: provider)

    async def scenario():
        gate = asyncio.Event()
        async def start(**kwargs):
            run_id = kwargs["message"]
            sink.set_run_id(run_id)
            async def archive():
                await gate.wait()
                span = emit_trace("llm_request", model="fake", messages=[])
                emit_trace("llm_response", span_id=span, content="saved", usage={"total_tokens": 5})
            tasks.append(submit_background(archive(), owner="memory_archive", run_id=run_id, source_trace_id=sink.trace_id))
        monkeypatch.setattr(chat_session, "_start_agent_chat_run", start)
        websocket = _WebSocket([json.dumps({"type": "chat", "message": run}) for run in ["first", "second"]])
        await chat_session.ChatSessionCoordinator(websocket).run()
        gate.set()
        await asyncio.gather(*tasks)
        sink.close()

    asyncio.run(scenario())
    files = list(tmp_path.rglob("trace_*.jsonl"))
    assert files == [Path(sink.path)]
    events = [json.loads(line) for line in Path(sink.path).read_text(encoding="utf-8").splitlines()]
    results = [event for event in events if event["event_type"] == "background_result"]
    assert {event["run_id"] for event in results} == {"first", "second"}
    assert all(event["session_id"] == "checkpoint-recovery-test" and event["trace_id"] == sink.trace_id for event in results)
    assert all(event["status"] == "completed" for event in results)
