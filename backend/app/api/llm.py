"""LLM integration API – code generation, UML optimization, chat."""

import logging
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.models.uml import (
    LlmRequest, LlmResponse,
    UmlOptimizeRequest, UmlOptimizeResponse,
)
from app.services.llm_service import chat
from app.services.code_generator import (
    SUPPORTED_LANGUAGES, optimize_uml,
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
