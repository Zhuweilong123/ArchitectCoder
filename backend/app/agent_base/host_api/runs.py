"""Public run identities, lease records and state errors."""
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    BUDGET_EXCEEDED = "budget_exceeded"
    CANCELED = "canceled"
    ORPHANED = "orphaned"


class RunStateError(RuntimeError):
    """Base error for invalid run state operations."""


class RunNotFound(RunStateError):
    pass


class RunConflict(RunStateError):
    pass


class InvalidRunTransition(RunStateError):
    pass


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    kind: str
    status: str
    session_id: str
    idempotency_key: str
    owner_id: str
    attempt: int
    version: int
    created_at: str
    updated_at: str
    started_at: str
    finished_at: str
    heartbeat_at: float | None
    lease_expires_at: float | None
    error: str
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RunStorePort(Protocol):
    db_path: Path
    def create(self, **kwargs) -> RunRecord: ...
    def get(self, run_id: str) -> RunRecord | None: ...
    def claim(self, run_id: str, owner_id: str, **kwargs) -> RunRecord: ...
    def heartbeat(self, run_id: str, owner_id: str, **kwargs) -> RunRecord: ...
    def transition(self, run_id: str, status: RunStatus, **kwargs) -> RunRecord: ...
