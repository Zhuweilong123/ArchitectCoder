import asyncio
import json

import pytest

from app.agent_base.host_api.errors import AgentAwaitingReview
from app.agent_base.tools.review import ReviewManager, SubmitUmlReviewTool
from app.agent_base.tools.registry import ToolRegistry
from app.services.candidate_artifact import CandidateArtifactStore, CandidateArtifactError
from app.services.change_set import ChangeSet
from app.services.pending_review import suspend_review, restore_pending_reviews, review_event, apply_review_candidate
from app.services.run_state import RunStore, RunStatus
from app.services.run_lifecycle import RunLifecycle


def pending(tmp_path):
    path = tmp_path / "design.umlproj"
    before = json.dumps({"name": "approved", "revision": 1, "diagrams": []})
    after = json.dumps({"name": "candidate", "revision": 1, "diagrams": []})
    path.write_text(before, encoding="utf-8")
    changes = ChangeSet()
    changes.record(str(path), True, before, after)
    path.write_text(after, encoding="utf-8")
    store = RunStore(tmp_path / "runs.db")
    run = store.claim(store.create(kind="agent_chat", session_id="s").run_id, "owner")
    manager = ReviewManager(session_id="s")
    request = manager.submit("uml_diff", title="Review", metadata={
        "original_diagrams": [{"name": "before"}], "diagrams": [{"name": "after"}]})
    artifacts = CandidateArtifactStore(root=tmp_path / "candidates")
    checkpoint = suspend_review(request, {"run_id": run.run_id, "project_file": str(path)}, changes, artifacts)
    store.transition(run.run_id, RunStatus.WAITING_APPROVAL, owner_id="owner", metadata_patch={"checkpoint": checkpoint})
    return path, store, run, checkpoint, artifacts


def test_pending_candidate_is_durable_and_does_not_change_workspace(tmp_path):
    path, store, run, checkpoint, artifacts = pending(tmp_path)
    assert json.loads(path.read_text())["name"] == "approved"
    assert checkpoint["status"] == "waiting_approval"
    manager, run_map = ReviewManager(session_id="s"), {}
    restored = restore_pending_reviews(store, "s", manager, run_map)
    request = restored[0][2]
    assert request.token == checkpoint["pending_review"]["token"]
    assert review_event(request)["original_diagrams"] == [{"name": "before"}]
    assert run_map[request.id] == run.run_id
    restore_pending_reviews(store, "s", manager, run_map)
    assert len(manager.get_pending()) == 1
    apply_review_candidate(checkpoint, artifacts)
    assert json.loads(path.read_text())["name"] == "candidate"


@pytest.mark.parametrize("accepted", [True, False])
def test_review_resolution_applies_only_accepted_candidate(tmp_path, monkeypatch, accepted):
    path, store, run, checkpoint, artifacts = pending(tmp_path)
    monkeypatch.setattr("app.services.pending_review.CandidateArtifactStore", lambda: artifacts)
    result = RunLifecycle(store, None).resolve_review(run_id=run.run_id, owner="owner", accepted=accepted)
    assert json.loads(path.read_text())["name"] == ("candidate" if accepted else "approved")
    assert result["resume_available"]
    assert result["status"] == "partial"
    assert "pending_review" not in result


def test_conflicting_workspace_keeps_review_pending(tmp_path, monkeypatch):
    path, store, run, checkpoint, artifacts = pending(tmp_path)
    path.write_text("user edit", encoding="utf-8")
    monkeypatch.setattr("app.services.pending_review.CandidateArtifactStore", lambda: artifacts)
    with pytest.raises(CandidateArtifactError):
        RunLifecycle(store, None).resolve_review(run_id=run.run_id, owner="owner", accepted=True)
    assert path.read_text() == "user edit"
    assert store.get(run.run_id).status == "waiting_approval"


def test_stop_preserves_unresolved_review(tmp_path):
    path, store, run, checkpoint, artifacts = pending(tmp_path)
    RunLifecycle(store, None).pause_review(run_id=run.run_id, owner="owner")
    assert store.get(run.run_id).status == "waiting_approval"
    assert json.loads(path.read_text())["name"] == "approved"


def test_durable_tool_yields_to_coordinator_without_waiting_for_timeout():
    manager = ReviewManager()
    manager.persist_reviews = True
    registry = ToolRegistry()
    events = []
    from types import SimpleNamespace
    registry.register_tool(SubmitUmlReviewTool(manager, timeout=300, progress=SimpleNamespace(emit=events.append)))
    with pytest.raises(AgentAwaitingReview):
        asyncio.run(registry.aexecute_tool_result_with_params("submit_uml_review", {
            "diagrams_json": json.dumps([{"name": "A"}])}))
    assert manager.has_pending()
    assert events == []  # Only the host may publish after persisting the snapshot.


def test_timeout_does_not_cancel_human_decision():
    manager = ReviewManager()
    tool = SubmitUmlReviewTool(manager, timeout=0.001)
    async def run():
        with pytest.raises(AgentAwaitingReview):
            await tool._execute({"diagrams_json": json.dumps([{"name": "A"}])})
        assert manager.has_pending()
        assert manager.resolve(0, '{"decision":"accept"}')
    asyncio.run(run())
