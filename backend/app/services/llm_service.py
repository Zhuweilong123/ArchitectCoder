"""Application-level chat helpers backed by the shared LLM gateway."""

from __future__ import annotations

import logging

from backend.config import get_settings
from app.llm import LLMRequest, OpenAICompatibleGateway

settings = get_settings()

_gateway: OpenAICompatibleGateway | None = None


def get_gateway() -> OpenAICompatibleGateway:
    """Return the configured gateway for the default application model."""
    global _gateway
    if _gateway is None:
        _gateway = OpenAICompatibleGateway(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_model,
            timeout=120.0,
        )
    return _gateway


def _resolve_model(model: str | None) -> str:
    return model or settings.deepseek_model


def _messages(prompt: str, system_prompt: str | None = None) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    return messages


async def chat(
    prompt: str,
    system_prompt: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    json_mode: bool = False,
    model: str | None = None,
) -> str:
    """Single-turn chat completion with bounded recovery retries."""
    logger = logging.getLogger(__name__)
    gateway = get_gateway()

    async def _call(json_mode_value: bool, token_limit: int):
        return await gateway.complete(LLMRequest(
            messages=_messages(prompt, system_prompt),
            model=_resolve_model(model),
            temperature=temperature,
            max_tokens=token_limit,
            json_mode=json_mode_value,
            timeout=120.0,
        ))

    response = await _call(json_mode, max_tokens)
    if response.finish_reason == "length":
        logger.warning(
            "[chat] Output truncated at %d tokens; retrying with %d",
            max_tokens, max_tokens * 2,
        )
        response = await _call(False, max_tokens * 2)
    if not response.content:
        logger.warning(
            "[chat] Empty response (finish=%s, json_mode=%s); retrying",
            response.finish_reason, json_mode,
        )
        response = await _call(False, max_tokens)
    return response.content


__all__ = [
    "chat",
    "get_gateway",
]
