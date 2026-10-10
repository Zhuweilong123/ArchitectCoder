"""Timeout recovery retries only the unanswered model request."""

import asyncio
from types import SimpleNamespace

import pytest

from app.agent_base.agents.react_runtime.fc_loop import _invoke_fc_model_impl
from app.agent_base.core.hooks import AgentRuntime, set_runtime, reset_runtime
from app.agent_base.core.exceptions import AgentInterrupted


async def invoke(llm, runtime=None, retries=3):
    runtime = runtime or AgentRuntime()
    token = set_runtime(runtime)
    try:
        return await _invoke_fc_model_impl(
            SimpleNamespace(name="Test", llm=llm, llm_timeout_retries=retries),
            messages=[{"role": "user", "content": "repair"}], tool_specs=[],
            finalization_mode=False, request_context={"current_user_index": 0},
            runtime=runtime, temperature=0.3, timeout_seconds=300,
        )
    finally:
        reset_runtime(token)


class TimeoutLLM:
    def __init__(self, failures):
        self.failures = failures
        self.calls = []

    async def ainvoke_with_tools(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) <= self.failures:
            raise asyncio.TimeoutError
        return {"content": "recovered", "tool_calls": []}


@pytest.mark.parametrize("failures", [0, 1, 3, 4])
def test_timeout_retries_up_to_three_times_with_five_minute_attempts(failures):
    llm = TimeoutLLM(failures)
    response, kind, reason = asyncio.run(invoke(llm))
    assert len(llm.calls) == min(failures + 1, 4)
    for index, call in enumerate(llm.calls, 1):
        assert call["trace_context"]["timeout_seconds"] == 300
        assert call["trace_context"]["timeout_attempt"] == index
        assert call["trace_context"]["timeout_max_attempts"] == 4
        assert call["messages"] == llm.calls[0]["messages"]
    if failures == 4:
        assert response is None
        assert (kind, reason) == ("timeout", "llm_timeout")
    else:
        assert response["content"] == "recovered"
        assert (kind, reason) == ("", "")


def test_stop_after_timeout_prevents_retry():
    llm = TimeoutLLM(4)
    runtime = AgentRuntime(stop_check=lambda: len(llm.calls) >= 1)
    with pytest.raises(AgentInterrupted):
        asyncio.run(invoke(llm, runtime))
    assert len(llm.calls) == 1


def test_cancel_in_flight_request_is_not_retried():
    calls = []
    async def run():
        started = asyncio.Event()
        async def request(**kwargs):
            calls.append(kwargs)
            started.set()
            await asyncio.Event().wait()
        task = asyncio.create_task(invoke(SimpleNamespace(ainvoke_with_tools=request)))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(run())
    assert len(calls) == 1


def test_non_timeout_error_is_not_retried():
    calls = []
    async def request(**kwargs):
        calls.append(kwargs)
        raise ValueError("bad request")
    with pytest.raises(ValueError, match="bad request"):
        asyncio.run(invoke(SimpleNamespace(ainvoke_with_tools=request)))
    assert len(calls) == 1
