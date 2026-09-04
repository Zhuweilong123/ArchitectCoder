"""Tests for the provider-neutral LLM gateway."""

import asyncio
from types import SimpleNamespace

from app.llm import LLMRequest, OpenAICompatibleGateway
from app.agent_base.core.llm import BaseAgentsLLM


class _Completions:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class _Client:
    def __init__(self, response):
        self.chat = SimpleNamespace(completions=_Completions(response))


def _response(content="answer", finish_reason="stop"):
    return SimpleNamespace(
        model="fake-model",
        choices=[SimpleNamespace(
            finish_reason=finish_reason,
            message=SimpleNamespace(content=content, tool_calls=None),
        )],
        usage=SimpleNamespace(
            prompt_tokens=3,
            completion_tokens=2,
            total_tokens=5,
            prompt_cache_hit_tokens=1,
        ),
    )


def test_gateway_normalizes_response_and_usage():
    client = _Client(_response())
    gateway = OpenAICompatibleGateway(
        api_key="test", base_url="http://fake", model="fake-model", client=client,
    )

    result = asyncio.run(gateway.complete(LLMRequest(
        messages=[{"role": "user", "content": "hi"}], model="fake-model",
    )))

    assert result.content == "answer"
    assert result.finish_reason == "stop"
    assert result.usage.to_dict()["total_tokens"] == 5
    assert result.usage.cached_tokens == 1
    assert client.chat.completions.calls[0]["model"] == "fake-model"


def test_gateway_builds_json_and_tools_request():
    client = _Client(_response())
    gateway = OpenAICompatibleGateway(
        api_key="test", base_url="http://fake", model="fake-model", client=client,
    )

    asyncio.run(gateway.complete(LLMRequest(
        messages=[], model="fake-model", json_mode=True,
        tools=[{"type": "function"}], tool_choice="auto", timeout=7,
    )))

    call = client.chat.completions.calls[0]
    assert call["response_format"] == {"type": "json_object"}
    assert call["tools"] == [{"type": "function"}]
    assert call["tool_choice"] == "auto"
    assert call["timeout"] == 7


def test_gateway_streams_text_chunks():
    class _StreamCompletions:
        async def create(self, **kwargs):
            async def chunks():
                yield SimpleNamespace(
                    choices=[SimpleNamespace(
                        delta=SimpleNamespace(content="a"),
                    )],
                )
                yield SimpleNamespace(
                    choices=[SimpleNamespace(
                        delta=SimpleNamespace(content="b"),
                    )],
                )
            return chunks()

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=_StreamCompletions()),
    )
    gateway = OpenAICompatibleGateway(
        api_key="test", base_url="http://fake", model="fake-model", client=client,
    )

    async def collect():
        return [chunk.text async for chunk in gateway.stream(LLMRequest(
            messages=[], model="fake-model",
        ))]

    assert asyncio.run(collect()) == ["a", "b"]


def test_base_agents_llm_routes_async_calls_through_gateway():
    class _AgentGateway:
        async def complete(self, request):
            return _response_with_tools(request.model)

        async def stream(self, request):
            if False:
                yield request

    llm = BaseAgentsLLM(
        api_key="test", base_url="http://fake", model="fake-model",
    )
    llm.gateway = _AgentGateway()

    result = asyncio.run(llm.ainvoke_with_tools(
        [{"role": "user", "content": "call"}],
        [{"type": "function"}],
    ))

    assert result["tool_calls"][0]["function"]["name"] == "echo"
    assert result["usage"]["total_tokens"] == 5


def _response_with_tools(model):
    return type("GatewayResponse", (), {
        "content": None,
        "model": model,
        "finish_reason": "tool_calls",
        "tool_calls": [{
            "id": "call-1",
            "type": "function",
            "function": {"name": "echo", "arguments": "{}"},
        }],
        "usage": type("Usage", (), {
            "to_dict": lambda self: {
                "prompt_tokens": 3,
                "completion_tokens": 2,
                "total_tokens": 5,
                "cached_tokens": None,
            },
        })(),
    })()
