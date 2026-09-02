"""Simple API authentication dependency.

When internal_api_token is set in config, the frontend must send
Authorization: Bearer <token> with every request.  If the token is
left empty (default) auth is skipped — suitable for local dev.

Usage as router-level dependency:
    app.include_router(..., dependencies=[Depends(require_auth)])
"""

import hmac
import logging
from fastapi import HTTPException, Request, Depends, WebSocket

from app.core.config import get_settings

logger = logging.getLogger(__name__)


async def require_auth(request: Request):
    """Enforce token auth when internal_api_token is configured."""
    settings = get_settings()

    # Auth is only active when the operator sets a token
    if not settings.internal_api_token:
        return

    # Extract token from Authorization header
    auth_header = request.headers.get("Authorization", "")
    token = ""
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]

    if not token:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    if not hmac.compare_digest(token, settings.internal_api_token):
        logger.warning(
            "Invalid API token from %s (path=%s)",
            request.client.host if request.client else "unknown",
            request.url.path,
        )
        raise HTTPException(status_code=403, detail="Invalid API token")


async def require_ws_auth(websocket: WebSocket) -> bool:
    """校验 WebSocket 的 Bearer token。

    WebSocket 不能直接复用 Request 依赖；这里保持与 HTTP 鉴权相同的
    fail-closed 语义，并在握手后立即以 1008 关闭未授权连接。
    """
    settings = get_settings()
    if not settings.internal_api_token:
        return True

    auth_header = websocket.headers.get("authorization", "")
    token = auth_header[7:] if auth_header.startswith("Bearer ") else ""
    if not token:
        await websocket.close(code=1008, reason="Missing Authorization header")
        return False
    if not hmac.compare_digest(token, settings.internal_api_token):
        logger.warning("Invalid WebSocket API token")
        await websocket.close(code=1008, reason="Invalid API token")
        return False
    return True
