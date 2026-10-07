"""Bind the public plugin capability contract to the host execution machinery."""
from contextlib import contextmanager


class ApplicationHostServices:
    def configuration(self):
        session = self.extension_context()
        if session is not None and session.metadata.get("execution_settings") is not None:
            return session.metadata["execution_settings"]
        from app.agent_base.core.plugin_runtime import current_snapshot
        snapshot = current_snapshot()
        if snapshot is not None:
            return snapshot.settings
        from backend.config import get_settings
        return get_settings()

    def project_storage(self, project_file="", **kwargs):
        from backend.config.project_storage import project_storage
        return project_storage(project_file, **kwargs)

    def project_id(self, project_file="", **kwargs):
        from backend.config.project_storage import project_id_for
        return project_id_for(project_file, **kwargs)

    def runtime_path(self, area=""):
        from backend.config.paths import runtime_root
        from pathlib import Path
        relative = Path(area)
        if relative.anchor or ".." in relative.parts:
            raise ValueError("runtime area must be a relative path within the runtime root")
        return runtime_root() / relative

    def workspace_paths(self, *args, **kwargs):
        from app.runtime.workspace_paths import WorkspacePathResolver
        return WorkspacePathResolver(*args, **kwargs)

    def decode_output(self, value):
        from app.runtime.encoding import decode_process_output
        return decode_process_output(value)

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

    def resolve_provider(self, slot, *, settings=None, **kwargs):
        from app.agent_base.core.plugins import get_plugin_manager
        if settings is None:
            settings = self.configuration()
        return get_plugin_manager().load_optional(slot, settings=settings, kwargs=kwargs)

    def run_store(self):
        from app.services.run_state import get_run_store
        return get_run_store()

    def inspect_file(self, path):
        from app.agent_base.tools.my_tools.file_inventory import inspect_file
        return inspect_file(path)

    async def create_agent(self, *args, **kwargs):
        from app.agent_base.assembly import create_dev_agent
        return await create_dev_agent(*args, **kwargs)

    def is_production_agent(self, agent):
        from app.agent_base.agents.react_agent import ReActAgent
        return isinstance(agent, ReActAgent)

    async def execute_agent(self, *args, **kwargs):
        from app.services.agent_execution import handle_agent_execution
        return await handle_agent_execution(*args, **kwargs)

    def create_progress(self):
        from app.agent_base.tools.my_tools.conversation_tools import ProgressRelay
        return ProgressRelay()

    def trace_session(self, **kwargs):
        from app.runtime.trace_session import TraceSession
        return TraceSession(**kwargs)

    def record_run(self, status):
        from app.services.agent_metrics import get_agent_metrics
        get_agent_metrics().record_run(status)

    def bind_task(self, **kwargs):
        from app.agent_base.tools.task_system import create_task_execution
        return create_task_execution(**kwargs)

    def create_loop_agent(self, **kwargs):
        from app.agent_base.agents.react_agent import ReActAgent
        return ReActAgent(**kwargs)

    def foundation_registry(self, source_dir, test_dir, allowed_tools):
        from app.agent_base.tools.registry import ToolRegistry
        from app.agent_base.tools.my_tools.foundation_tools import create_foundation_tools
        registry = ToolRegistry()
        for tool in create_foundation_tools(source_dir, test_dir):
            if tool.name in allowed_tools:
                registry.register_tool(tool)
        return registry
