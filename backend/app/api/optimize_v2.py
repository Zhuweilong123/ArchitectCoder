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
"""

import asyncio
import json
import logging
import os

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.services.uml_optimizer_v2 import (
    optimize_v2,
    optimize_v2_stream,
)
from app.services.chat_trace import ChatTraceLogger, set_trace_hook
from app.services.agent_chat_ws import _TRACE_BRIDGE

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/optimize_v2", tags=["optimize_v2"])


# ── trace hook bridge（与 agent_chat_ws.py 共用 _TRACE_BRIDGE）──

def _trace_bridge(kind: str, *args, **kwargs):
    """全局 LLM trace hook — 转发到当前 optimize_v2 会话的 ChatTraceLogger"""
    from app.services.chat_trace import current_trace_spans

    tracer = _TRACE_BRIDGE.get("tracer")
    if tracer is None:
        return None
    spans = current_trace_spans()
    span_path = "/".join(spans) if spans else ""
    try:
        if kind == "llm_request":
            return tracer.llm_request(
                provider=kwargs.get("provider", "unknown"),
                model=kwargs.get("model", ""),
                messages=kwargs.get("messages", []),
                temperature=kwargs.get("temperature"),
                max_tokens=kwargs.get("max_tokens"),
                tools=kwargs.get("tools"),
                tool_choice=kwargs.get("tool_choice"),
                span_path=span_path,
            )
        elif kind == "llm_response":
            tracer.llm_response(
                span_id=kwargs.get("span_id", ""),
                content=kwargs.get("content", ""),
                tool_calls=kwargs.get("tool_calls"),
                usage=kwargs.get("usage"),
                error=kwargs.get("error", ""),
                duration_ms=kwargs.get("duration_ms", 0.0),
                span_path=span_path,
            )
            return None
    except Exception:
        logger.exception("[Trace] Bridge failed for kind=%s", kind)
    return None


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

    # ── Trace ──
    trace_log: ChatTraceLogger | None = None
    pid = os.path.splitext(os.path.basename(project_file))[0] if project_file else "no_project"
    from datetime import datetime
    sid = f"{pid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    trace_log = ChatTraceLogger(session_id=sid)
    trace_log.start(
        user_message=instructions,
        project_file=project_file,
        source_dir="",
        test_dir="",
        env_snapshot={"stream_mode": True, "version": "v2"},
    )
    set_trace_hook(_trace_bridge)
    _TRACE_BRIDGE["tracer"] = trace_log

    logger.info(
        "[optimize_v2 SSE] project=%s, instructions=%s",
        project_file, instructions[:80],
    )

    async def _event_stream():
        nonlocal trace_log
        try:
            async for line in optimize_v2_stream(
                project_file=project_file,
                instructions=instructions,
            ):
                # 检查客户端是否已断开
                if await request.is_disconnected():
                    logger.info("[optimize_v2 SSE] Client disconnected")
                    break
                yield line

            if trace_log:
                trace_log.done(answer="SSE stream completed")
        except asyncio.CancelledError:
            logger.info("[optimize_v2 SSE] Cancelled")
        except Exception as e:
            logger.exception("[optimize_v2 SSE] Error")
            yield _sse_data(json.dumps({"event": "error", "message": str(e)}))
        finally:
            set_trace_hook(None)
            _TRACE_BRIDGE["tracer"] = None
            if trace_log:
                trace_log.close()
                trace_log = None

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
