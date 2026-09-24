from app.services.chat_session import (
    _latest_resumable_run,
    _resume_prompt,
)


def test_contract_recovery_uses_next_message_without_a_command(monkeypatch):
    class Record:
        run_id = "run-blocked"
        status = "partial"
        metadata = {
            "checkpoint": {
                "run_id": "run-blocked",
                "request_summary": "implement source change",
                "stop_reason": "contract_check_failed",
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
    prompt = _resume_prompt(checkpoint, "修复设计契约并继续")
    assert "design-contract gate" in prompt
    assert "submit_uml_review" in prompt
    assert "framework restores the source/test candidate" in prompt
    assert prompt.endswith("修复设计契约并继续")


def test_contract_recovery_keeps_new_direction_and_full_message():
    instruction = "保留当前设计，重写代码使其符合设计。" + "不要恢复旧候选。" * 300
    prompt = _resume_prompt({
        "request_summary": "change source",
        "candidate_artifact": {"artifact_id": "run-blocked"},
    }, instruction)
    assert prompt.endswith(instruction)
    assert "do not restore the old candidate" in prompt


def test_completed_newer_run_does_not_resurrect_older_candidate():
    class Record:
        def __init__(self, run_id, status, checkpoint):
            self.run_id = run_id
            self.status = status
            self.metadata = {"checkpoint": checkpoint}

    blocked = Record("blocked", "partial", {
        "stop_reason": "contract_check_failed",
        "candidate_artifact": {"artifact_id": "blocked"},
    })
    finished = Record("finished", "succeeded", {"status": "completed"})

    class Store:
        def list(self, *, limit, session_id):
            return [finished, blocked]

    assert _latest_resumable_run("session-1", store_factory=Store) is None


def test_consumed_parent_is_skipped_but_latest_paused_child_resumes():
    class Record:
        def __init__(self, run_id, status, checkpoint):
            self.run_id = run_id
            self.status = status
            self.metadata = {"checkpoint": checkpoint}

    parent = Record("parent", "partial", {
        "stop_reason": "contract_check_failed",
        "candidate_artifact": {"artifact_id": "parent"},
        "resume_consumed": True,
    })
    child = Record("child", "paused", {"resume_available": True})

    class Store:
        def list(self, *, limit, session_id):
            return [parent, child]

    result = _latest_resumable_run("session-1", store_factory=Store)
    assert result is not None and result[0].run_id == "child"


def test_latest_run_without_checkpoint_does_not_resume_stale_task():
    class Record:
        def __init__(self, status, checkpoint):
            self.status = status
            self.metadata = {"checkpoint": checkpoint}

    class Store:
        def list(self, *, limit, session_id):
            return [
                Record("running", None),
                Record("partial", {
                    "stop_reason": "contract_check_failed",
                    "candidate_artifact": {"artifact_id": "stale"},
                }),
            ]

    assert _latest_resumable_run("session-1", store_factory=Store) is None


def test_partial_unrelated_failure_does_not_revive_old_candidate():
    class Record:
        status = "partial"
        metadata = {"checkpoint": {
            "stop_reason": "verification_failed",
            "candidate_artifact": {"artifact_id": "old-candidate"},
        }}

    class Store:
        def list(self, *, limit, session_id):
            return [Record()]

    assert _latest_resumable_run("session-1", store_factory=Store) is None
