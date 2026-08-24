"""Trace API — 浏览 / 读取会话 trace (JSONL)。

只读端点，供前端 TraceViewer 使用：
  GET /api/trace/list          列出所有 trace 文件（元数据）
  GET /api/trace/{session_id}  读取单个 session 的完整事件流
"""

import logging

from fastapi import APIRouter, HTTPException

from app.services.trace_reader import list_traces, read_trace, reconstruct_history
from app.services.replay import replay_agent_session, ReplayExhausted

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/trace", tags=["trace"])


@router.get("/list")
async def list_trace_endpoint():
    """列出所有 trace 文件，按修改时间倒序。"""
    return {"traces": list_traces()}


@router.get("/{session_id}")
async def read_trace_endpoint(session_id: str):
    """读取单个 session 的完整事件流。"""
    result = read_trace(session_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Trace not found")
    return result


@router.get("/{session_id}/history")
async def trace_history_endpoint(session_id: str):
    """返回该 session 的对话历史（结论级：user + assistant），供会话恢复。"""
    history = reconstruct_history(session_id)
    if history is None:
        raise HTTPException(status_code=404, detail="Trace not found")
    return {"session_id": session_id, "history": history}


@router.post("/{session_id}/replay")
async def replay_trace_endpoint(session_id: str, mode: str = "mock", turn: int | None = None):
    """离线回放该 session。mode: mock（全模拟，默认）/ rerun（真调 LLM）。

    turn: 单步执行——只重放到第 N 轮（1-based，累积语义）；省略则重放全部轮次。
    """
    try:
        return await replay_agent_session(session_id, mode=mode, until_turn=turn)
    except ValueError as e:
        msg = str(e)
        status = 404 if "不存在" in msg else 400
        raise HTTPException(status_code=status, detail=msg)
    except ReplayExhausted as e:
        raise HTTPException(status_code=422, detail=str(e))
