"""LLM integration API – chat and supported languages."""

from fastapi import APIRouter

from app.models.uml import (
    LlmRequest, LlmResponse,
)
from app.services.llm_service import chat
from app.services.code_generator import SUPPORTED_LANGUAGES

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
