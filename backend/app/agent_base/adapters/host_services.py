"""Bind the public plugin capability contract to the host execution machinery."""
from contextlib import contextmanager


class ApplicationHostServices:
    def emit_event(self, event_type, payload):
        from app.agent_base.core.observability import emit_trace
        emit_trace("event", event_type=event_type, payload=payload)

    def record_event(self, event_type, **payload):
        from app.agent_base.core.observability import current_trace_sink
        sink = current_trace_sink()
        if sink is not None:
            sink.event(event_type, **payload)

    def trace_span(self, name):
        from app.agent_base.core.observability import trace_span
        return trace_span(name)

    @contextmanager
    def suppress_tracing(self):
        from app.agent_base.core.observability import push_trace_hook, pop_trace_hook
        def ignore(*args, **kwargs):
            return None
        push_trace_hook(ignore)
        try:
            yield
        finally:
            pop_trace_hook(ignore)

    def runtime(self):
        from app.agent_base.core.hooks import get_runtime
        return get_runtime()

    @contextmanager
    def runtime_scope(self):
        from app.agent_base.core.hooks import AgentRuntime, set_runtime, reset_runtime
        runtime = AgentRuntime()
        token = set_runtime(runtime)
        try:
            yield runtime
        finally:
            reset_runtime(token)

    def extension_context(self):
        from app.agent_base.core.extension_context import current_extension_context
        return current_extension_context()

    def current_operation(self):
        from app.agent_base.core.operations import current_operation
        return current_operation()

    def operation_scope(self, kind, **kwargs):
        from app.agent_base.core.operations import operation_scope
        return operation_scope(kind, **kwargs)

    def submit_background(self, work, **kwargs):
        from app.agent_base.core.background_tasks import submit_background
        return submit_background(work, **kwargs)

    async def publish(self, stage, *, agent_name, run_id, payload):
        from app.agent_base.host_api.lifecycle import HookContext
        from app.agent_base.core.hooks import get_hooks
        await get_hooks().aemit(stage, HookContext(stage, agent_name, run_id=run_id, payload=payload))

    def install_contributions(self, contributions, *, plugin):
        from app.agent_base.core.hooks import get_hooks
        from app.agent_base.core.lifecycle import resolve_contribution, validate_contributions
        contributions = tuple(contributions)
        validate_contributions(contributions)
        hooks = get_hooks()
        for item in contributions:
            if not hooks.has_contribution(item.id):
                hooks.register(item.stage, resolve_contribution(item), priority=item.priority,
                    contribution_id=item.id, plugin=plugin, mode=item.mode,
                    fail_closed=item.fail_closed, interface_id=item.interface_id)

    def schedule_provider(self, provider, plugin):
        from app.agent_base.core.plugin_dispatch import schedule_tool_provider
        return schedule_tool_provider(provider, plugin)

    def create_model(self, **kwargs):
        from app.agent_base.core.llm import BaseAgentsLLM
        return BaseAgentsLLM.from_settings(**kwargs)
