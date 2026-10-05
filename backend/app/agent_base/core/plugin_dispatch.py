"""Per-interface scheduling; provider instances stay local to their callers."""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from functools import wraps

from .hooks import HookContext, HookEvent, HookRegistry, PUBLIC_STAGES, get_hooks, get_runtime
from .operations import current_operation, operation_scope

# Optional public capabilities are listed without instantiating a provider.
OPTIONAL_INTERFACES = {
    "orchestration": ("create_tools", "explore"),
    "knowledge_graph": ("contract_facts", "index_facts", "sync_facts", "create_tools"),
    "design_contract": ("collect_facts", "snapshot_from_facts"),
    "trace": ("query", "replay", "list_traces", "read_trace", "summarize_trace", "reconstruct_history"),
    "evals": ("get_baseline", "start_batch", "merge_batches", "list_batches", "get_batch", "delete_batch", "trends",
              "archive", "archive_baseline", "list_archives", "list_performance_results", "get_performance_result",
              "delete_performance_result", "archive_performance_result", "list_trace_case_projects", "list_trace_case_drafts",
              "delete_trace_case_draft", "create_trace_case_draft", "get_trace_case_draft", "review_trace_case_draft",
              "capture_trace_case_fixture", "preview_trace_case_fixture", "validate_trace_case_draft", "publish_trace_case_draft"),
}

ASYNC_INTERFACES = {
    "memory": {"recall", "archive", "reinforce"},
    "orchestration": {"prepare", "explore"},
    "trace": {"replay"},
    "evals": {"run_case", "start_batch", "create_trace_case_draft", "validate_trace_case_draft"},
}


def interface_stage(plugin, method):
    if method == "create_tools":
        return HookEvent.AGENT_INITIALIZE
    if plugin == "skills":
        return HookEvent.AGENT_INITIALIZE if method == "list_skills" else HookEvent.SKILL_READ
    if plugin == "memory":
        return {"recall": HookEvent.CONTEXT_PREPARE, "reinforce": HookEvent.MEMORY_REINFORCE,
                "archive": HookEvent.TASK_ARCHIVE}[method]
    if plugin == "orchestration":
        return HookEvent.ORCHESTRATION_EXECUTE if method == "explore" else HookEvent.ORCHESTRATION_PREPARE
    if plugin == "trace":
        return HookEvent.TRACE_INITIALIZE if method == "create" else HookEvent.TRACE_REPLAY if method == "replay" else HookEvent.TRACE_QUERY
    if plugin == "knowledge_graph":
        return HookEvent.GRAPH_UPDATE if method in {"rebuild_project", "index_facts", "sync_facts"} else HookEvent.GRAPH_QUERY
    if plugin == "design_contract":
        return HookEvent.CONTRACT_COLLECT
    if plugin == "evals":
        if method in {"run_case", "start_batch", "validate_trace_case_draft"}:
            return HookEvent.EVALUATION_RUN
        if method.startswith(("list_", "get_", "preview_")) or method == "trends":
            return HookEvent.EVALUATION_QUERY
        return HookEvent.EVALUATION_UPDATE
    return HookEvent.PLUGIN_SERVICE


SERVICE_STAGES = frozenset(PUBLIC_STAGES)


@dataclass
class Invocation:
    interface_id: str
    target: object
    args: tuple
    kwargs: dict
    result: object = None
    executed: bool = False
    asynchronous: bool = False


def invoke_provider(context):
    call = context.invocation
    if call is None or call.executed:
        raise RuntimeError("missing or repeated provider invocation")
    call.executed = True
    if call.asynchronous:
        async def finish():
            result = call.target(*call.args, **call.kwargs)
            call.result = await result if inspect.isawaitable(result) else result
        return finish()
    call.result = call.target(*call.args, **call.kwargs)
    if inspect.isawaitable(call.result):
        close = getattr(call.result, "close", None)
        if close is not None:
            close()
        raise TypeError("synchronous provider interface returned an awaitable")


