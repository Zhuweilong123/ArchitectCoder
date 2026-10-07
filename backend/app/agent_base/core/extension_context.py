"""Scoped provider capabilities and private extension state.

The host binds providers at assembly and publishes data through lifecycle hooks.
Handlers access their provider without receiving an Agent or tool registry.
"""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any
from functools import wraps
import inspect
from app.agent_base.host_api.services import HostServices, host_services_scope


@dataclass
class ExtensionContext:
    providers: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    _states: dict[str, dict] = field(default_factory=dict, repr=False)
    _contributions: list = field(default_factory=list, repr=False)
    _registry: Any = field(default=None, repr=False)
    host_services: HostServices | None = field(default=None, repr=False)

    def bind(self, name, provider, *, settings=None, options=None):
        """Bind a capability and its declared handlers for standalone use too."""
        from .plugins import get_plugin_manager
        from .lifecycle import declared_contributions, _ordered
        manager = get_plugin_manager()
        spec = manager.get_spec(name)
        declared = declared_contributions(spec, manager, settings)
        self._contributions = list(_ordered([
            *(pair for pair in self._contributions if pair[0] != name),
            *((name, item) for item in declared),
        ]))
        self.providers[name] = provider
        self.metadata.setdefault("plugin_options", {})[name] = dict(options or {})
        self._registry = None

    def state(self, name):
        return self._states.setdefault(name, {})

    def fork(self):
        """Start a request with shared capabilities and fresh private state."""
        import copy
        return ExtensionContext(dict(self.providers), copy.deepcopy(self.metadata),
                                _contributions=list(self._contributions), _registry=self._registry,
                                host_services=self.host_services)

    def hooks_for(self, fallback):
        if self._registry is not None and self._registry.plan_id:
            return self._registry
        if fallback.plan_id or not self._contributions:
            return fallback
        if self._registry is None:
            from .hooks import HookRegistry
            from .lifecycle import resolve_contribution
            registry = HookRegistry()
            registry._hooks = {stage: list(items) for stage, items in fallback._hooks.items()}
            registry._metadata = dict(fallback._metadata)
            for order, (plugin, item) in enumerate(self._contributions):
                registry.register(item.stage, resolve_contribution(item), mode=item.mode,
                    priority=len(self._contributions) - order, fail_closed=item.fail_closed,
                    contribution_id=item.id, plugin=plugin, interface_id=item.interface_id)
            self._registry = registry
        return self._registry


_context: ContextVar[ExtensionContext | None] = ContextVar("extension_context", default=None)


def current_extension_context():
    return _context.get()


@contextmanager
def extension_scope(context=None):
    token = _context.set(context)
    try:
        services = getattr(context, "host_services", None)
        with host_services_scope(services) if services is not None else nullcontext():
            yield context
    finally:
        _context.reset(token)


def extension_request(function):
    """Pin request state through execution and later authoritative completion."""
    signature = inspect.signature(function)
    @wraps(function)
    async def wrapped(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        agent = bound.arguments["agent"]
        base = getattr(agent, "extension_context", None)
        session = base.fork() if base else None
        run_id = bound.arguments.get("run_id", "")
        if session is not None:
            session.metadata["run_id"] = run_id
            pending = getattr(agent, "extension_runs", None)
            if pending is None:
                pending = agent.extension_runs = {}
            pending[run_id] = session
            while len(pending) > 32:
                pending.pop(next(iter(pending)))
        with extension_scope(session):
            try:
                return await function(*args, **kwargs)
            finally:
                # Keep only requests actually awaiting a later review result.
                status = session.metadata.get("task_status") if session else None
                status = status or getattr(agent, "last_run_checkpoint", {}).get("status")
                if status != "waiting_approval":
                    getattr(agent, "extension_runs", {}).pop(run_id, None)
    return wrapped


async def publish_task_result(agent, *, run_id="", **payload):
    """Publish the post-check/review outcome without selecting any consumer."""
    from .hooks import HookContext, HookEvent, get_hooks
    session = getattr(agent, "extension_runs", {}).get(run_id) or current_extension_context()
    if session is None:
        base = getattr(agent, "extension_context", None)
        session = base.fork() if base else None
    if session is not None:
        session.metadata["task_status"] = payload.get("status", "")
    with extension_scope(session):
        await get_hooks().aemit(HookEvent.TASK_AFTER, HookContext(
            HookEvent.TASK_AFTER, getattr(agent, "name", "Agent"), run_id=run_id, payload=payload,
        ))
    if payload.get("status") != "waiting_approval":
        getattr(agent, "extension_runs", {}).pop(run_id, None)
