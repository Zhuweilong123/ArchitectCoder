"""Pipeline API – manage and run the 6-stage automation pipeline."""

import asyncio
import hmac
import json
import logging
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect

from app.core.auth import require_auth
from app.core.config import get_settings
from app.models.uml import UmlDiagram
from app.models.pipeline import (
    ConfirmRequest, PipelineState,
)
from pydantic import BaseModel

from app.services.pipeline_service import (
    create_pipeline, get_pipeline, confirm_stage,
    run_pipeline, resume_pipeline, resume_with_instructions,
    stop_pipeline, _is_stopped, _clear_stop_flag,
    _update_stage, _save_pipeline_log,
    _save_case_review_file, _save_uml_review_record,
)
from app.models.pipeline import StageName, StageStatus

logger = logging.getLogger(__name__)


class CreatePipelineBody(BaseModel):
    diagram_id: str
    auto_confirm: bool = False
    diagram: UmlDiagram

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


@router.post("/create", dependencies=[Depends(require_auth)])
async def create_pipeline_endpoint(body: CreatePipelineBody):
    """Create a new pipeline for a diagram."""
    state = create_pipeline(body.diagram_id, body.diagram)
    return {"pipeline": state.model_dump()}


@router.get("/{pipeline_id}", dependencies=[Depends(require_auth)])
async def get_pipeline_endpoint(pipeline_id: str):
    """Get pipeline state."""
    state = get_pipeline(pipeline_id)
    if not state:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    return {"pipeline": state.model_dump()}


@router.post("/{pipeline_id}/confirm", dependencies=[Depends(require_auth)])
async def confirm_stage_endpoint(pipeline_id: str, req: ConfirmRequest):
    """Confirm or reject a pipeline stage."""
    state = confirm_stage(pipeline_id, req.stage, req.accepted, req.comment)
    return {"pipeline": state.model_dump()}


@router.post("/{pipeline_id}/resume", dependencies=[Depends(require_auth)])
async def resume_pipeline_endpoint(
    pipeline_id: str,
    diagram: UmlDiagram,
    language: str = "python",
):
    """Resume pipeline from dev_confirm stage (after user confirms)."""
    state = get_pipeline(pipeline_id)
    if not state:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    results = []
    async for event in resume_pipeline(pipeline_id, diagram, language, auto_confirm=True):
        results.append(event)
    return {"events": results, "pipeline": get_pipeline(pipeline_id).model_dump()}


