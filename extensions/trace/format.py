"""Shared JSONL trace format and storage-location primitives.

This module deliberately contains no provider, reader, or Agent-runtime imports.
Keeping these primitives independent prevents the JSONL writer and reader from
forming a package cycle.
"""

from __future__ import annotations

import os
import hashlib
import re
from datetime import datetime
from pathlib import Path


EVT_SESSION_START = "session_start"
EVT_SESSION_END = "session_end"
EVT_USER_MESSAGE = "user_message"
EVT_LLM_REQUEST = "llm_request"
EVT_LLM_RESPONSE = "llm_response"
EVT_AGENT_STEP = "agent_step"
EVT_TOOL_CALL = "tool_call"
EVT_TOOL_RESULT = "tool_result"
EVT_REVIEW_REQUEST = "review_request"
EVT_REVIEW_RESPONSE = "review_response"
EVT_DONE = "done"
EVT_ERROR = "error"
EVT_KG_INJECT = "kg_inject"
EVT_CONTEXT_COMPACTED = "context_compacted"
EVT_TASK_SUMMARY = "task_summary"


def chat_log_dir() -> str:
    """Return the default chat-trace directory under the runtime root."""
    from backend.config import get_settings

    settings = get_settings()
    return os.path.normpath(os.path.abspath(
        os.path.join(settings.runtime_dir, "chat_log"),
    ))


_DATE_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def safe_trace_session_id(session_id: str) -> str:
    """Use the same safe filename key for writing and reading a session."""
    safe = "".join(c for c in session_id if c.isalnum() or c in "-_.")
    if not safe:
        return hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:16]
    if len(safe) > 120:
        return safe[:100] + "_" + hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:16]
    return safe


def is_trace_date_dir(name: str) -> bool:
    return bool(_DATE_DIR.fullmatch(name))


def iter_chat_trace_paths(root: str | Path):
    """Yield both historical flat files and one-level date-folder files."""
    base = Path(root)
    if not base.is_dir():
        return
    yield from base.glob("trace_*.jsonl")
    for day in base.iterdir():
        if day.is_dir() and is_trace_date_dir(day.name):
            yield from day.glob("trace_*.jsonl")


def find_chat_trace_path(root: str | Path, session_id: str) -> Path | None:
    filename = f"trace_{safe_trace_session_id(session_id)}.jsonl"
    matches = [path for path in iter_chat_trace_paths(root) if path.name == filename]
    return max(matches, key=lambda path: path.stat().st_mtime, default=None)


def new_chat_trace_path(root: str | Path, session_id: str) -> Path:
    day = datetime.now().astimezone().strftime("%Y-%m-%d")
    return Path(root) / day / f"trace_{safe_trace_session_id(session_id)}.jsonl"
