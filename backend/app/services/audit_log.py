"""Durable, redacted audit events for harness operations."""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_SENSITIVE_KEYS = {"api_key", "authorization", "password", "secret", "token"}
_SECRET_VALUE = re.compile(r"(?i)(bearer\s+|sk-[a-z0-9_-]{8,})[a-z0-9._~+/=-]*")


def _audit_path() -> Path:
    from backend.config import get_settings

    return Path(get_settings().uml_dir).resolve().parent / "data" / "audit.jsonl"


def _redact(value: Any, key: str = "") -> Any:
    if key.lower() in _SENSITIVE_KEYS:
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return _SECRET_VALUE.sub("[REDACTED]", value)
    return value


class AuditLogger:
    """Append-only JSONL audit writer with a small query helper."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else _audit_path()
        self._lock = threading.Lock()

    def record(
        self,
        event_type: str,
        *,
        run_id: str = "",
        session_id: str = "",
        **payload: Any,
    ) -> dict[str, Any]:
        event = {
            "schema_version": 1,
            "event_id": uuid.uuid4().hex,
            "event_type": event_type,
            "run_id": run_id,
            "session_id": session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **_redact(payload),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event, ensure_ascii=False, default=str)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        return event

    def list(self, *, run_id: str = "", limit: int = 100) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        limit = max(1, min(limit, 500))
        rows: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and (not run_id or event.get("run_id") == run_id):
                rows.append(event)
        return rows[-limit:]


_default_logger: AuditLogger | None = None
_default_lock = threading.Lock()


def get_audit_logger() -> AuditLogger:
    global _default_logger
    if _default_logger is None:
        with _default_lock:
            if _default_logger is None:
                _default_logger = AuditLogger()
    return _default_logger
