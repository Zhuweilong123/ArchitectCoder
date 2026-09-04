"""Provider-neutral lifecycle management for agent sessions.

The runtime owns session retention, run leases and trace resource cleanup.
Transport adapters (WebSocket, HTTP, evals, CLI) should use this boundary
instead of maintaining their own session registry.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

from app.trace.tracing import TraceSink


DEFAULT_SESSION_TTL_SECONDS = 2 * 3600


class SessionBusyError(RuntimeError):
    """Raised when a second owner tries to run the same session."""


@dataclass
class AgentSession:
    """Reusable state for one logical agent conversation."""

    session_id: str
    project_file: str = ""
    agent: Any = None
    review_mgr: Any = None
    progress: Any = None
    prompt_builder: Any = None
    trace_log: TraceSink | None = None
    last_active: float = field(default_factory=time.time)
    run_owner: str | None = None
    _run_lock: threading.RLock = field(
        default_factory=threading.RLock, init=False, repr=False,
    )

    def touch(self) -> None:
        self.last_active = time.time()

    def try_claim_run(self, owner: str) -> bool:
        """Acquire the single-writer lease for this session."""
        with self._run_lock:
            if self.run_owner is not None and self.run_owner != owner:
                return False
            self.run_owner = owner
            self.touch()
            return True

    def release_run(self, owner: str) -> None:
        """Release a lease only when called by its current owner."""
        with self._run_lock:
            if self.run_owner == owner:
                self.run_owner = None
                self.touch()

    @contextmanager
    def run_lease(self, owner: str) -> Iterator["AgentSession"]:
        """Yield the session while holding a run lease."""
        if not self.try_claim_run(owner):
            raise SessionBusyError(
                f"session {self.session_id!r} is already running"
            )
        try:
            yield self
        finally:
            self.release_run(owner)


class AgentRuntime:
    """Own the lifecycle of reusable agent sessions.

    A runtime instance is intentionally injectable. The module-level
    ``runtime`` is only the compatibility default used by the current HTTP
    transport; tests and future workers can create isolated instances.
    """

    def __init__(self, session_ttl_seconds: float = DEFAULT_SESSION_TTL_SECONDS):
        self.session_ttl_seconds = max(0.0, float(session_ttl_seconds))
        self._sessions: dict[str, AgentSession] = {}
        self._lock = threading.RLock()

    def get(self, session_id: str) -> Optional[AgentSession]:
        expired: AgentSession | None = None
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            if (
                session.run_owner is None
                and time.time() - session.last_active > self.session_ttl_seconds
            ):
                expired = self._sessions.pop(session_id)
            else:
                return session
        self._finalize(expired)
        return None

    def get_or_create(
        self, session_id: str, project_file: str = "",
    ) -> AgentSession:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                session = AgentSession(
                    session_id=session_id,
                    project_file=project_file,
                )
                self._sessions[session_id] = session
            elif project_file:
                session.project_file = project_file
            session.touch()
            return session

    def finalize(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.pop(session_id, None)
        self._finalize(session)

    def cleanup_expired(self, now: float | None = None) -> int:
        now = now if now is not None else time.time()
        expired: list[AgentSession] = []
        with self._lock:
            for session_id, session in list(self._sessions.items()):
                if (
                    session.run_owner is None
                    and now - session.last_active > self.session_ttl_seconds
                ):
                    expired.append(self._sessions.pop(session_id))
        for session in expired:
            self._finalize(session)
        return len(expired)

    def active_count(self) -> int:
        with self._lock:
            return len(self._sessions)

    def try_claim_run(self, session_id: str, owner: str) -> bool:
        session = self.get(session_id)
        return session.try_claim_run(owner) if session is not None else False

    def release_run(self, session_id: str, owner: str) -> None:
        session = self.get(session_id)
        if session is not None:
            session.release_run(owner)

    @staticmethod
    def _finalize(session: AgentSession | None) -> None:
        if session is None or session.trace_log is None:
            return
        try:
            session.trace_log.close()
        except Exception:
            # Resource cleanup must not prevent the registry from releasing
            # an expired session.
            pass


runtime = AgentRuntime()


# Compatibility functions for callers that used the old module-level API.
def get(session_id: str) -> Optional[AgentSession]:
    return runtime.get(session_id)


def get_or_create(session_id: str, project_file: str = "") -> AgentSession:
    return runtime.get_or_create(session_id, project_file)


def finalize(session_id: str) -> None:
    runtime.finalize(session_id)


def cleanup_expired(now: float | None = None) -> int:
    return runtime.cleanup_expired(now)


def active_count() -> int:
    return runtime.active_count()


def try_claim_run(session_id: str, owner: str) -> bool:
    return runtime.try_claim_run(session_id, owner)


def release_run(session_id: str, owner: str) -> None:
    runtime.release_run(session_id, owner)


__all__ = [
    "AgentRuntime",
    "AgentSession",
    "SessionBusyError",
    "active_count",
    "cleanup_expired",
    "finalize",
    "get",
    "get_or_create",
    "release_run",
    "runtime",
    "try_claim_run",
]
