"""Request state and hook bindings must not leak across extension executions."""
import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from app.agent_base.core.extension_context import (
    ExtensionContext, current_extension_context, extension_request, extension_scope,
    publish_task_result,
)
from app.agent_base.core.hooks import HookRegistry
from app.agent_base.host_api.lifecycle import HookContext, HookEvent


@dataclass
class RequestState:
    evidence: list[str] = field(default_factory=list)


def test_state_contract_rejects_collisions_and_fork_has_fresh_state():
    base = ExtensionContext(metadata={"workspace": {"workspace_roots": ["original"]}})
    state = base.state("plugin", RequestState)
    state.evidence.append("parent")
    assert base.state("plugin", RequestState) is state
    with pytest.raises(TypeError, match="does not match"):
        base.state("plugin", list)
    fork = base.fork()
    fork.state("plugin", RequestState).evidence.append("child")
    fork.metadata["workspace"]["workspace_roots"].append("child")
    assert state.evidence == ["parent"]
    assert base.metadata["workspace"]["workspace_roots"] == ["original"]


@pytest.mark.parametrize("plan_id", ["", "compiled-plan"])
def test_bound_hooks_and_forks_are_independent_snapshots(plan_id):
    registry = HookRegistry()
    registry.plan_id = plan_id
    calls = []

    def observe(context):
        calls.append(context.run_id)

    registry.register(HookEvent.TASK_AFTER, observe, mode="observer", contribution_id="plugin.observe")
    base = ExtensionContext()
    base.bind_hooks(registry)
    first, second = base.fork(), base.fork()
    registry.unregister(HookEvent.TASK_AFTER, observe)
    first.hooks_for(registry).unregister(HookEvent.TASK_AFTER, observe)
    second_hooks = second.hooks_for(registry)
    second_hooks.emit(HookEvent.TASK_AFTER, HookContext(HookEvent.TASK_AFTER, "test", run_id="second"))
    assert calls == ["second"]
    assert second_hooks.plan_id == plan_id
    assert second_hooks.has_contribution("plugin.observe")
    assert base.hooks_for(registry).has_contribution("plugin.observe")
    assert not first.hooks_for(registry).has_contribution("plugin.observe")


def test_request_uses_default_run_id_and_keeps_review_state_on_context():
    base = ExtensionContext()
    agent = SimpleNamespace(extension_context=base)

    @extension_request
    async def execute(agent, run_id="default-run"):
        session = current_extension_context()
        assert session is base.run_context(run_id)
        assert session.metadata["run_id"] == run_id
        session.state("plugin", RequestState).evidence.append("before-review")
        session.metadata["task_status"] = "waiting_approval"

    asyncio.run(execute(agent))
    assert base.run_context("default-run").state("plugin", RequestState).evidence == ["before-review"]
    assert not hasattr(agent, "extension_runs")
    assert current_extension_context() is None


@pytest.mark.parametrize("error", [ValueError, asyncio.CancelledError])
def test_failed_request_releases_state_and_restores_outer_scope(error):
    base, outer = ExtensionContext(), ExtensionContext()
    agent = SimpleNamespace(extension_context=base)

    @extension_request
    async def execute(agent, run_id):
        assert base.run_context(run_id) is current_extension_context()
        raise error()

    with extension_scope(outer):
        with pytest.raises(error):
            asyncio.run(execute(agent, "failed"))
        assert current_extension_context() is outer
    assert base.run_context("failed") is None


def test_concurrent_requests_release_only_their_own_state():
    base = ExtensionContext()
    agent = SimpleNamespace(extension_context=base)

    @extension_request
    async def execute(agent, run_id):
        session = current_extension_context()
        session.state("plugin", RequestState).evidence.append(run_id)
        await asyncio.sleep(0)
        assert current_extension_context() is session
        if run_id == "review":
            session.metadata["task_status"] = "waiting_approval"

    async def run():
        await asyncio.gather(execute(agent, "review"), execute(agent, "finished"))

    asyncio.run(run())
    assert base.run_context("finished") is None
    assert base.run_context("review").state("plugin", RequestState).evidence == ["review"]
    assert base.state("plugin", RequestState).evidence == []


def test_old_execution_cannot_release_replacement_with_same_id():
    base = ExtensionContext()
    first = base.start_run("run")
    replacement = base.start_run("run")
    base.release_run("run", first)
    assert base.run_context("run") is replacement


def test_terminal_notification_releases_review_state_even_when_cancelled():
    base = ExtensionContext()
    session = base.start_run("review")
    session.metadata["task_status"] = "waiting_approval"
    registry = HookRegistry()

    async def cancel(context):
        assert current_extension_context() is session
        raise asyncio.CancelledError()

    registry.register(HookEvent.TASK_AFTER, cancel)
    session.bind_hooks(registry)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(publish_task_result(SimpleNamespace(extension_context=base),
                                      run_id="review", status="completed"))
    assert base.run_context("review") is None
    assert current_extension_context() is None


def test_result_for_another_run_does_not_reuse_active_state():
    base = ExtensionContext()
    current = base.start_run("active")
    current.state("plugin", RequestState).evidence.append("active-only")
    registry = HookRegistry()
    observed = []

    def observe(context):
        session = current_extension_context()
        observed.append((session.metadata["run_id"], session.state("plugin", RequestState).evidence))

    registry.register(HookEvent.TASK_AFTER, observe)
    base.bind_hooks(registry)
    with extension_scope(current):
        asyncio.run(publish_task_result(SimpleNamespace(extension_context=base),
                                      run_id="other", status="completed"))
    assert observed == [("other", [])]
    assert "task_status" not in current.metadata
    assert base.run_context("active") is current
