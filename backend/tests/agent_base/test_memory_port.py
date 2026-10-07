"""Memory failures use the production lifecycle contribution."""
import asyncio
import pytest
from app.agent_base.core.extension_context import ExtensionContext, extension_scope
from app.agent_base.core.hooks import HookContext, HookEvent, HookRegistry
from extensions.memory.contributions import prepare
from extensions.memory.plugin_api import MemoryRecallResult

@pytest.mark.parametrize("outcome", ["valid", "broken", "invalid", "absent"])
def test_recall_lifecycle_contains_failures(outcome, caplog):
    class Provider:
        async def recall(self, request):
            assert request.project_id == "p" and request.query == "query"
            if outcome == "broken":
                raise RuntimeError("backend unavailable")
            return MemoryRecallResult(context_block="evidence") if outcome == "valid" else None
    session = ExtensionContext(providers={} if outcome == "absent" else {"memory": Provider()})
    registry = HookRegistry()
    registry.register(HookEvent.PREPARE, prepare, mode="transform", contribution_id="memory.context.prepare")
    payload = {"project_id": "p", "user_message": "query", "sections": {}}
    with extension_scope(session):
        asyncio.run(registry.aemit(HookEvent.PREPARE, HookContext(HookEvent.PREPARE, "test", payload=payload)))
    assert payload["sections"] == ({"memory": "evidence"} if outcome == "valid" else {})
    if outcome in {"broken", "invalid"}:
        assert "prepare recall failed" in caplog.text
