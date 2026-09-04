import json

from app.services.audit_log import AuditLogger
from extensions.trace.chat_trace import ChatTraceLogger


def test_audit_log_is_durable_and_redacts_sensitive_values(tmp_path):
    logger = AuditLogger(tmp_path / "audit.jsonl")

    logger.record(
        "tool_call", run_id="run-1", authorization="Bearer abc123",
        details={"api_key": "sk-secret-value", "value": "safe"},
    )
    events = logger.list(run_id="run-1")

    assert len(events) == 1
    assert events[0]["authorization"] == "[REDACTED]"
    assert events[0]["details"]["api_key"] == "[REDACTED]"
    assert events[0]["details"]["value"] == "safe"


def test_trace_events_include_active_run_id(tmp_path, monkeypatch):
    monkeypatch.setattr("extensions.trace.chat_trace._chat_log_dir", lambda: str(tmp_path))
    trace = ChatTraceLogger("session-1")
    trace.set_run_id("run-1")
    trace.start()
    trace.agent_step(step=1, actions=["read_file"])
    trace.close()

    rows = [
        json.loads(line)
        for line in (tmp_path / "trace_session-1.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert rows[0]["run_id"] == "run-1"
    assert rows[1]["run_id"] == "run-1"
