"""Execution policy is replaceable without editing the transaction coordinator."""
import asyncio
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent_base.adapters.execution import dispatch_execution
from app.agent_base.core import hooks, plugin_runtime
from app.agent_base.core.extension_context import ExtensionContext, extension_scope
from app.agent_base.core.hooks import HookRegistry
from app.agent_base.host_api.execution import ExecutionRequest, ExecutionSlots
from app.agent_base.host_api.lifecycle import HookContext, HookEvent


@pytest.fixture
def registry(monkeypatch):
    registry = HookRegistry()
    monkeypatch.setattr(hooks, "_registry", registry)
    monkeypatch.setattr(plugin_runtime, "_active", None)
    return registry


def test_unrelated_policy_prepares_checks_and_observes_committed_transaction(registry):
    seen = []

    async def prepare(ctx):
        ctx.invocation.context += "\ncustom context"
        ctx.invocation.allowed_tools = ["read_file"]

    async def check(ctx):
        ctx.invocation.allowed = False
        ctx.invocation.message = "custom policy rejected"

    def committed(ctx):
        seen.extend(ctx.invocation.data["changed_paths"])

    for slot, handler, stage in [(ExecutionSlots.PREPARE, prepare, HookEvent.PREPARE),
                                  (ExecutionSlots.CHECK, check, HookEvent.FINALIZE),
                                  (ExecutionSlots.COMMITTED, committed, HookEvent.FINALIZE)]:
        registry.register(stage, handler, contribution_id="custom." + slot, mode="service", interface_id=slot)
    # Normal model lifecycle publication must not run transaction policies.
    asyncio.run(registry.aemit(HookEvent.FINALIZE, HookContext(HookEvent.FINALIZE, "test")))
    request = ExecutionRequest(ExecutionSlots.PREPARE)
    asyncio.run(dispatch_execution(request))
    assert request.context == "\ncustom context" and request.allowed_tools == ["read_file"]
    request.interface_id = ExecutionSlots.CHECK
    asyncio.run(dispatch_execution(request))
    assert not request.allowed and request.message == "custom policy rejected"
    request.interface_id = ExecutionSlots.COMMITTED
    request.data["changed_paths"] = [{"path": "accepted.py"}]
    asyncio.run(dispatch_execution(request))
    assert seen == [{"path": "accepted.py"}]


def test_missing_required_binding_and_handler_exception_cannot_approve(registry):
    session = ExtensionContext(metadata={"required_execution_bindings": {
        ExecutionSlots.CHECK: ("required.check",)}})
    with extension_scope(session), pytest.raises(RuntimeError, match="required execution"):
        asyncio.run(dispatch_execution(ExecutionRequest(ExecutionSlots.CHECK)))

    def broken(ctx):
        raise ValueError("check failed")

    registry.register(HookEvent.FINALIZE, broken, contribution_id="required.check",
                      mode="service", interface_id=ExecutionSlots.CHECK)
    with extension_scope(session), pytest.raises(ValueError, match="check failed"):
        asyncio.run(dispatch_execution(ExecutionRequest(ExecutionSlots.CHECK)))


def test_execution_entry_does_not_select_plugin_policies_or_loaders():
    root = Path(__file__).resolve().parents[3]
    source = (root / "backend/app/services/agent_execution.py").read_text(encoding="utf-8-sig")
    assert all(marker not in source for marker in (
        "load_orchestrator", "contract_gate", "contract_failure_analyzer",
        "architecture_scheduling", "contract_graph_sync", "route_architecture"))
    for path in (root / "extensions").rglob("*.py"):
        if path.name in {"api.py", "cli.py", "full_api.py", "trace_cases.py"}:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith(("app.services", "app.agent_base.adapters",
                    "app.agent_base.assembly", "app.agent_base.agents", "app.agent_base.core")), path
