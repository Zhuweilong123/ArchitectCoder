"""Shared run admission and review completion rules for chat entry points."""

import logging

from app.runtime.agent_runtime import SessionBusyError
from app.services.run_state import RunConflict, RunStatus, run_status_for_completion
from app.agent_base.execution_summary import build_task_execution_summary

logger = logging.getLogger(__name__)


class RunLifecycle:
    def __init__(self, store, sessions):
        self.store = store
        self.sessions = sessions

    def start(self, *, session_id: str, owner: str, metadata: dict,
              idempotency_key: str = ""):
        if not self.sessions.try_claim_run(session_id, owner):
            raise SessionBusyError("This session is already running on another connection")
        try:
            run = self.store.create(
                kind="agent_chat", session_id=session_id, metadata=metadata,
                idempotency_key=idempotency_key,
            )
            if run.status != RunStatus.QUEUED.value:
                raise RunConflict("This request has already been accepted")
            return self.store.claim(run.run_id, owner)
        except BaseException:
            self.sessions.release_run(session_id, owner)
            raise

    def resolve_review(self, *, run_id: str, owner: str, accepted: bool):
        record = self.store.get(run_id)
        if record is None:
            raise RunConflict("The reviewed run no longer exists")
        checkpoint = dict(record.metadata.get("checkpoint") or {})
        status = checkpoint.pop("post_review_status", "partial") if accepted else "partial"
        checkpoint.update(status=status, review_status="accepted" if accepted else "rejected")
        checkpoint["task_summary"] = build_task_execution_summary(checkpoint.get("tool_calls", []), checkpoint, status)
        if isinstance(checkpoint.get("outcome"), dict):
            checkpoint["outcome"] = {
                **checkpoint["outcome"], "status": status,
                "stop_reason": checkpoint.get("stop_reason") or "model_answer"
                if accepted else "review_rejected",
            }
        # Persist before publishing completion or changing the Agent's latest snapshot.
        self.store.transition(
            run_id, run_status_for_completion(status),
            expected={RunStatus.WAITING_APPROVAL}, owner_id=owner,
            metadata_patch={"checkpoint": checkpoint},
        )
        if checkpoint.get("task_id"):
            try:
                from app.agent_base.tools.task_system import finalize_task_execution
                finalize_task_execution(
                    scope=record.session_id or checkpoint.get("project_file") or "default",
                    task_id=checkpoint["task_id"], run_id=run_id, checkpoint=checkpoint,
                )
            except Exception:
                logger.warning("Could not update task projection after review for %s", run_id, exc_info=True)
        return checkpoint

    def pause_review(self, *, run_id: str, owner: str):
        record = self.store.get(run_id)
        if record is None:
            raise RunConflict("The reviewed run no longer exists")
        checkpoint = dict(record.metadata.get("checkpoint") or {})
        checkpoint.update(status="paused", resume_available=True,
                          stop_reason="review transport disconnected; send continue to resume")
        self.store.transition(
            run_id, RunStatus.PAUSED, expected={RunStatus.WAITING_APPROVAL},
            owner_id=owner, metadata_patch={"checkpoint": checkpoint},
        )
        return checkpoint
