"""Plugin-owned HTTP API for Trace browsing and replay."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.trace.tracing import TraceReplayExhausted, load_trace

router = APIRouter(prefix="/api/trace", tags=["trace"])


@router.get("/list")
async def list_trace_endpoint():
    return {"traces": load_trace().query().list_traces()}


@router.get("/{session_id}")
async def read_trace_endpoint(session_id: str):
    result = load_trace().query().read_trace(session_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Trace not found")
    return result


@router.get("/{session_id}/history")
async def trace_history_endpoint(session_id: str):
    history = load_trace().query().reconstruct_history(session_id)
    if history is None:
        raise HTTPException(status_code=404, detail="Trace not found")
    return {"session_id": session_id, "history": history}


@router.get("/{session_id}/summary")
async def trace_summary_endpoint(session_id: str):
    summary = load_trace().query().summarize_trace(session_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="Trace not found")
    return summary


@router.post("/{session_id}/replay")
async def replay_trace_endpoint(
    session_id: str,
    mode: str = "mock",
    turn: int | None = None,
    tool_policy: str = "readonly",
):
    try:
        return await load_trace().replay(
            session_id, mode=mode, until_turn=turn, tool_policy=tool_policy,
        )
    except ValueError as exc:
        message = str(exc)
        status = 404 if "不存在" in message or "not found" in message.lower() else 400
        raise HTTPException(status_code=status, detail=message) from exc
    except TraceReplayExhausted as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


__all__ = ["router"]