import json
from types import SimpleNamespace

import pytest
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


@pytest.mark.asyncio
async def test_chat_followup_resumes_checkpoint_and_restores_candidate_only_after_design_accept(
    tmp_path, monkeypatch,
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

    websocket = _WebSocket([
        json.dumps({"type": "chat", "message": "先评估旧候选是否符合新设计，不符合就重写"}, ensure_ascii=False),
        json.dumps({"type": "review_response", "review_id": review.id, "decision": "accept"}),
    ])
    await chat_session.ChatSessionCoordinator(websocket).run()

    assert start_args["resume_record"] is record
    assert start_args["resume_checkpoint"] is checkpoint
    assert start_args["raw_user_message"] == "先评估旧候选是否符合新设计，不符合就重写"
    assert "Latest user message (complete; follow this instruction)" in start_args["message"]
    assert "先评估旧候选是否符合新设计，不符合就重写" in start_args["message"]
    assert restored == [candidate_ref]
    assert source.read_text(encoding="utf-8") == candidate_source
    assert trace_log.review_events[0]["candidate_recovery"] is True


async def _async_noop(*_args, **_kwargs):
    return None
