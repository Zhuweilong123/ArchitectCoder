"""Durable human decisions, independent of the model and WebSocket lifetime."""

from copy import deepcopy
from pathlib import Path

from app.services.candidate_artifact import CandidateArtifactStore
from app.services.change_set import ChangeSet


def review_event(request):
    return {"event": "uml_review", "review_id": request.id, "title": request.title,
            **deepcopy(request.metadata), "auto": False}


def suspend_review(request, checkpoint, change_set, artifact_store=None):
    """Snapshot a candidate and restore the approved workspace before waiting."""
    reference = None
    if change_set is not None and change_set.has_changes:
        reference = (artifact_store or CandidateArtifactStore()).capture(change_set, checkpoint["run_id"])
        if reference is None:
            raise RuntimeError("Could not persist the pending review candidate")
        change_set.rollback()
    return {**checkpoint, "status": "waiting_approval", "review_status": "pending",
            "stop_reason": "waiting_for_review", "resume_available": False,
            "post_review_status": "partial", "review_baseline": request.metadata.get("original_diagrams"),
            "pending_review": {**request.to_dict(), "id": request.id},
            "review_candidate": reference}


def restore_pending_reviews(store, session_id, manager, run_map):
    """Rebuild live requests from durable records after session/server recreation."""
    restored = []
    for record in reversed(store.list(session_id=session_id, limit=100)):
        checkpoint = record.metadata.get("checkpoint") or {}
        saved = checkpoint.get("pending_review")
        if record.status != "waiting_approval" or not saved:
            continue
        request = manager.restore(saved)
        run_map[request.id] = record.run_id
        restored.append((record, checkpoint, request))
    return restored


def apply_review_candidate(checkpoint, artifact_store=None):
    """Accept only the recorded candidate; reject stale workspace snapshots."""
    reference = checkpoint.get("review_candidate")
    if not reference:
        return
    changes = ChangeSet()
    project = checkpoint.get("project_file") or ""
    roots = [checkpoint.get("workspace_root") or str(Path(project).parent)]
    try:
        (artifact_store or CandidateArtifactStore()).restore(reference, changes, allowed_roots=roots)
        changes.commit()
    except BaseException:
        if changes.has_changes:
            changes.rollback()
        raise
