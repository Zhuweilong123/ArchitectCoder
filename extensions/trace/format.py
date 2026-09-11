"""Shared JSONL trace format and storage-location primitives.

This module deliberately contains no provider, reader, or Agent-runtime imports.
Keeping these primitives independent prevents the JSONL writer and reader from
forming a package cycle.
"""

from __future__ import annotations

import os


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
    """Return the default chat-trace directory next to the UML workspace."""
    from backend.config import get_settings

    settings = get_settings()
    return os.path.normpath(os.path.abspath(
        os.path.join(os.path.dirname(settings.uml_dir), "chat_log"),
    ))
