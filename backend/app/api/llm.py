"""LLM integration API – code generation, UML optimization, chat."""

import logging
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.models.uml import (
    LlmRequest, LlmResponse,
    CodeGenRequest, CodeGenResponse,
    UmlOptimizeRequest, UmlOptimizeResponse,
)
from app.services.llm_service import chat
from app.services.code_generator import (
    SUPPORTED_LANGUAGES, generate_code, optimize_uml, optimize_project,
    optimize_project_stream,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/llm", tags=["llm"])


@router.post("/chat", response_model=LlmResponse)
async def llm_chat(req: LlmRequest):
    """Send a prompt to the LLM and get a response."""
    content = await chat(
        prompt=req.prompt,
        system_prompt=req.system_prompt,
        temperature=req.temperature,
        max_tokens=req.max_tokens,
    )
    return LlmResponse(content=content)


@router.get("/languages")
async def get_languages():
    """Get the list of supported programming languages."""
    return {"languages": SUPPORTED_LANGUAGES}


@router.post("/generate-code", response_model=CodeGenResponse)
async def generate_code_endpoint(req: CodeGenRequest):
    """Generate code from a UML diagram for a specific language."""
    if req.language not in SUPPORTED_LANGUAGES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported language: {req.language}. Supported: {SUPPORTED_LANGUAGES}",
        )
    files = await generate_code(req.diagram, req.language)
    return CodeGenResponse(language=req.language, files=files)


@router.post("/optimize-uml", response_model=UmlOptimizeResponse)
async def optimize_uml_endpoint(req: UmlOptimizeRequest):
    """Ask LLM to analyze and optimize a UML diagram design."""
    try:
        result = await optimize_uml(req.diagram, req.instructions)

        from app.models.uml import UmlDiagram as UD
        optimized = UD(**result.get("optimized", req.diagram.model_dump()))

        return UmlOptimizeResponse(
            original=req.diagram,
            optimized=optimized,
            changes_summary=result.get("changes_summary", ""),
            diff=result.get("diff", ""),
        )
    except Exception as e:
        logger.exception(f"UML optimization failed: {e}")
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {str(e)}")


class GlobalOptimizeRequest(BaseModel):
    """Request for global (multi-diagram) optimization.

    Send existing diagrams as a list in ``existing_diagrams`` for reference.
    The old per-type fields (class_diagram, sequence_diagram, component_diagram)
    are still accepted for backward compatibility.
    """
    instructions: str = ""
    existing_diagrams: list[dict] = Field(default_factory=list)
    # Deprecated — kept for backward compatibility:
    class_diagram: dict | None = None
    sequence_diagram: dict | None = None
    component_diagram: dict | None = None


@router.post("/optimize-project")
async def optimize_project_endpoint(req: GlobalOptimizeRequest):
    """Cross-validate and globally optimize all diagrams in a project."""
    try:
        # Build diagrams list — prefer new existing_diagrams, fall back to old fields
        diagrams = list(req.existing_diagrams) if req.existing_diagrams else []
        if not diagrams:
            for d in (req.class_diagram, req.sequence_diagram, req.component_diagram):
                if d:
                    diagrams.append(d)
        result = await optimize_project(
            diagrams=diagrams if diagrams else None,
            instructions=req.instructions,
        )
        return result
    except Exception as e:
        logger.exception(f"Global optimization failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/optimize-project-stream")
async def optimize_project_stream_endpoint(req: GlobalOptimizeRequest):
    """Streaming global optimization — yields entities one by one as SSE."""

    # Build diagrams list — prefer new existing_diagrams, fall back to old fields
    diagrams = list(req.existing_diagrams) if req.existing_diagrams else []
    if not diagrams:
        for d in (req.class_diagram, req.sequence_diagram, req.component_diagram):
            if d:
                diagrams.append(d)

    async def event_stream():
        async for payload in optimize_project_stream(
            diagrams=diagrams if diagrams else None,
            instructions=req.instructions,
        ):
            if payload == "DONE":
                yield "data: DONE\n\n"
            else:
                # Multi-line SSE: each line of the payload prefixed with "data: "
                for pline in payload.split("\n"):
                    yield f"data: {pline}\n"
                yield "\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