@router.websocket("/ws/{pipeline_id}")
async def pipeline_websocket(ws: WebSocket, pipeline_id: str, token: str = ""):
    """WebSocket endpoint for real-time pipeline progress.

    Uses a background reader + asyncio.Queue so that stop messages are
    received even while the pipeline is actively executing (LLM calls, etc.).
    """
    settings = get_settings()
    if settings.internal_api_token:
        if not token or not hmac.compare_digest(token, settings.internal_api_token):
            await ws.close(code=4001, reason="Unauthorized")
            return

    await ws.accept()

    # ── Read initial handshake message ──────────────────
    data = await ws.receive_text()
    msg = json.loads(data)
    diagram = UmlDiagram(**msg.get("diagram", {}))
    language = msg.get("language", "python")
    auto_confirm = msg.get("auto_confirm", False)
    source_dir = msg.get("source_dir", "")
    test_dir = msg.get("test_dir", "")
    max_change_ratio = msg.get("max_change_ratio", 0)
    project_data = msg.get("project", {})
    all_diagrams = project_data.get("diagrams", [])
    seq_diagram = next((d for d in all_diagrams if d.get("diagram_type") == "sequence"), None)
    comp_diagram = next((d for d in all_diagrams if d.get("diagram_type") == "component"), None)

    class_diagram_data = next((d for d in all_diagrams if d.get("diagram_type", "class") == "class"), None)
    if class_diagram_data:
        diagram = UmlDiagram(**class_diagram_data)
        logger.info(f"[Pipeline] Using class diagram '{diagram.name}' ({len(diagram.classes)} classes) for pipeline")
    else:
        # No class diagram yet — use an empty one as placeholder; LLM will generate
        diagram = UmlDiagram(name=project_data.get("name", "Untitled"))

    state = get_pipeline(pipeline_id) or create_pipeline(pipeline_id, diagram)
    instructions = ""

    # ═══════════════════════════════════════════════════════════════
    # Background reader — always reading, feeds messages into a queue
    # ═══════════════════════════════════════════════════════════════
    msg_queue: asyncio.Queue = asyncio.Queue()

    async def background_reader():
        """Continuously read WebSocket messages and push into the queue.
        Runs as a background task so the main loop never blocks on reads."""
        try:
            while True:
                raw = await ws.receive_text()
                await msg_queue.put(json.loads(raw))
        except WebSocketDisconnect:
            await msg_queue.put(None)  # signal disconnect
        except Exception:
            await msg_queue.put(None)

    reader_task = asyncio.create_task(background_reader())

    # ═══════════════════════════════════════════════════════════════
    # Message helpers — read from the shared queue, not from ws
    # ═══════════════════════════════════════════════════════════════

    async def get_msg(timeout: float | None = None) -> dict | None:
        """Get next message from queue. Returns None on disconnect or timeout."""
        try:
            if timeout:
                return await asyncio.wait_for(msg_queue.get(), timeout=timeout)
            return await msg_queue.get()
        except asyncio.TimeoutError:
            return None

    def _is_stop_msg(m: dict | None) -> bool:
        return m is not None and m.get("action") == "stop"

    # ═══════════════════════════════════════════════════════════════
    # Wait helpers — block until specific action or stop
    # ═══════════════════════════════════════════════════════════════

    async def wait_for_instructions():
        """Wait for user to submit or skip optimization instructions."""
        nonlocal instructions
        while True:
            m = await get_msg()
            if m is None:
                return False
            if _is_stop_msg(m):
                stop_pipeline(pipeline_id)
                return False
            if m.get("action") == "submit_instructions":
                instructions = m.get("instructions", "")
                async for resume_event in resume_with_instructions(
                    pipeline_id, diagram, instructions, language,
                    source_dir=source_dir, test_dir=test_dir,
                    sequence_diagram=seq_diagram, component_diagram=comp_diagram,
                    all_diagrams=all_diagrams,
                    max_change_ratio=max_change_ratio,
                ):
                    if _is_stopped(pipeline_id):
                        await ws.send_json({"event": "stopped", "pipeline_id": pipeline_id, "data": get_pipeline(pipeline_id).model_dump() if get_pipeline(pipeline_id) else None})
                        return False
                    await ws.send_json(resume_event)
                    if resume_event.get("stage") == "dev_confirm":
                        return True
                return False
            if m.get("action") == "skip_instructions":
                pipeline = get_pipeline(pipeline_id)
                if pipeline:
                    await ws.send_json(await _update_stage(
                        pipeline, StageName.UML_OPTIMIZE, StageStatus.SKIPPED, "Skipped by user"))
                    await ws.send_json(await _update_stage(
                        pipeline, StageName.DEV_CONFIRM, StageStatus.SUCCESS,
                        "Auto-confirmed (skipped optimization)"))
                    _save_uml_review_record(diagram.name, "skip", "UML优化已跳过")
                    async for resume_event in resume_pipeline(
                        pipeline_id, diagram, language, auto_confirm=True,
                        source_dir=source_dir, test_dir=test_dir,
                        sequence_diagram=seq_diagram, component_diagram=comp_diagram,
                    ):
                        if _is_stopped(pipeline_id):
                            await ws.send_json({"event": "stopped", "pipeline_id": pipeline_id, "data": get_pipeline(pipeline_id).model_dump() if get_pipeline(pipeline_id) else None})
                            return False
                        await ws.send_json(resume_event)
                        if resume_event.get("event") == "request_case_review":
                            await wait_for_case_review()
                            return False
                return False

    async def wait_for_confirm():
        """Wait for user to confirm/reject the UML optimization result."""
        while True:
            m = await get_msg()
            if m is None:
                return
            if _is_stop_msg(m):
                stop_pipeline(pipeline_id)
                return
            if m.get("action") == "confirm":
                confirm_stage(
                    pipeline_id,
                    StageName(m.get("stage", "dev_confirm")),
                    m.get("accepted", False),
                    m.get("comment", ""),
                )
                async for resume_event in resume_pipeline(
                    pipeline_id, diagram, language, auto_confirm=True,
                    source_dir=source_dir, test_dir=test_dir,
                    sequence_diagram=seq_diagram, component_diagram=comp_diagram,
                    max_change_ratio=max_change_ratio,
                ):
                    if _is_stopped(pipeline_id):
                        await ws.send_json({"event": "stopped", "pipeline_id": pipeline_id, "data": get_pipeline(pipeline_id).model_dump() if get_pipeline(pipeline_id) else None})
                        return
                    await ws.send_json(resume_event)
                    if resume_event.get("event") == "request_case_review":
                        await wait_for_case_review()
                        return
                return

    async def wait_for_case_review():
        """Wait for user to confirm case review."""
        while True:
            m = await get_msg()
            if m is None:
                return
            if _is_stop_msg(m):
                stop_pipeline(pipeline_id)
                return
            if m.get("action") == "confirm_case_review":
                pipeline = get_pipeline(pipeline_id)
                if pipeline:
                    for s in pipeline.stages:
                        if s.name == StageName.CASE_REVIEW:
                            s.status = StageStatus.SUCCESS
                    tc = m.get("test_cases", "")
                    logger.info(f"[Pipeline] confirm_case_review: test_cases length={len(tc)}")
                    pipeline.stages[3].result = {"test_cases": tc}
                    _save_case_review_file(pipeline_id, diagram.name, tc)
                    await ws.send_json(await _update_stage(
                        pipeline, StageName.CASE_REVIEW, StageStatus.SUCCESS, "User confirmed"))
                async for resume_event in resume_pipeline(
                    pipeline_id, diagram, language, auto_confirm=True,
                    skip_case_review=True, skip_code_gen=True,
                    source_dir=source_dir, test_dir=test_dir,
                    sequence_diagram=seq_diagram, component_diagram=comp_diagram,
                ):
                    if _is_stopped(pipeline_id):
                        await ws.send_json({"event": "stopped", "pipeline_id": pipeline_id, "data": get_pipeline(pipeline_id).model_dump() if get_pipeline(pipeline_id) else None})
                        return
                    await ws.send_json(resume_event)
                return

    # ═══════════════════════════════════════════════════════════════
    # Drain any queued messages and check for stop
    # ═══════════════════════════════════════════════════════════════

    async def check_stop() -> bool:
        """Drain the queue non-blocking; return True if a stop was requested.
        Does NOT send the stopped event — caller is responsible for finalising."""
        while True:
            try:
                m = msg_queue.get_nowait()
            except asyncio.QueueEmpty:
                return _is_stopped(pipeline_id)
            if m is None or _is_stop_msg(m):
                if m is not None and not _is_stopped(pipeline_id):
                    stop_pipeline(pipeline_id)
                return True

    # ═══════════════════════════════════════════════════════════════
    # Main pipeline loop
    # ═══════════════════════════════════════════════════════════════

    try:
        async for event in run_pipeline(
            pipeline_id, diagram, language, auto_confirm,
            source_dir=source_dir, test_dir=test_dir,
            sequence_diagram=seq_diagram, component_diagram=comp_diagram,
            all_diagrams=all_diagrams,
            max_change_ratio=max_change_ratio,
        ):
            # Check for stop BEFORE every event we send
            if await check_stop():
                pipeline = get_pipeline(pipeline_id)
                await ws.send_json({
                    "event": "stopped", "pipeline_id": pipeline_id,
                    "data": pipeline.model_dump() if pipeline else None,
                })
                break

            await ws.send_json(event)

            # Save incremental log after key stages
            if event.get("event") == "stage_update" and event.get("status") in ("success", "failed"):
                stage_name = event.get("stage", "")
                if stage_name in ("code_gen", "test_gen", "code_optimize"):
                    p = get_pipeline(pipeline_id)
                    if p:
                        try:
                            _save_pipeline_log(p, diagram, language)
                        except Exception:
                            pass

            # Pause points — hand control to the appropriate waiter
            if event.get("event") == "request_instructions":
                need_confirm = await wait_for_instructions()
                if need_confirm:
                    await wait_for_confirm()
                break

            if event.get("stage") == "dev_confirm" and not auto_confirm:
                await wait_for_confirm()
                break

            if event.get("event") == "request_case_review":
                await wait_for_case_review()
                break

        # Drain any remaining queued messages (e.g. stop that arrived during final stages)
        was_stopped = await check_stop()

        # Final log save
        pipeline = get_pipeline(pipeline_id)
        if pipeline:
            try:
                _save_pipeline_log(pipeline, diagram, language)
            except Exception as log_e:
                logger.warning(f"[Pipeline] Failed to save log: {log_e}")

        if was_stopped:
            pipeline = get_pipeline(pipeline_id)
            await ws.send_json({
                "event": "stopped", "pipeline_id": pipeline_id,
                "data": pipeline.model_dump() if pipeline else None,
            })
        else:
            await ws.send_json({"event": "pipeline_complete", "pipeline_id": pipeline_id,
                                "message": "流水线执行完成"})
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await ws.send_json({"event": "error", "error": str(e)})
        except Exception:
            pass
    finally:
        _clear_stop_flag(pipeline_id)
        reader_task.cancel()
        try:
            await reader_task
        except asyncio.CancelledError:
            pass
