"""Common request/response boundary for OpenAI-compatible providers."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, AsyncIterator, Protocol

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LLMRequest:
    messages: list[dict[str, Any]]
    model: str
    temperature: float = 0.7
    max_tokens: int | None = None
    json_mode: bool = False
    tools: list[dict[str, Any]] | None = None
    tool_choice: str = "auto"
    timeout: float | None = None


@dataclass(frozen=True)
class LLMUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cached_tokens: int | None = None

    def to_dict(self) -> dict[str, int | None]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cached_tokens": self.cached_tokens,
        }


@dataclass(frozen=True)
class LLMResponse:
    content: str = ""
    model: str = ""
    finish_reason: str = ""
    tool_calls: list[dict[str, Any]] | None = None
    usage: LLMUsage | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "model": self.model,
            "finish_reason": self.finish_reason,
            "tool_calls": self.tool_calls,
            "usage": self.usage.to_dict() if self.usage else None,
        }


@dataclass(frozen=True)
class LLMChunk:
    text: str = ""
    usage: LLMUsage | None = None


class LLMGateway(Protocol):
    async def complete(self, request: LLMRequest) -> LLMResponse: ...

    def stream(self, request: LLMRequest) -> AsyncIterator[LLMChunk]: ...


def _value(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


def _usage(source: Any) -> LLMUsage | None:
    if source is None:
        return None
    prompt = _value(source, "prompt_tokens")
    completion = _value(source, "completion_tokens")
    total = _value(source, "total_tokens")
    if total is None and prompt is not None and completion is not None:
        total = int(prompt) + int(completion)
    cached = _value(source, "prompt_cache_hit_tokens")
    if cached is None:
        cached = _value(source, "cached_tokens")
    return LLMUsage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        cached_tokens=cached,
    )


def _tool_calls(message: Any) -> list[dict[str, Any]] | None:
    calls = _value(message, "tool_calls")
    if not calls:
        return None
    normalized = []
    for call in calls:
        function = _value(call, "function", {})
        normalized.append({
            "id": _value(call, "id", ""),
            "type": _value(call, "type", "function"),
            "function": {
                "name": _value(function, "name", ""),
                "arguments": _value(function, "arguments", ""),
            },
        })
    return normalized


def _is_retryable(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    return any(token in name or token in message for token in (
        "timeout", "connection", "rate limit", "server", "temporarily",
        "read error", "remote protocol", "reset", "broken pipe",
    ))


class OpenAICompatibleGateway:
    """Async gateway for OpenAI-compatible chat-completion providers."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 120.0,
        max_retries: int = 2,
        client: AsyncOpenAI | Any | None = None,
    ) -> None:
        self.model = model
        self.timeout = timeout
        self.max_retries = max(0, int(max_retries))
        self.client = client or AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )

    async def complete(self, request: LLMRequest) -> LLMResponse:
        kwargs = self._request_kwargs(request)
        response = await self._call_with_retry(
            lambda: self.client.chat.completions.create(**kwargs),
        )
        choice = response.choices[0]
        message = choice.message
        return LLMResponse(
            content=_value(message, "content", "") or "",
            model=_value(response, "model", request.model) or request.model,
            finish_reason=_value(choice, "finish_reason", "") or "",
            tool_calls=_tool_calls(message),
            usage=_usage(_value(response, "usage")),
        )

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMChunk]:
        kwargs = self._request_kwargs(request)
        kwargs["stream"] = True
        stream = await self._call_with_retry(
            lambda: self.client.chat.completions.create(**kwargs),
        )
        async for chunk in stream:
            choices = _value(chunk, "choices", []) or []
            text = ""
            if choices:
                delta = _value(choices[0], "delta", {})
                text = _value(delta, "content", "") or ""
            usage = _usage(_value(chunk, "usage"))
            if text or usage:
                yield LLMChunk(text=text, usage=usage)

    def _request_kwargs(self, request: LLMRequest) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": request.model or self.model,
            "messages": request.messages,
            "temperature": request.temperature,
        }
        if request.max_tokens is not None:
            kwargs["max_tokens"] = request.max_tokens
        if request.json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if request.tools:
            kwargs["tools"] = request.tools
            kwargs["tool_choice"] = request.tool_choice
        if request.timeout is not None:
            kwargs["timeout"] = request.timeout
        return kwargs

    async def _call_with_retry(self, call):
        for attempt in range(self.max_retries + 1):
            try:
                return await call()
            except Exception as exc:
                if attempt >= self.max_retries or not _is_retryable(exc):
                    raise
                delay = min(2.0 ** attempt, 8.0)
                logger.warning(
                    "[LLM] retryable provider error; retry %d/%d after %.1fs: %s",
                    attempt + 1, self.max_retries, delay, exc,
                )
                await asyncio.sleep(delay)


__all__ = [
    "LLMChunk",
    "LLMGateway",
    "LLMRequest",
    "LLMResponse",
    "LLMUsage",
    "OpenAICompatibleGateway",
]
