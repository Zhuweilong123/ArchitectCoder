"""Simple API authentication dependency.

When internal_api_token is set in config, the frontend must send
Authorization: Bearer <token> with every request.  If the token is
left empty (default) auth is skipped — suitable for local dev.

Usage as router-level dependency:
    app.include_router(..., dependencies=[Depends(require_auth)])
"""

import base64
import binascii
import hmac
import logging
from fastapi import HTTPException, Request, WebSocket

from backend.config import get_settings

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

    if not hmac.compare_digest(token.encode("utf-8"), settings.internal_api_token.encode("utf-8")):
        logger.warning(
            "Invalid API token from %s (path=%s)",
            request.client.host if request.client else "unknown",
            request.url.path,
        )
        raise HTTPException(status_code=403, detail="Invalid API token")


async def require_ws_auth(websocket: WebSocket) -> bool:
    """校验 WebSocket 的来源及 Bearer/subprotocol 凭证。

    WebSocket 不能直接复用 Request 依赖；这里保持与 HTTP 鉴权相同的
    fail-closed 语义，并在握手后立即以 1008 关闭未授权连接。
    """
    settings = get_settings()
    origin = websocket.headers.get("origin")
    if origin and origin not in settings.cors_origins:
        await websocket.close(code=1008, reason="Origin is not allowed")
        return False
    if not settings.internal_api_token:
        return True

    auth_header = websocket.headers.get("authorization", "")
    token = auth_header[7:] if auth_header.startswith("Bearer ") else ""
    # Browsers cannot set Authorization on WebSocket upgrades. Offer the
    # credential as a base64url subprotocol; never put it in URLs/access logs
    # or echo that subprotocol in the server's handshake response.
    if not token:
        credentials = [p.strip()[5:] for p in
                       websocket.headers.get("sec-websocket-protocol", "").split(",")
                       if p.strip().startswith("auth.")]
        if len(credentials) == 1:
            try:
                encoded = credentials[0]
                token = base64.b64decode(encoded + "=" * (-len(encoded) % 4),
                                        altchars=b"-_", validate=True).decode("utf-8")
            except (ValueError, UnicodeError, binascii.Error):
                token = ""
    if not token:
        await websocket.close(code=1008, reason="Missing WebSocket credential")
        return False
    if not hmac.compare_digest(token.encode("utf-8"), settings.internal_api_token.encode("utf-8")):
        logger.warning("Invalid WebSocket API token")
        await websocket.close(code=1008, reason="Invalid API token")
        return False
    return True
