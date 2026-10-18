"""Scoped provider capabilities and private extension state.

The host binds providers at assembly and publishes data through lifecycle hooks.
Handlers access their provider without receiving an Agent or tool registry.
"""
from __future__ import annotations
from app.agent_base.host_api.execution_types import TaskResultPayload
from typing import Unpack, TYPE_CHECKING

from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from app.agent_base.host_api.configuration import PluginConfiguration
from typing import Awaitable, Callable, Iterator, ParamSpec, TypedDict, Mapping, TypeVar, cast
from functools import wraps
import inspect
from app.agent_base.host_api.services import host_services_scope
from app.agent_base.host_api.data import JsonObject, WorkspaceSnapshot
from app.agent_base.host_api.lifecycle import Contribution
from app.agent_base.host_api.payloads import json_object

if TYPE_CHECKING:
    from .hooks import HookRegistry


ProviderT = TypeVar("ProviderT")
StateT = TypeVar("StateT")
P = ParamSpec("P")
R = TypeVar("R")

class ExtensionMetadata(TypedDict, total=False):
    execution_settings: PluginConfiguration
    project_id: str
    workspace: WorkspaceSnapshot
    plugin_options: dict[str, JsonObject]
    required_execution_bindings: dict[str, list[str]]
    run_id: str
    task_status: str



@dataclass
class ExtensionContext:
    # Heterogeneous bindings are narrowed only at the consuming plugin's contract.
    providers: dict[str, object] = field(default_factory=dict)
    metadata: ExtensionMetadata = field(default_factory=lambda: ExtensionMetadata())
    _states: dict[str, object] = field(default_factory=dict, repr=False)
    _contributions: list[tuple[str, Contribution]] = field(default_factory=list, repr=False)
    _registry: HookRegistry | None = field(default=None, repr=False)
    _registry_pinned: bool = field(default=False, repr=False)
    _runs: dict[str, ExtensionContext] = field(default_factory=dict, repr=False)
    host_services: object | None = field(default=None, repr=False)

    def bind(self, name: str, provider: object, *, settings: PluginConfiguration | None = None,
             options: JsonObject | None = None) -> None:
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
        self.metadata.setdefault("plugin_options", {})[name] = deepcopy(options or {})
        self._registry = None
        self._registry_pinned = False

    @property
    def project_id(self) -> str:
        return str(self.metadata.get("project_id") or "")

    def provider(self, name: str, contract: type[ProviderT]) -> ProviderT | None:
        """Read a scoped binding through the consuming plugin's public contract."""
        return cast(ProviderT | None, self.providers.get(name))

    def provider_options(self, name: str) -> Mapping[str, object]:
        """Return detached options; consumers cannot mutate request bindings."""
        from types import MappingProxyType
        return MappingProxyType(deepcopy(self.metadata.get("plugin_options", {}).get(name, {})))

    def state(self, name: str, state_type: type[StateT]) -> StateT:
        """Create plugin-owned state once and reject conflicting state contracts."""
        if name not in self._states:
            self._states[name] = state_type()
        value = self._states[name]
        if not isinstance(value, state_type):
            raise TypeError(f"extension state {name!r} does not match {state_type.__name__}")
        return value

    def fork(self) -> ExtensionContext:
        """Start a request with shared capabilities and fresh private state."""
        return ExtensionContext(dict(self.providers), deepcopy(self.metadata),
                                _contributions=list(self._contributions),
                                _registry=self._registry.copy() if self._registry is not None else None,
                                _registry_pinned=self._registry_pinned,
                                host_services=self.host_services)

    def start_run(self, run_id: str) -> ExtensionContext:
        session = self.fork()
        session.metadata["run_id"] = run_id
        self._runs[run_id] = session
        while len(self._runs) > 32:
            self._runs.pop(next(iter(self._runs)))
        return session

    def run_context(self, run_id: str) -> ExtensionContext | None:
        return self._runs.get(run_id)

    def release_run(self, run_id: str, session: ExtensionContext) -> None:
        """Release this execution without discarding a replacement with the same ID."""
        if self._runs.get(run_id) is session:
            self._runs.pop(run_id)

    def bind_hooks(self, registry: HookRegistry) -> None:
        """Pin an independent binding snapshot for this context and its forks."""
        self._registry = registry.copy()
        self._registry_pinned = True

    def hooks_for(self, fallback: HookRegistry) -> HookRegistry:
        if self._registry is not None and (self._registry_pinned or self._registry.plan_id):
            return self._registry
        if fallback.plan_id or not self._contributions:
            return fallback
        if self._registry is None:
            from .lifecycle import resolve_contribution
            registry = fallback.copy()
            for order, (plugin, item) in enumerate(self._contributions):
                registry.register(item.stage, resolve_contribution(item), mode=item.mode,
                    priority=len(self._contributions) - order, fail_closed=item.fail_closed,
                    contribution_id=item.id, plugin=plugin, interface_id=item.interface_id)
            self._registry = registry
        return self._registry


_context: ContextVar[ExtensionContext | None] = ContextVar("extension_context", default=None)


def current_extension_context() -> ExtensionContext | None:
    return _context.get()


@contextmanager
def extension_scope(context: ExtensionContext | None = None) -> Iterator[ExtensionContext | None]:
    token = _context.set(context)
    try:
        services = context.host_services if context is not None else None
        with host_services_scope(services) if services is not None else nullcontext():
            yield context
    finally:
        _context.reset(token)


def _agent_context(agent: object) -> ExtensionContext | None:
    """Adapt optional extension support at the host boundary, never in plugins."""
    context = getattr(agent, "extension_context", None)
    if context is not None and not isinstance(context, ExtensionContext):
        raise TypeError("agent.extension_context must be an ExtensionContext")
    return context


def extension_request(function: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    """Pin request state through execution and later authoritative completion."""
    signature = inspect.signature(function)
    @wraps(function)
    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        agent = bound.arguments["agent"]
        base = _agent_context(agent)
        run_id = bound.arguments.get("run_id", "")
        if not isinstance(run_id, str):
            raise TypeError("run_id must be a string")
        session = base.start_run(run_id) if base is not None else None
        with extension_scope(session):
            try:
                return await function(*args, **kwargs)
            finally:
                # Keep only requests actually awaiting a later review result.
                status = session.metadata.get("task_status") if session else None
                checkpoint = getattr(agent, "last_run_checkpoint", {})
                status = status or (checkpoint.get("status") if isinstance(checkpoint, dict) else None)
                if status != "waiting_approval" and base is not None and session is not None:
                    base.release_run(run_id, session)
    return wrapped


async def publish_task_result(agent: object, *, run_id: str = "", **payload: Unpack[TaskResultPayload]) -> None:
    """Publish the post-check/review outcome without selecting any consumer."""
    from .hooks import get_hooks
    from app.agent_base.host_api.lifecycle import HookContext, HookEvent
    base = _agent_context(agent)
    session = base.run_context(run_id) if base is not None else None
    current = current_extension_context()
    if session is None and current is not None and current.metadata.get("run_id", "") == run_id:
        session = current
    if session is None:
        session = base.fork() if base else None
    if session is not None:
        session.metadata["run_id"] = run_id
        session.metadata["task_status"] = payload.get("status", "")
    try:
        with extension_scope(session):
            await get_hooks().aemit(HookEvent.TASK_AFTER, HookContext(
                HookEvent.TASK_AFTER, getattr(agent, "name", "Agent"), run_id=run_id,
                payload=json_object(payload),
            ))
    finally:
        if payload.get("status") != "waiting_approval" and base is not None and session is not None:
            base.release_run(run_id, session)
