"""Tests for the provider-neutral agent runtime lifecycle."""

import pytest

from app.runtime.agent_runtime import AgentRuntime, SessionBusyError


class _Trace:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_runtime_reuses_session_and_updates_project_file():
    runtime = AgentRuntime()

    first = runtime.get_or_create("session-1", "first.umlproj")
    second = runtime.get_or_create("session-1", "second.umlproj")

    assert first is second
    assert second.project_file == "second.umlproj"
    assert runtime.active_count() == 1


def test_run_lease_is_exclusive_and_releases_on_exit():
    runtime = AgentRuntime()
    session = runtime.get_or_create("session-1")

    with session.run_lease("owner-a"):
        assert session.run_owner == "owner-a"
        with pytest.raises(SessionBusyError):
            with session.run_lease("owner-b"):
                pass

    assert session.run_owner is None
    assert session.try_claim_run("owner-b") is True
    session.release_run("owner-b")


def test_expiry_finalizes_trace_resource():
    runtime = AgentRuntime(session_ttl_seconds=1)
    session = runtime.get_or_create("session-1")
    trace = _Trace()
    session.trace_log = trace
    session.last_active = 10.0

    assert runtime.cleanup_expired(now=12.0) == 1
    assert trace.closed is True
    assert runtime.get("session-1") is None


def test_active_run_is_not_expired():
    runtime = AgentRuntime(session_ttl_seconds=1)
    session = runtime.get_or_create("session-1")
    session.last_active = 10.0
    assert session.try_claim_run("owner-a") is True

    assert runtime.cleanup_expired(now=12.0) == 0
    assert runtime.get("session-1") is session

    session.release_run("owner-a")
