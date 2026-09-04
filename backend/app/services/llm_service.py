"""Application-level chat helpers backed by the shared LLM gateway."""

from __future__ import annotations

import logging
from typing import Any

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


def get_client() -> Any:
    """Compatibility accessor for callers that still need the SDK client."""
    return get_gateway().client


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


async def chat_stream(
    prompt: str,
    system_prompt: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    model: str | None = None,
):
    """Stream content chunks from the shared gateway."""
    request = LLMRequest(
        messages=_messages(prompt, system_prompt),
        model=_resolve_model(model),
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=120.0,
    )
    async for chunk in get_gateway().stream(request):
        if chunk.text:
            yield chunk.text


async def chat_with_history(
    messages: list[dict],
    temperature: float = 0.7,
    max_tokens: int = 4096,
    model: str | None = None,
) -> str:
    response = await get_gateway().complete(LLMRequest(
        messages=messages,
        model=_resolve_model(model),
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=120.0,
    ))
    return response.content


async def chat_with_tools(
    messages: list[dict],
    tools: list[dict],
    tool_choice: str = "auto",
    temperature: float = 0.3,
    max_tokens: int = 4096,
    model: str | None = None,
) -> dict:
    """Complete a conversation with native function-calling support."""
    response = await get_gateway().complete(LLMRequest(
        messages=messages,
        model=_resolve_model(model),
        temperature=temperature,
        max_tokens=max_tokens,
        tools=tools,
        tool_choice=tool_choice,
        timeout=120.0,
    ))
    return {
        "content": response.content or None,
        "tool_calls": response.tool_calls,
    }


__all__ = [
    "chat",
    "chat_stream",
    "chat_with_history",
    "chat_with_tools",
    "get_client",
    "get_gateway",
]
