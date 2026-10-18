"""Per-interface scheduling; provider instances stay local to their callers."""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from functools import wraps
from typing import Awaitable, Callable, ContextManager, TypeVar, cast
ProviderT = TypeVar("ProviderT")

from .hooks import HookRegistry, get_hooks, get_runtime
from ..host_api.lifecycle import Contribution, HookContext, HookEvent, PUBLIC_STAGES
from .operations import Operation, current_operation, operation_scope

# Compatibility views generated from plugin-owned declarations.
from .plugins import DEFAULT_PLUGIN_SPECS, PluginSpec
from .plugin_types import PluginCallable
OPTIONAL_INTERFACES = {spec.name: spec.optional_methods for spec in DEFAULT_PLUGIN_SPECS if spec.optional_methods}
ASYNC_INTERFACES = {spec.name: frozenset(spec.async_methods) for spec in DEFAULT_PLUGIN_SPECS if spec.async_methods}


SERVICE_STAGES = frozenset(PUBLIC_STAGES)


@dataclass
class Invocation:
    """A bound provider call; heterogeneous values stay inside this dynamic adapter."""
    interface_id: str
    target: Callable[[], object]
    result: object = None
    executed: bool = False
    asynchronous: bool = False


def invoke_provider(context: HookContext) -> Awaitable[None] | None:
    call = context.invocation
    if not isinstance(call, Invocation) or call.executed:
        raise RuntimeError("missing or repeated provider invocation")
    call.executed = True
    if call.asynchronous:
        async def finish() -> None:
            result = call.target()
            call.result = await result if inspect.isawaitable(result) else result
        return finish()
    call.result = call.target()
    if inspect.isawaitable(call.result):
        close = getattr(call.result, "close", None)
        if close is not None:
            close()
        raise TypeError("synchronous provider interface returned an awaitable")
    return None


def service_contributions(spec: PluginSpec) -> tuple[Contribution, ...]:
    methods = tuple(dict.fromkeys((*spec.required_methods, *spec.optional_methods)))
    stages = dict(spec.interface_stages)
    return tuple(Contribution(f"{spec.name}.interface.{method}", HookEvent(stages.get(method, "run_start")),
                             "app.agent_base.core.plugin_dispatch:invoke_provider", mode="service",
                             interface_id=f"{spec.name}.{method}", scope="invocation") for method in methods)


def schedule_tool_provider(provider: ProviderT, name: str) -> ProviderT:
    """Keep extension-created tools on the same scheduler as their parent provider."""
    if isinstance(provider, ScheduledProvider):
        return provider
    from .plugins import get_plugin_manager
    spec = get_plugin_manager().get_spec(name)
    return cast(ProviderT, ScheduledProvider(provider, spec))


class ScheduledProvider:
    """Route declared capabilities through their compiled contribution bindings."""
    def __init__(self, provider: object, spec: PluginSpec) -> None:
        self._provider = provider
        self._spec = spec
        self._bindings = {item.interface_id.split(".", 1)[1]: item for item in service_contributions(spec)}

    def __getattr__(self, name: str) -> object:
        target: object = getattr(self._provider, name)
        binding = self._bindings.get(name)
        if binding is None or not callable(target):
            if callable(target) and not name.startswith("_") and name not in {"close", "aclose"}:
                raise RuntimeError(f"undeclared plugin interface: {self._spec.name}.{name}")
            return target
        asynchronous = inspect.iscoroutinefunction(target) or name in self._spec.async_methods
        # Signatures belong to each plugin's public port. Only this adapter erases
        # them to route arbitrary declared interfaces through one scheduler.
        method = cast(PluginCallable, target)

        def prepare(args: tuple[object, ...], kwargs: dict[str, object]) -> tuple[HookRegistry, HookContext, Invocation]:
            registry = get_hooks()
            if not registry.plan_id:
                # CLI/library use has no app lifespan; use the same declared binding locally.
                registry = HookRegistry()
                registry.register(binding.stage, invoke_provider, contribution_id=binding.id,
                                  plugin=self._spec.name, mode="service", interface_id=binding.interface_id,
                                  plugin_version=self._spec.version, plugin_revision=self._spec.revision)
            elif not registry.has_contribution(binding.id, interface_id=binding.interface_id):
                raise RuntimeError(f"plugin interface is not in the active execution plan: {binding.interface_id}")
            runtime = get_runtime()
            call = Invocation(binding.interface_id, lambda: method(*args, **kwargs), asynchronous=asynchronous)
            request = args[0] if args else None
            parent = current_operation()
            run_id = getattr(request, "run_id", "") or runtime.run_id or (parent.run_id if parent else "")
            context = HookContext(binding.stage, self._spec.name, run_id=run_id, runtime=runtime,
                                  payload={"interface_id": binding.interface_id}, invocation=call)
            return registry, context, call

        def result(call: Invocation) -> object:
            if not call.executed:
                raise RuntimeError(f"plugin interface was not executed: {binding.interface_id}")
            value = call.result
            # Read-side trace adapters are public plugin capabilities too. Sinks are
            # intentionally left unwrapped to avoid recursive instrumentation.
            return ScheduledProvider(value, self._spec) if name in self._spec.wrapped_results else value

        def interval(context: HookContext) -> ContextManager[Operation]:
            parent = current_operation()
            return operation_scope("plugin", run_id=context.run_id,
                stage=parent.stage if parent else binding.stage.value,
                scope=parent.scope if parent else "run" if context.run_id else "agent" if binding.stage == HookEvent.INITIALIZE else "request",
                plugin=self._spec.name, interface_id=binding.interface_id, contribution_id=binding.id)

        if asynchronous:
            @wraps(target)
            async def invoke_async(*args: object, **kwargs: object) -> object:
                registry, context, call = prepare(args, kwargs)
                with interval(context):
                    await registry.ainvoke(context)
                    return result(call)
            return invoke_async
        @wraps(target)
        def synchronous(*args: object, **kwargs: object) -> object:
            registry, context, call = prepare(args, kwargs)
            with interval(context):
                registry.invoke(context)
                return result(call)
        return synchronous
