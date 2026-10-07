"""Exercise plugin policy selection and the host capability boundary."""
import ast
import asyncio
import sys
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.config import Settings
from app.agent_base.adapters.contract_analysis import load_contract_failure_analyzer
from app.agent_base.adapters.contract_gate import build_contract_gate_context, load_contract_gate
from app.agent_base.adapters.contracts import load_contracts
from app.agent_base.adapters.review import ReviewAdapter
from app.agent_base.core import hooks, plugins
from app.agent_base.core.hooks import HookRegistry
from app.agent_base.core.lifecycle import build_plan, install_plan
from app.agent_base.core.plugins import PluginManager
from app.agent_base.host_api.contexts import ContractFailureAnalysisContext, ContractGateContext, ContractGateDecision, ReviewPrompt
from app.agent_base.host_api.contract_checks import ContractCheckResult
from app.trace.tracing import reset_current_trace_sink, set_current_trace_sink
from extensions.design_contract.gate import DefaultContractGate


async def emit_ok(event):
    return True


def context(**kwargs):
    return ContractGateContext(workspace_manifest={"workspace_root": "example"},
        changed_paths=("example/src/service.py",), emit=emit_ok, run_id="candidate", **kwargs)


def test_host_api_contains_no_loading_or_concrete_extension_dependencies():
    base = Path(__file__).resolve().parents[2] / "app/agent_base"
    for path in (base / "host_api").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not any(part in (node.module or "").split(".")
                               for part in ("extensions", "adapters", "plugins")), path
            elif isinstance(node, ast.Import):
                assert all(not item.name.startswith("extensions") for item in node.names), path
    for name in ("memory", "skills", "orchestration", "knowledge_graph", "evals", "contracts",
                 "contract_gate", "contract_analysis", "contract_harness", "contract_pipeline", "language_adapters"):
        assert not (base / "core" / f"{name}.py").exists()
    for path in [base / "__init__.py", *(base / "adapters").glob("*.py")]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("extensions"):
                assert (node.module or "").endswith("plugin_api"), path
    tree = ast.parse((base / "assembly.py").read_text(encoding="utf-8"))
    assert all(not isinstance(node, ast.ImportFrom) or not (node.module or "").startswith("extensions")
               or (node.module or "").endswith("plugin_api") for node in ast.walk(tree))
    assert not {"agent", "change_set", "review_manager"} & {f.name for f in fields(ContractGateContext)}
    assert "agent" not in {f.name for f in fields(ContractFailureAnalysisContext)}


def test_host_context_is_a_snapshot_of_manifest_and_candidate_changes():
    manifest = {"workspace_root": "original", "project_files": ["one.uml"]}
    changes = [{"path": "one.py", "operation": "modify"}]
    built = build_contract_gate_context(agent=SimpleNamespace(workspace_manifest=manifest),
        change_set=SimpleNamespace(manifest=lambda: changes), review_manager=None,
        emit=emit_ok, emit_review=emit_ok)
    manifest["project_files"].append("two.uml")
    changes[0]["path"] = "two.py"
    assert built.workspace_manifest["project_files"] == ["one.uml"]
    assert built.changed_paths[0]["path"] == "one.py"


def test_configured_policy_provider_is_scheduled_for_gate_and_failure_analysis(monkeypatch):
    calls, events = [], []
    class Provider:
        def collect(self, *args, **kwargs):
            pytest.fail("custom policies must not fall through to built-in collection")

        async def evaluate(self, ctx):
            calls.append(("evaluate", ctx.run_id))
            return ContractGateDecision(False, message="custom decision")

        async def finalize(self, ctx, prior_result=None):
            calls.append(("finalize", ctx.run_id))

        async def analyze(self, ctx):
            calls.append(("analyze", ctx.run_id))
            return "custom analysis"

    manager, registry = PluginManager(), HookRegistry()
    monkeypatch.setattr(plugins, "_default_manager", manager)
    monkeypatch.setattr(hooks, "_registry", registry)
    monkeypatch.setitem(sys.modules, "test_contract_policy",
                        SimpleNamespace(create=lambda **kwargs: Provider()))
    settings = Settings(_env_file=None, llm_api_key="test", llm_base_url="http://test/v1",
        llm_model_id="test", agent_design_contract_provider="test_contract_policy:create")
    install_plan(build_plan(manager, settings), registry)
    provider = load_contracts(settings=settings)
    gate = load_contract_gate(settings=settings, provider=provider)
    analyzer = load_contract_failure_analyzer(settings=settings, provider=provider)
    token = set_current_trace_sink(SimpleNamespace(event=lambda kind, **data: events.append(data)))
    async def run():
        assert (await gate.evaluate(context())).message == "custom decision"
        await gate.finalize(context())
        async def invoke(request):
            pytest.fail("custom analyzer must own model invocation policy")
        assert await analyzer.analyze(ContractFailureAnalysisContext(
            result=ContractCheckResult("c", "block", "p"), invoke=invoke, run_id="candidate")) == "custom analysis"
    try:
        asyncio.run(run())
    finally:
        reset_current_trace_sink(token)
    assert calls == [("evaluate", "candidate"), ("finalize", "candidate"), ("analyze", "candidate")]
    executed = {event.get("contribution_id") for event in events if event.get("status") == "executed"}
    assert {f"design_contract.interface.{name}" for name in ("evaluate", "finalize", "analyze")} <= executed


