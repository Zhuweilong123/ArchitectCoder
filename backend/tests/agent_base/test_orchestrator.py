"""Orchestration preparation uses the actual transaction slot."""
import asyncio
from types import SimpleNamespace
import pytest
from app.agent_base.adapters.execution import dispatch_execution
from app.agent_base.core import hooks
from app.agent_base.core.extension_context import ExtensionContext, extension_scope
from app.agent_base.core.hooks import HookRegistry, HookEvent
from app.agent_base.host_api.execution import ExecutionRequest, ExecutionSlots
from extensions.orchestration.execution import prepare

@pytest.mark.parametrize("outcome", ["broken", "invalid", "disabled", "absent"])
def test_orchestrator_slot_failure_or_disabled_does_not_block_main_agent(monkeypatch, outcome):
    class Provider:
        async def prepare(self, request):
            if outcome == "broken":
                raise RuntimeError("provider unavailable")
            return None
    registry = HookRegistry()
    monkeypatch.setattr(hooks, "_registry", registry)
    registry.register(HookEvent.PREPARE, prepare, mode="service",
        contribution_id="orchestration.execution.prepare", interface_id=ExecutionSlots.PREPARE)
    session = ExtensionContext(providers={} if outcome == "absent" else {"orchestration": Provider()}, metadata={
        "execution_settings": SimpleNamespace(agent_orchestration_enabled=outcome != "disabled",
                                               agent_knowledge_graph_enabled=True)})
    request = ExecutionRequest(ExecutionSlots.PREPARE, data={"user_message": "hello",
        "project_file": "demo.umlproj", "source_dir": "", "test_dir": "",
        "available_tools": ["read_file", "route_architecture", "explore_architecture"]})
    with extension_scope(session):
        asyncio.run(dispatch_execution(request))
    if outcome == "absent":
        assert request.allowed_tools is None
    else:
        assert request.allowed_tools == ["read_file"]
        if outcome in {"broken", "invalid"}:
            assert request.data["phase"] == "unavailable"
