"""Plugins consume replaceable capabilities with request and task isolation."""
import asyncio
from types import SimpleNamespace

import pytest

from app.agent_base.adapters.host_services import ApplicationHostServices
from app.agent_base.core.extension_context import ExtensionContext, extension_scope
from app.agent_base.core.hooks import get_runtime
from app.agent_base.core.observability import emit_trace, get_trace_hook, push_trace_hook, pop_trace_hook
from app.agent_base.host_api.services import get_host_services, host_services_scope
from extensions.memory.provider import SQLiteMemoryProvider


def test_concurrent_plugins_and_background_children_keep_injected_capabilities():
    default = get_host_services()
    events = []

    async def run():
        async def request(name):
            host = SimpleNamespace(emit_event=lambda event, payload: events.append((name, event, payload)))
            with host_services_scope(host):
                async def child():
                    await asyncio.sleep(0)
                    SQLiteMemoryProvider._trace("child", request=name)
                    assert get_host_services() is host
                task = asyncio.create_task(child())
                await asyncio.sleep(0)
                SQLiteMemoryProvider._trace("foreground", request=name)
                await task
        await asyncio.gather(request("first"), request("second"))

    asyncio.run(run())
    assert get_host_services() is default
    assert {(name, event) for name, event, _ in events} == {
        (name, event) for name in ("first", "second") for event in ("foreground", "child")}
    assert all(payload["request"] == name for name, _, payload in events)


def test_extension_request_fork_preserves_services_and_restores_on_error():
    default = get_host_services()
    injected = SimpleNamespace()
    context = ExtensionContext(host_services=injected)
    fork = context.fork()
    assert fork.host_services is injected
    with pytest.raises(ValueError):
        with extension_scope(fork):
            assert get_host_services() is injected
            raise ValueError("request failed")
    assert get_host_services() is default


def test_host_runtime_scope_and_trace_suppression_restore_parent_bindings():
    host = ApplicationHostServices()
    parent_runtime = get_runtime()
    with host.runtime_scope() as child:
        assert child is get_runtime() and child is not parent_runtime
        child.policy_metadata["example"] = True
    assert get_runtime() is parent_runtime
    events = []
    def observer(kind, **kwargs):
        events.append((kind, kwargs))
    push_trace_hook(observer)
    try:
        with pytest.raises(ValueError):
            with host.suppress_tracing():
                host.emit_event("suppressed", {})
                raise ValueError("replay failed")
        assert get_trace_hook() is observer
        emit_trace("event", event_type="restored", payload={})
        assert events == [("event", {"event_type": "restored", "payload": {}})]
    finally:
        pop_trace_hook(observer)
