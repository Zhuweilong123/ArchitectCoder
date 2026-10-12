"""Bind the public plugin capability contract to the host execution machinery."""
from __future__ import annotations
from contextlib import contextmanager
from collections.abc import Awaitable, Callable, Iterable, Iterator, Mapping
from typing import ContextManager
from pathlib import Path
from asyncio import Task
from typing import TYPE_CHECKING, TypeVar, Unpack
if TYPE_CHECKING:
    from app.agent_base.core.extension_context import ExtensionContext
from app.agent_base.host_api.agents import (
    AgentAssembly, AgentPort, ModelPort, ProgressPort, ReviewManagerPort, TaskBindingPort, ToolRegistryPort,
)
from app.agent_base.host_api.assembly import AssemblyInputs
from app.agent_base.host_api.configuration import PluginConfiguration
from app.agent_base.host_api.data import ConversationMessage, JsonObject, JsonValue
from app.agent_base.host_api.lifecycle import Contribution, HookEvent
from app.agent_base.host_api.services import RuntimePort, ExtensionContextPort, OperationPort, FileInspectionPort, ProjectStoragePort, WorkspacePathsPort
from app.agent_base.host_api.runs import RunStorePort
from app.agent_base.host_api.tracing import TraceSessionPort, TraceSink, TraceProvider
from app.agent_base.host_api.validation import SourceFactsProvider, ValidationReport, ValidationRequirement

ProviderT = TypeVar('ProviderT')
WorkResult = TypeVar('WorkResult')


