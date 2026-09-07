"""WebSocket transport adapter for the agent chat session."""

import logging

from fastapi import APIRouter, WebSocket

from app.core.auth import require_ws_auth
from app.services.chat_session import ChatSessionCoordinator

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/agent", tags=["agent-chat"])


@router.websocket("/ws/chat")
async def agent_chat_ws(websocket: WebSocket):
    """Authenticate the WebSocket and delegate session orchestration."""
    await websocket.accept()
    if not await require_ws_auth(websocket):
        return
    logger.info("[AgentChat] WebSocket connected")
    await ChatSessionCoordinator(websocket).run()
