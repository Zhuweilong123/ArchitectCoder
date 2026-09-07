import pytest

from app.runtime.agent_runtime import AgentRuntime, SessionBusyError
from app.services.run_lifecycle import RunLifecycle
from app.services.run_state import RunConflict, RunStatus, RunStore


@pytest.fixture
def lifecycle(tmp_path):
    sessions = AgentRuntime()
    sessions.get_or_create("session")
    return RunLifecycle(RunStore(tmp_path / "runs.db"), sessions)


def start(lifecycle, owner="a", **metadata):
    return lifecycle.start(session_id="session", owner=owner, metadata=metadata)


@pytest.mark.parametrize("metadata", [{}, {"parent_run_id": "reviewed"}, {"resume_of": "paused"}])
def test_all_request_kinds_use_session_lease(lifecycle, metadata):
    run = start(lifecycle, **metadata)
    with pytest.raises(SessionBusyError):
        start(lifecycle, owner="b", parent_run_id="other")
    assert lifecycle.sessions.get("session").run_owner == "a"
    assert lifecycle.store.get(run.run_id).status == "running"


def test_claim_failure_releases_session(lifecycle, monkeypatch):
    def fail(*args):
        raise RunConflict("claim failed")
    monkeypatch.setattr(lifecycle.store, "claim", fail)
    with pytest.raises(RunConflict):
        start(lifecycle)
    assert lifecycle.sessions.get("session").run_owner is None


def awaiting(lifecycle, status="completed"):
    run = start(lifecycle)
    lifecycle.store.transition(
        run.run_id, RunStatus.WAITING_APPROVAL, owner_id="a",
        metadata_patch={"checkpoint": {
            "run_id": run.run_id, "status": "waiting_approval",
            "post_review_status": status, "changed_files": ["old.py"],
            "project_file": "design.umlproj", "review_baseline": [{"id": "before"}],
        }},
    )
    lifecycle.sessions.release_run("session", "a")
    return run


@pytest.mark.parametrize("status, expected", [
    ("completed", "succeeded"), ("partial", "partial"),
    ("timed_out", "timed_out"), ("budget_exceeded", "budget_exceeded"),
])
def test_review_completion_uses_its_own_run(lifecycle, status, expected):
    reviewed = awaiting(lifecycle, status)
    newer = start(lifecycle, owner="b", message="another task")
    checkpoint = lifecycle.resolve_review(run_id=reviewed.run_id, owner="a", accepted=True)
    assert checkpoint["changed_files"] == ["old.py"]
    assert checkpoint["status"] == status
    assert lifecycle.store.get(reviewed.run_id).status == expected
    assert lifecycle.store.get(newer.run_id).status == "running"
    assert lifecycle.sessions.get("session").run_owner == "b"


def test_rejected_review_can_start_revision_with_lease(lifecycle):
    reviewed = awaiting(lifecycle)
    checkpoint = lifecycle.resolve_review(run_id=reviewed.run_id, owner="a", accepted=False)
    assert checkpoint["status"] == "partial"
    revised = start(lifecycle, parent_run_id=reviewed.run_id)
    assert revised.metadata["parent_run_id"] == reviewed.run_id
    with pytest.raises(SessionBusyError):
        start(lifecycle, owner="b")


def test_failed_review_persistence_does_not_change_checkpoint(lifecycle):
    reviewed = awaiting(lifecycle)
    with pytest.raises(RunConflict):
        lifecycle.resolve_review(run_id=reviewed.run_id, owner="wrong", accepted=True)
    assert lifecycle.store.get(reviewed.run_id).metadata["checkpoint"]["status"] == "waiting_approval"


def test_disconnected_review_retains_resume_context(lifecycle):
    reviewed = awaiting(lifecycle)
    checkpoint = lifecycle.pause_review(run_id=reviewed.run_id, owner="a")
    assert checkpoint["resume_available"]
    assert checkpoint["project_file"] == "design.umlproj"
    assert checkpoint["review_baseline"] == [{"id": "before"}]
    assert lifecycle.store.get(reviewed.run_id).status == "paused"