class ApplicationHostServices:
    def plugin_config(self, plugin: str) -> Mapping[str, object]:
        """Return a detached snapshot so plugins cannot mutate request settings."""
        from copy import deepcopy
        from types import MappingProxyType
        return MappingProxyType(deepcopy(self.configuration().plugin_configs.get(plugin, {})))

    def configuration(self) -> PluginConfiguration:
        session = self.extension_context()
        if session is not None and session.metadata.get("execution_settings") is not None:
            return session.metadata["execution_settings"]
        from app.agent_base.core.plugin_runtime import current_snapshot
        snapshot = current_snapshot()
        if snapshot is not None:
            return snapshot.settings
        from backend.config import get_settings
        return get_settings()

    def project_storage(self, project_file: str = "", *, workspace_root: str = "", create: bool = True) -> ProjectStoragePort | None:
        from backend.config.project_storage import project_storage
        return project_storage(project_file, workspace_root=workspace_root, create=create)

    def project_id(self, project_file: str = "", *, workspace_root: str = "") -> str:
        from backend.config.project_storage import project_id_for
        return project_id_for(project_file, workspace_root=workspace_root)

    def runtime_path(self, area: str = "") -> Path:
        from backend.config.paths import runtime_root
        from pathlib import Path
        relative = Path(area)
        if relative.anchor or ".." in relative.parts:
            raise ValueError("runtime area must be a relative path within the runtime root")
        return runtime_root() / relative

    def workspace_paths(self, workspace_root: str = "", source_dir: str = "", test_dir: str = "", design_dir: str = "") -> WorkspacePathsPort:
        from app.runtime.workspace_paths import WorkspacePathResolver
        return WorkspacePathResolver(workspace_root, source_dir, test_dir, design_dir)

    def decode_output(self, value: bytes) -> str:
        from app.runtime.encoding import decode_process_output
        return decode_process_output(value)

    def emit_event(self, event_type: str, payload: JsonObject) -> None:
        from app.agent_base.core.observability import emit_trace
        emit_trace("event", event_type=event_type, payload=payload)

    def record_event(self, event_type: str, **payload: JsonValue) -> None:
        from app.agent_base.core.observability import current_trace_sink
        sink = current_trace_sink()
        if sink is not None:
            sink.event(event_type, **payload)

    def trace_span(self, name: str) -> ContextManager[None]:
        from app.agent_base.core.observability import trace_span
        return trace_span(name)

    @contextmanager
    def suppress_tracing(self) -> Iterator[None]:
        from app.agent_base.core.observability import push_trace_hook, pop_trace_hook
        def ignore(kind: str, **payload: object) -> None:
            return None
        push_trace_hook(ignore)
        try:
            yield
        finally:
            pop_trace_hook(ignore)

    def runtime(self) -> RuntimePort:
        from app.agent_base.core.hooks import get_runtime
        return get_runtime()

    @contextmanager
    def runtime_scope(self) -> Iterator[RuntimePort]:
        from app.agent_base.core.hooks import AgentRuntime, set_runtime, reset_runtime
        runtime = AgentRuntime()
        token = set_runtime(runtime)
        try:
            yield runtime
        finally:
            reset_runtime(token)

    def extension_context(self) -> ExtensionContext | None:
        from app.agent_base.core.extension_context import current_extension_context
        return current_extension_context()

    def current_operation(self) -> OperationPort | None:
        from app.agent_base.core.operations import current_operation
        return current_operation()

    def operation_scope(self, kind: str, *, run_id: str = "", stage: str = "", scope: str = "run", plugin: str = "", interface_id: str = "", contribution_id: str = "", parent_operation_id: str | None = None) -> ContextManager[OperationPort]:
        from app.agent_base.core.operations import operation_scope
        return operation_scope(kind, run_id=run_id, stage=stage, scope=scope, plugin=plugin, interface_id=interface_id, contribution_id=contribution_id, parent_operation_id=parent_operation_id)

    def submit_background(self, work: Awaitable[WorkResult], *, owner: str, run_id: str = "", source_trace_id: str = "") -> Task[WorkResult | None]:
        from app.agent_base.core.background_tasks import submit_background
        return submit_background(work, owner=owner, run_id=run_id, source_trace_id=source_trace_id)

    async def publish(self, stage: HookEvent, *, agent_name: str, run_id: str, payload: JsonObject) -> None:
        from app.agent_base.host_api.lifecycle import HookContext
        from app.agent_base.core.hooks import get_hooks
        await get_hooks().aemit(stage, HookContext(stage, agent_name, run_id=run_id, payload=payload))

    def install_contributions(self, contributions: Iterable[Contribution], *, plugin: str) -> None:
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

    def schedule_provider(self, provider: ProviderT, plugin: str) -> ProviderT:
        from app.agent_base.core.plugin_dispatch import schedule_tool_provider
        return schedule_tool_provider(provider, plugin)

    def create_model(self, *, settings: PluginConfiguration | None = None, model: str | None = None, temperature: float = 0.7, max_tokens: int | None = None, timeout: int = 120) -> ModelPort:
        from app.agent_base.core.llm import BaseAgentsLLM
        return BaseAgentsLLM.from_settings(settings=settings or self.configuration(), model=model, temperature=temperature, max_tokens=max_tokens, timeout=timeout)

    def resolve_provider(self, slot: str, *, settings: PluginConfiguration | None = None, **inputs: Unpack[AssemblyInputs]) -> object | None:
        from app.agent_base.core.plugins import get_plugin_manager
        if settings is None:
            settings = self.configuration()
        return get_plugin_manager().load_optional(slot, settings=settings, kwargs=dict(inputs))

    def validate_design(self, diagrams: Iterable[JsonObject], *, workspace_root: str = "", source_provider: SourceFactsProvider | None = None, requirements: tuple[ValidationRequirement | dict[str, str], ...] = ()) -> ValidationReport:
        from app.services.design_validation import validate_project_diagrams
        return validate_project_diagrams(diagrams, workspace_root, source_provider=source_provider, requirements=requirements)

    def run_store(self) -> RunStorePort:
        from app.services.run_state import get_run_store
        return get_run_store()

    def inspect_file(self, path: str) -> FileInspectionPort | None:
        from app.agent_base.tools.my_tools.file_inventory import inspect_file
        return inspect_file(Path(path))

    async def create_agent(self, llm: ModelPort, source_dir: str = '', test_dir: str = '',
        project_file: str = '', user_message: str = '', progress: ProgressPort | None = None,
        restore_history: list[ConversationMessage] | None = None, task_scope: str = '',
        auto_approve_reviews: bool = False, max_tool_calls: int | None = None,
        max_run_seconds: float | None = None, max_total_tokens: int | None = None,
        design_dir: str = '', workspace_root: str = '') -> AgentAssembly:
        from app.agent_base.assembly import create_dev_agent
        return await create_dev_agent(llm, source_dir=source_dir, test_dir=test_dir, project_file=project_file,
            user_message=user_message, progress=progress, restore_history=restore_history, task_scope=task_scope,
            auto_approve_reviews=auto_approve_reviews, max_tool_calls=max_tool_calls,
            max_run_seconds=max_run_seconds, max_total_tokens=max_total_tokens, design_dir=design_dir, workspace_root=workspace_root)

    def is_production_agent(self, agent: object) -> bool:
        from app.agent_base.agents.react_agent import ReActAgent
        return isinstance(agent, ReActAgent)

    async def execute_agent(self, agent: AgentPort, review_mgr: ReviewManagerPort | None,
        user_message: str, send: Callable[[JsonObject], Awaitable[bool]], stop_check: Callable[[], bool],
        trace_log: TraceSink | None = None, project_file: str = '', source_dir: str = '',
        test_dir: str = '', design_dir: str = '', workspace_root: str = '', progress: ProgressPort | None = None,
        context: str = '', fallback_review_runs: dict[int, str] | None = None, run_id: str = '',
        run_owner: str = '', session_id: str = '', resume_checkpoint: JsonObject | None = None,
        disconnect_check: Callable[[], bool] | None = None, design_contract_enabled: bool | None = None) -> None:
        from app.services.agent_execution import handle_agent_execution
        from app.agent_base.agents.react_agent import ReActAgent
        if not isinstance(agent, ReActAgent):
            raise TypeError("execute_agent requires a production ReActAgent")
        await handle_agent_execution(agent, review_mgr, user_message, send, stop_check, trace_log=trace_log,
            project_file=project_file, source_dir=source_dir, test_dir=test_dir, design_dir=design_dir,
            workspace_root=workspace_root, progress=progress, context=context, fallback_review_runs=fallback_review_runs,
            run_id=run_id, run_owner=run_owner, session_id=session_id, resume_checkpoint=resume_checkpoint,
            disconnect_check=disconnect_check, design_contract_enabled=design_contract_enabled)

    def create_progress(self) -> ProgressPort:
        from app.agent_base.tools.my_tools.conversation_tools import ProgressRelay
        return ProgressRelay()

    def trace_session(self, *, session_id: str, user_message: str = '', project_file: str = '',
        source_dir: str = '', test_dir: str = '', design_dir: str = '', trace_dir: str = '',
        env_snapshot: JsonObject | None = None, provider: TraceProvider | None = None,
        sink: TraceSink | None = None, background_timeout_seconds: float | None = None) -> TraceSessionPort:
        from app.runtime.trace_session import TraceSession
        return TraceSession(session_id=session_id, user_message=user_message, project_file=project_file,
            source_dir=source_dir, test_dir=test_dir, design_dir=design_dir, trace_dir=trace_dir,
            env_snapshot=env_snapshot, provider=provider, sink=sink, background_timeout_seconds=background_timeout_seconds)

    def record_run(self, status: str) -> None:
        from app.services.agent_metrics import get_agent_metrics
        get_agent_metrics().record_run(status)

    def bind_task(self, *, scope: str, run_id: str, owner: str, subject: str, description: str = "") -> TaskBindingPort:
        from app.agent_base.tools.task_system import create_task_execution
        return create_task_execution(scope=scope, run_id=run_id, owner=owner, subject=subject, description=description)

    def create_loop_agent(self, *, name: str, llm: ModelPort, tool_registry: ToolRegistryPort, system_prompt: str | None = None, max_tool_calls: int = 0) -> AgentPort:
        from app.agent_base.agents.react_agent import ReActAgent
        return ReActAgent(name=name, llm=llm, tool_registry=tool_registry, system_prompt=system_prompt, max_tool_calls=max_tool_calls)

    def foundation_registry(self, source_dir: str, test_dir: str, allowed_tools: set[str]) -> ToolRegistryPort:
        from app.agent_base.tools.registry import ToolRegistry
        from app.agent_base.tools.my_tools.foundation_tools import create_foundation_tools
        registry = ToolRegistry()
        for tool in create_foundation_tools(source_dir, test_dir):
            if tool.name in allowed_tools:
                registry.register_tool(tool)
        return registry
