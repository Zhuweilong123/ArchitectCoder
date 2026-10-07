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

    registry.register(HookEvent.FINALIZE, lambda ctx: None, contribution_id="required.check",
                      mode="observer")
    with extension_scope(session), pytest.raises(RuntimeError, match="required execution"):
        asyncio.run(dispatch_execution(ExecutionRequest(ExecutionSlots.CHECK)))
    registry.clear()

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
    transports = {"trace/api.py", "evals/api.py", "evals/cli.py", "evals/full_api.py"}
    forbidden = ("app.services", "app.agent_base.adapters", "app.agent_base.assembly",
                 "app.agent_base.agents", "app.agent_base.core", "backend.config", "app.runtime")
    for path in (root / "extensions").rglob("*.py"):
        relative = path.relative_to(root / "extensions").as_posix()
        for module in imported_modules(path.read_text(encoding="utf-8-sig")):
            # Only the named transport entry points may use loader adapters.
            if relative in transports and module.startswith("app.agent_base.adapters."):
                continue
            assert not any(module == prefix or module.startswith(prefix + ".") for prefix in forbidden), (path, module)


def imported_modules(source):
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            yield node.module
        elif isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)


def test_boundary_scanner_checks_both_import_styles_and_local_imports():
    assert list(imported_modules("import app.runtime as runtime\ndef f():\n from backend.config import Settings")) == [
        "app.runtime", "backend.config"]


def test_host_protocols_do_not_import_implementations_or_plugins():
    root = Path(__file__).resolve().parents[3] / "backend/app/agent_base/host_api"
    forbidden = ("extensions", "backend.config", "app.services", "app.runtime",
                 "app.agent_base.core", "app.agent_base.adapters", "app.agent_base.agents")
    for path in root.rglob("*.py"):
        for module in imported_modules(path.read_text(encoding="utf-8-sig")):
            assert not any(module == prefix or module.startswith(prefix + ".") for prefix in forbidden), (path, module)
