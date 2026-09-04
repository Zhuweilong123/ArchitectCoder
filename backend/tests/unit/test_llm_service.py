"""Tests that the application chat helpers use the shared gateway."""

import asyncio

from app.llm import LLMResponse, LLMUsage
from app.services import llm_service


class _Gateway:
    async def complete(self, request):
        return LLMResponse(
            content="gateway response",
            model=request.model,
            usage=LLMUsage(total_tokens=3),
        )

    async def stream(self, request):
        if False:
            yield request


def test_chat_delegates_to_gateway(monkeypatch):
    monkeypatch.setattr(llm_service, "_gateway", _Gateway())

    result = asyncio.run(llm_service.chat("hello"))

    assert result == "gateway response"


def test_chat_with_tools_keeps_legacy_response_shape(monkeypatch):
    monkeypatch.setattr(llm_service, "_gateway", _Gateway())

    result = asyncio.run(llm_service.chat_with_tools([], []))

    assert result == {"content": "gateway response", "tool_calls": None}
