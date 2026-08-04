"""optimize_uml_v2 API — 专用全局优化端点

与 v1 (agent_chat_ws.py) 不同，此端点绕过了 ReActAgent 和工具调用链，
直接处理 UML 优化请求。

SSE (stream):
    POST /api/optimize_v2/stream
    Body: {"project_file": "...", "instructions": "..."}
    Response: text/event-stream
        data: class:{"id":"c1","diagram_name":"Domain Model",...}
        data: relation:{"source":"c1","target":"c2",...}
        data: DONE
        data: design_updated:{"diagrams":[],"consistency_report":[]}

REST:
    POST /api/optimize_v2/optimize
    Body: {"project_file": "...", "instructions": "..."}
    Returns: {"diagrams": [...], "consistency_report": [...]}

Trace 生命周期由 optimize_v2 / optimize_v2_stream 内部管理，
端点层无需配置。
"""

import asyncio
import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.services.uml_optimizer_v2 import (
    optimize_v2,
    optimize_v2_stream,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/optimize_v2", tags=["optimize_v2"])


class OptimizeV2Request(BaseModel):
    project_file: str = ""
    instructions: str = ""


class OptimizeV2Response(BaseModel):
    diagrams: list = []
    consistency_report: list = []


# ── SSE 流式端点 ──────────────────────────────────────

@router.post("/stream")
async def optimize_v2_stream_endpoint(body: OptimizeV2Request, request: Request):
    """全局优化 SSE 流

    返回 text/event-stream:
        data: <type>:<json>      — 设计元素（class, relation, lifeline, ...）
        data: DONE               — 流式元素结束
        data: design_updated:... — 最终验证+布局后的完整结果

    客户端通过 ReadableStream 读取，支持 abort 中断。
    """
    project_file = body.project_file
    instructions = body.instructions

    if not project_file:
        return StreamingResponse(
            _sse_error("Missing project_file"),
            media_type="text/event-stream",
        )

    logger.info(
        "[optimize_v2 SSE] project=%s, instructions=%s",
        project_file, instructions[:80],
    )

    async def _event_stream():
        try:
            async for line in optimize_v2_stream(
                project_file=project_file,
                instructions=instructions,
            ):
                if await request.is_disconnected():
                    logger.info("[optimize_v2 SSE] Client disconnected")
                    break
                yield line
        except asyncio.CancelledError:
            logger.info("[optimize_v2 SSE] Cancelled")
        except Exception as e:
            logger.exception("[optimize_v2 SSE] Error")
            yield _sse_data(json.dumps({"event": "error", "message": str(e)}))

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 禁用 nginx 缓冲
        },
    )


# ── REST 端点 ──────────────────────────────────────────

@router.post("/optimize", response_model=OptimizeV2Response)
async def optimize_v2_endpoint(body: OptimizeV2Request):
    """非流式全局优化 REST API

    适用于 Pipeline 或第三方调用。
    """
    result = await optimize_v2(
        project_file=body.project_file,
        instructions=body.instructions,
    )
    return OptimizeV2Response(
        diagrams=result.get("diagrams", []),
        consistency_report=result.get("consistency_report", []),
    )


# ── Helpers ────────────────────────────────────────────

def _sse_data(payload: str) -> str:
    """Format a data: line for SSE."""
    return f"data: {payload}\n\n"

def _sse_error(message: str) -> str:
    """Format an SSE error data line."""
    return f"data: error:{{\"message\": \"{message}\"}}\n\n"
