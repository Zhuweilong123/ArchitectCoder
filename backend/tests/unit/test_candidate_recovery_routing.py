from app.services.chat_session import (
    _is_resume_request,
    _latest_resumable_run,
    _resume_prompt,
)


def test_contract_recovery_is_an_explicit_resumable_path(monkeypatch):
    assert _is_resume_request("修复设计契约并继续")

    class Record:
        run_id = "run-blocked"
        status = "partial"
        metadata = {
            "checkpoint": {
                "run_id": "run-blocked",
                "request_summary": "implement source change",
                "candidate_artifact": {
                    "artifact_id": "run-blocked",
                    "artifact_path": "C:/runtime/data/candidate_artifacts/run-blocked/manifest.json",
                },
            }
        }

    class Store:
        def list(self, *, limit, session_id):
            assert limit == 50
            assert session_id == "session-1"
            return [Record()]

    monkeypatch.setattr("app.services.chat_session.get_run_store", lambda: Store())
    record, checkpoint = _latest_resumable_run("session-1")
    assert record.run_id == "run-blocked"
    prompt = _resume_prompt(checkpoint)
    assert "design-contract recovery" in prompt
    assert "submit_uml_review" in prompt
    assert "previous source/test candidate" in prompt
