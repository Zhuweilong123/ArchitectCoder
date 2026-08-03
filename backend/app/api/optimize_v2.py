"""optimize_uml_v2 API — 专用全局优化端点

与 v1 (agent_chat_ws.py) 不同，此端点绕过了 ReActAgent 和工具调用链，
直接处理 UML 优化请求。

WebSocket:
    ws://host/api/optimize_v2/ws
    接受: {"project_file": "...", "instructions": "...", "stream_mode": bool}
    发送: "design_element" 事件（流式模式）, "design_updated" 事件（结果）

REST:
    POST /api/optimize_v2/optimize
    Body: {"project_file": "...", "instructions": "..."}
    Returns: {"diagrams": [...], "consistency_report": [...]}
"""

import asyncio
import json
import logging
import os

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from app.services.uml_optimizer_v2 import (
    optimize_v2,
    optimize_v2_stream,
    _get_stream_last_result,
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


# ── WebSocket 端点 ──────────────────────────────────────

@router.websocket("/ws")
async def optimize_v2_ws(websocket: WebSocket):
    """全局优化 WebSocket — 流式 + 非流式统一入口

    接收 JSON: {"project_file": "...", "instructions": "...", "stream_mode": true/false}

    非流式 (stream_mode=false):
        完成后发送 design_updated，然后关闭连接。

    流式 (stream_mode=true):
        逐元素发送 design_element，最后发送设计完成后的 layout
        diagram_update 元素，完成后发送 design_updated。
    """
    await websocket.accept()
    logger.info("[optimize_v2 WS] Connected")

    trace_log: ChatTraceLogger | None = None

    def _open_trace(pf: str, instr: str, sm: bool):
        """创建会话 trace，注册全局 hook"""
        nonlocal trace_log
        pid = os.path.splitext(os.path.basename(pf))[0] if pf else "no_project"
        from datetime import datetime
        sid = f"{pid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        trace_log = ChatTraceLogger(session_id=sid)
        trace_log.start(
            user_message=instr,
            project_file=pf,
            source_dir="",
            test_dir="",
            env_snapshot={"stream_mode": sm, "version": "v2"},
        )
        set_trace_hook(_trace_bridge)
        _TRACE_BRIDGE["tracer"] = trace_log
        logger.info("[optimize_v2 WS] Trace started: %s", sid)

    def _close_trace():
        """关闭 trace，注销 hook"""
        nonlocal trace_log
        if trace_log:
            trace_log.close()
            trace_log = None
        set_trace_hook(None)
        _TRACE_BRIDGE["tracer"] = None

    try:
        raw = await websocket.receive_text()
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            await _ws_send(websocket, {"event": "error", "message": "Invalid JSON"})
            await websocket.close()
            return

        project_file = msg.get("project_file", "")
        instructions = msg.get("instructions", "")
        stream_mode = msg.get("stream_mode", False)

        if not project_file:
            await _ws_send(websocket, {
                "event": "error",
                "message": "Missing project_file",
            })
            await websocket.close()
            return

        # ── 启动 trace ──
        _open_trace(project_file, instructions, stream_mode)

        logger.info(
            "[optimize_v2 WS] project=%s, stream=%s, instructions=%s",
            project_file, stream_mode, instructions[:80],
        )

        if stream_mode:
            # ── 流式模式 ──
            async for _elem_type, _elem_json in optimize_v2_stream(
                project_file=project_file,
                instructions=instructions,
                progress=lambda ev: asyncio.create_task(
                    _ws_send(websocket, ev)
                ),
            ):
                pass  # 事件由 progress 回调发送

            # 发送最终结果
            result = _get_stream_last_result()
            if result and result.get("diagrams"):
                await _ws_send(websocket, {
                    "event": "design_updated",
                    "diagrams": result["diagrams"],
                    "consistency_report": result.get("consistency_report", []),
                    "review": True,
                })
            if trace_log:
                trace_log.done(
                    answer=json.dumps(result or {}, ensure_ascii=False)[:2000],
                )
        else:
            # ── 非流式模式 ──
            result = await optimize_v2(
                project_file=project_file,
                instructions=instructions,
            )

            if result.get("diagrams"):
                await _ws_send(websocket, {
                    "event": "design_updated",
                    "diagrams": result["diagrams"],
                    "consistency_report": result.get("consistency_report", []),
                    "review": True,
                })
            else:
                await _ws_send(websocket, {
                    "event": "error",
                    "message": result.get("changes_summary", "Optimization produced no results"),
                    "details": result.get("consistency_report", []),
                })
            if trace_log:
                trace_log.done(
                    answer=json.dumps(result, ensure_ascii=False)[:2000],
                )

    except WebSocketDisconnect:
        logger.info("[optimize_v2 WS] Client disconnected")
        if trace_log:
            trace_log.error(event_type="websocket", message="Client disconnected")
    except Exception as e:
        logger.exception("[optimize_v2 WS] Error")
        if trace_log:
            trace_log.error(event_type="server", message=f"Server error: {e}")
        try:
            await _ws_send(websocket, {"event": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        _close_trace()
        try:
            await websocket.close()
        except Exception:
            pass


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

async def _ws_send(websocket: WebSocket, data: dict) -> bool:
    """发送 JSON 到 WebSocket，忽略断开连接的异常。"""
    try:
        await websocket.send_json(data)
        return True
    except Exception:
        return False
