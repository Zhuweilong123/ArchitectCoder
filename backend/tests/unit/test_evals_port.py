"""Tests for the provider-neutral evaluation boundary."""

from types import SimpleNamespace
import asyncio

import pytest

from app.agent_base.adapters.evals import (NoOpEvalProvider, _ResilientEvalProvider, load_evals)
from extensions.evals.plugin_api import EvalCase, EvalResult


class _Provider:
    def list_cases(self):
        return [{"id": "case-1"}]

    def get_case(self, case_id):
        return {"id": case_id}

    async def run_case(self, case):
        return {"case_id": case["id"], "passed": True}

    def list_results(self, limit=100):
        return []


def test_disabled_evals_use_noop_provider():
    provider = load_evals(settings=SimpleNamespace(agent_evals_enabled=False))
    assert isinstance(provider, NoOpEvalProvider)
    assert provider.list_cases() == []
    with pytest.raises(RuntimeError, match="disabled"):
        import asyncio
        asyncio.run(provider.run_case({}))


def test_eval_provider_factory_is_pluggable(monkeypatch):
    import sys

    module_name = "test_eval_provider_plugin"
    monkeypatch.setitem(sys.modules, module_name, SimpleNamespace(create=lambda **kwargs: _Provider()))
    settings = SimpleNamespace(
        agent_evals_enabled=True,
        agent_evals_provider=f"{module_name}:create",
    )

    provider = load_evals(settings=settings)
    assert provider.list_cases() == [{"id": "case-1"}]


def test_typed_eval_wrapper_preserves_arguments_and_result_identity():
    case = EvalCase(id="typed", prompt="inspect")
    result = EvalResult(run_id="run", case_id=case.id, status="passed", passed=True)
    calls = []

    class Provider(NoOpEvalProvider):
        def get_case(self, case_id):
            calls.append(case_id)
            return case

        async def run_case(self, received):
            assert received is case
            return result

    provider = _ResilientEvalProvider(Provider())
    assert provider.get_case(case_id="typed") is case
    assert asyncio.run(provider.run_case(case=case)) is result
    assert calls == ["typed"]


@pytest.mark.parametrize("operation", ["get_case", "run_case"])
def test_eval_wrapper_logs_and_preserves_original_errors(caplog, operation):
    error = ValueError("invalid evaluation")

    class Provider(NoOpEvalProvider):
        def get_case(self, case_id):
            raise error

        async def run_case(self, case):
            raise error

    provider = _ResilientEvalProvider(Provider())
    with pytest.raises(ValueError) as caught:
        if operation == "get_case":
            provider.get_case("case")
        else:
            asyncio.run(provider.run_case(EvalCase(id="case", prompt="inspect")))
    assert caught.value is error
    assert f"provider operation {operation} failed" in caplog.text


def test_eval_wrapper_propagates_cancellation_without_failure_log(caplog):
    class Provider(NoOpEvalProvider):
        async def run_case(self, case):
            raise asyncio.CancelledError()

    provider = _ResilientEvalProvider(Provider())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(provider.run_case(EvalCase(id="case", prompt="inspect")))
    assert "provider operation" not in caplog.text


def test_eval_wrapper_preserves_sync_and_async_trace_case_extensions():
    request = object()

    class Provider(NoOpEvalProvider):
        def get_trace_case_draft(self, draft_id):
            return {"draft_id": draft_id}

        async def create_trace_case_draft(self, received):
            assert received is request
            return {"draft_id": "new"}

    provider = _ResilientEvalProvider(Provider())
    assert provider.get_trace_case_draft(draft_id="old") == {"draft_id": "old"}
    assert asyncio.run(provider.create_trace_case_draft(request)) == {"draft_id": "new"}