def test_builtin_gate_collects_through_declared_interfaces(tmp_path, monkeypatch):
    source = tmp_path / "src"
    source.mkdir()
    path = source / "service.py"
    path.write_text("class Service:\n    pass\n", encoding="utf-8")
    manager, registry = PluginManager(), HookRegistry()
    monkeypatch.setattr(plugins, "_default_manager", manager)
    monkeypatch.setattr(hooks, "_registry", registry)
    settings = Settings(_env_file=None, llm_api_key="test", llm_base_url="http://test/v1",
        llm_model_id="test", agent_knowledge_graph_enabled=False)
    install_plan(build_plan(manager, settings), registry)
    provider = load_contracts(settings=settings)
    events = []
    token = set_current_trace_sink(SimpleNamespace(event=lambda kind, **data: events.append(data)))
    async def run():
        gate = load_contract_gate(settings=settings, provider=provider)
        ctx = ContractGateContext({"workspace_root": str(tmp_path), "source_root": str(source)},
                                  (str(path),), emit_ok)
        decision = await gate.evaluate(ctx)
        assert decision.allowed and decision.result.status in {"pass", "warn"}
        assert decision.result.graph_status == "not_requested"
        await gate.finalize(ctx, decision.result)
    try:
        asyncio.run(run())
    finally:
        reset_current_trace_sink(token)
    executed = {event.get("contribution_id") for event in events if event.get("status") == "executed"}
    assert {f"design_contract.interface.{name}" for name in
            ("evaluate", "finalize", "collect_facts", "snapshot_from_facts")} <= executed


@pytest.mark.parametrize("status,allowed", [("pass", True), ("not_applicable", True),
                                         ("block", False), ("inconclusive", False), ("warn", True)])
def test_plugin_gate_preserves_status_policy_and_indexes_only_after_commit(monkeypatch, status, allowed):
    calls = []
    result = ContractCheckResult("check", status, "project")
    def check(self, manifest, **kwargs):
        calls.append(kwargs)
        return result
    monkeypatch.setattr("extensions.design_contract.gate.ContractHarness.check", check)
    collector, runner = object(), object()
    gate = DefaultContractGate(collector=collector, language_runner=runner)
    async def run():
        assert (await gate.evaluate(context())).allowed is allowed
        assert calls[0]["index_graph"] is False
        assert calls[0]["contract_provider"] is collector
        assert calls[0]["language_runner"] is runner
        if allowed:
            assert await gate.finalize(context(), result) is result
            assert calls[1]["index_graph"] is True
    asyncio.run(run())


@pytest.mark.parametrize("reply,allowed", [(' {"decision":"accept"}', True),
                                         ('{"decision":"reject"}', False), ("invalid", False), (None, False)])
def test_warning_review_is_a_callback_and_handles_rejection_or_timeout(monkeypatch, reply, allowed):
    monkeypatch.setattr("extensions.design_contract.gate.ContractHarness.check",
                        lambda *args, **kwargs: ContractCheckResult("c", "warn", "p"))
    prompts = []
    async def review(prompt):
        prompts.append(prompt)
        if reply is None:
            raise asyncio.TimeoutError
        return reply
    assert asyncio.run(DefaultContractGate().evaluate(context(request_review=review))).allowed is allowed
    assert prompts[0].review_type == "design_contract"
    assert prompts[0].metadata["check_id"] == "c"


def test_gate_does_not_commit_when_check_event_cannot_be_delivered(monkeypatch):
    monkeypatch.setattr("extensions.design_contract.gate.ContractHarness.check",
                        lambda *args, **kwargs: ContractCheckResult("c", "pass", "p"))
    async def reject(event):
        return False
    ctx = ContractGateContext({}, ("one.py",), reject)
    assert not asyncio.run(DefaultContractGate().evaluate(ctx)).allowed


def test_disabled_and_unavailable_capabilities_have_distinct_outcomes():
    provider = SimpleNamespace(collect=lambda *args: None)
    disabled = load_contract_gate(settings=SimpleNamespace(agent_design_contract_enabled=False), provider=provider)
    unavailable = load_contract_gate(settings=SimpleNamespace(agent_design_contract_enabled=True), provider=provider)
    assert asyncio.run(disabled.evaluate(context())).allowed
    decision = asyncio.run(unavailable.evaluate(context()))
    assert not decision.allowed and decision.result.status == "inconclusive"
    assert asyncio.run(unavailable.evaluate(context(contract_enabled=False))).allowed
    async def invoke(request):
        pytest.fail("missing analysis capability must not invoke a model")
    analysis = load_contract_failure_analyzer(provider=provider)
    assert asyncio.run(analysis.analyze(ContractFailureAnalysisContext(
        decision.result, invoke, message="factual failure"))) == "factual failure"


def test_review_adapter_preserves_transport_events_and_timeout():
    async def run():
        events = []
        async def emit(event):
            events.append(event)
        future = asyncio.get_running_loop().create_future()
        def submit(**kwargs):
            return SimpleNamespace(id="review", future=future, **kwargs)
        adapter = ReviewAdapter(SimpleNamespace(submit=submit), emit)
        with pytest.raises(asyncio.TimeoutError):
            await adapter.ask(ReviewPrompt("kind", "title", "content", "question", 0.001, {"key": 1}))
        assert [event["event"] for event in events] == ["review", "review_timeout"]
        assert events[0]["metadata"] == {"key": 1}
    asyncio.run(run())