def service_contributions(spec):
    from .lifecycle import Contribution
    methods = tuple(dict.fromkeys((*spec.required_methods, *OPTIONAL_INTERFACES.get(spec.name, ()))))
    stages = dict(spec.interface_stages)
    return tuple(Contribution(f"{spec.name}.interface.{method}", HookEvent(stages[method]) if method in stages else interface_stage(spec.name, method),
                             "app.agent_base.core.plugin_dispatch:invoke_provider", mode="service",
                             interface_id=f"{spec.name}.{method}", scope="invocation") for method in methods)


def schedule_tool_provider(provider, name):
    """Keep extension-created tools on the same scheduler as their parent provider."""
    if isinstance(provider, ScheduledProvider):
        return provider
    from .plugins import get_plugin_manager
    spec = next(spec for spec in get_plugin_manager().specs if spec.name == name)
    return ScheduledProvider(provider, spec)


class ScheduledProvider:
    """Route declared capabilities through their compiled contribution bindings."""
    def __init__(self, provider, spec):
        self._provider = provider
        self._spec = spec
        self._bindings = {item.interface_id.split(".", 1)[1]: item for item in service_contributions(spec)}

    def __getattr__(self, name):
        target = getattr(self._provider, name)
        binding = self._bindings.get(name)
        if binding is None or not callable(target):
            if callable(target) and not name.startswith("_") and name not in {"close", "aclose"}:
                raise RuntimeError(f"undeclared plugin interface: {self._spec.name}.{name}")
            return target
        asynchronous = inspect.iscoroutinefunction(target) or name in ASYNC_INTERFACES.get(self._spec.name, ())

        def prepare(args, kwargs):
            registry = get_hooks()
            if not registry.plan_id:
                # CLI/library use has no app lifespan; use the same declared binding locally.
                registry = HookRegistry()
                registry.register(binding.stage, invoke_provider, contribution_id=binding.id,
                                  plugin=self._spec.name, mode="service", interface_id=binding.interface_id)
            elif not registry.has_contribution(binding.id):
                raise RuntimeError(f"plugin interface is not in the active execution plan: {binding.interface_id}")
            runtime = get_runtime()
            call = Invocation(binding.interface_id, target, args, kwargs, asynchronous=asynchronous)
            request = args[0] if args else None
            parent = current_operation()
            run_id = getattr(request, "run_id", "") or runtime.run_id or (parent.run_id if parent else "")
            context = HookContext(binding.stage, self._spec.name, run_id=run_id, runtime=runtime,
                                  payload={"interface_id": binding.interface_id}, invocation=call)
            return registry, context

        def result(context):
            if not context.invocation.executed:
                raise RuntimeError(f"plugin interface was not executed: {binding.interface_id}")
            value = context.invocation.result
            # Read-side trace adapters are public plugin capabilities too. Sinks are
            # intentionally left unwrapped to avoid recursive instrumentation.
            return ScheduledProvider(value, self._spec) if self._spec.name == "trace" and name == "query" else value

        def interval(context):
            parent = current_operation()
            return operation_scope("plugin", run_id=context.run_id,
                stage=parent.stage if parent else binding.stage.value,
                scope=parent.scope if parent else "run" if context.run_id else "agent" if binding.stage == HookEvent.INITIALIZE else "request",
                plugin=self._spec.name, interface_id=binding.interface_id, contribution_id=binding.id)

        if asynchronous:
            @wraps(target)
            async def asynchronous(*args, **kwargs):
                registry, context = prepare(args, kwargs)
                with interval(context):
                    await registry.ainvoke(context)
                    return result(context)
            return asynchronous
        @wraps(target)
        def synchronous(*args, **kwargs):
            registry, context = prepare(args, kwargs)
            with interval(context):
                registry.invoke(context)
                return result(context)
        return synchronous
