import time

import pytest

from app.services.run_state import (
    InvalidRunTransition,
    RunConflict,
    RunStatus,
    RunStore,
)


def test_run_state_is_durable_and_idempotent(tmp_path):
    path = tmp_path / "runs.db"
    first = RunStore(path)
    created = first.create(
        kind="agent_chat",
        session_id="session-1",
        idempotency_key="message-1",
        metadata={"message": "inspect"},
    )
    duplicate = first.create(
        kind="agent_chat",
        session_id="session-1",
        idempotency_key="message-1",
        metadata={"message": "different"},
    )

    assert duplicate.run_id == created.run_id
    assert duplicate.metadata == {"message": "inspect"}
    assert RunStore(path).get(created.run_id) == created


def test_run_state_claim_heartbeat_and_terminal_transition(tmp_path):
    store = RunStore(tmp_path / "runs.db")
    run = store.create(kind="agent_chat")

    running = store.claim(run.run_id, "worker-1", lease_seconds=5)
    assert running.status == RunStatus.RUNNING.value
    assert running.attempt == 1
    assert running.owner_id == "worker-1"

    heartbeat = store.heartbeat(run.run_id, "worker-1", lease_seconds=5)
    assert heartbeat.version == running.version + 1

    finished = store.transition(
        run.run_id,
        RunStatus.SUCCEEDED,
        expected={RunStatus.RUNNING},
        owner_id="worker-1",
        metadata_patch={"answer": "ok"},
    )
    assert finished.status == RunStatus.SUCCEEDED.value
    assert finished.finished_at
    assert finished.owner_id == ""
    assert finished.metadata["answer"] == "ok"


def test_run_state_rejects_wrong_owner_and_invalid_transition(tmp_path):
    store = RunStore(tmp_path / "runs.db")
    run = store.create(kind="agent_chat")
    store.claim(run.run_id, "worker-1")

    with pytest.raises(RunConflict):
        store.heartbeat(run.run_id, "worker-2")
    with pytest.raises(InvalidRunTransition):
        store.transition(run.run_id, RunStatus.QUEUED)


def test_expired_run_requires_explicit_resume(tmp_path):
    store = RunStore(tmp_path / "runs.db")
    run = store.create(kind="agent_chat")
    running = store.claim(run.run_id, "worker-1", lease_seconds=1)

    orphaned = store.recover_expired(now=(running.lease_expires_at or time.time()) + 1)
    assert [item.run_id for item in orphaned] == [run.run_id]
    assert store.get(run.run_id).status == RunStatus.ORPHANED.value

    queued = store.resume(run.run_id)
    assert queued.status == RunStatus.QUEUED.value
    assert store.claim(run.run_id, "worker-2").attempt == 2
