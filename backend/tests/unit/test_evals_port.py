"""Tests for the provider-neutral evaluation boundary."""

from types import SimpleNamespace

import pytest

from app.agent_base.core.evals import NoOpEvalProvider, load_evals


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
