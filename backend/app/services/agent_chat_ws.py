"""WebSocket transport adapter for the agent chat session."""

import logging

from fastapi import APIRouter, WebSocket

from app.core.auth import require_ws_auth
from app.services.chat_session import (
    ChatSessionCoordinator,
    DevPromptBuilder,
    ReActAgent,
    BaseAgentsLLM,
    ProgressRelay,
    _archive_task_to_memory,
    _build_task_execution_summary,
    _checkpoint_answer,
    _consume_task_exception,
    _create_dev_agent,
    _enabled_tools_context,
    _history_structure,
    _is_resume_request,
    _latest_resumable_run as _latest_resumable_run_impl,
    _latest_persisted_checkpoint as _latest_persisted_checkpoint_impl,
    _record_audit,
    _resume_prompt,
    _resume_supplement,
    _set_trace_bridge,
    _should_archive_task_memory,
    _terminal_checkpoint_status,
    _todo_progress_state,
    _trace_hook_bridge,
    _ws_send,
    get_run_store,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/agent", tags=["agent-chat"])


def _latest_persisted_checkpoint(session_id: str) -> dict:
    """Compatibility wrapper that keeps legacy monkeypatch points working."""
    return _latest_persisted_checkpoint_impl(session_id, store_factory=get_run_store)


def _latest_resumable_run(session_id: str):
    """Compatibility wrapper that keeps legacy monkeypatch points working."""
    return _latest_resumable_run_impl(session_id, store_factory=get_run_store)


@router.websocket("/ws/chat")
async def agent_chat_ws(websocket: WebSocket):
    """Authenticate the WebSocket and delegate session orchestration."""
    await websocket.accept()
    if not await require_ws_auth(websocket):
        return
    logger.info("[AgentChat] WebSocket connected")
    await ChatSessionCoordinator(websocket).run()
