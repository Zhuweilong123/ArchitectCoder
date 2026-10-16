"""Injectable host capabilities; this module never imports their implementations."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Awaitable, ContextManager, Protocol, TypeVar, cast, TYPE_CHECKING, Unpack
from asyncio import Task
from pathlib import Path
from collections.abc import Callable, Iterable, Iterator, Mapping
from .runs import RunStorePort
from .execution_types import RuntimePolicyMetadata
from .data import JsonObject, JsonValue, ConversationMessage, TodoItem
from .agents import AgentAssembly, AgentPort, ModelPort, ProgressPort, ReviewManagerPort, TaskBindingPort, ToolRegistryPort
from .tracing import TraceProvider, TraceSessionPort, TraceSink
if TYPE_CHECKING:
    from .assembly import AssemblyInputs
    from .lifecycle import Contribution, HookEvent
from .configuration import PluginConfiguration
from .validation import SourceFactsProvider, ValidationReport, ValidationRequirement

ProviderT = TypeVar("ProviderT")
WorkResult = TypeVar("WorkResult")


class RuntimePort(Protocol):
    run_id: str
    run_operation_id: str
    plugin_plan_id: str
    policy_metadata: RuntimePolicyMetadata
    todos: list[TodoItem]


class ExtensionContextPort(Protocol):
    @property
    def project_id(self) -> str: ...

    def provider(self, name: str, contract: type[ProviderT]) -> ProviderT | None: ...
    def provider_options(self, name: str) -> Mapping[str, object]: ...
    def state(self, name: str) -> dict[str, object]: ...


class OperationPort(Protocol):
    operation_id: str
    parent_operation_id: str
    operation_kind: str
    scope: str
    status: str


class FileInspectionPort(Protocol):
    @property
    def bytes(self) -> int: ...
    @property
    def lines(self) -> int | None: ...
    @property
    def symbol_hints(self) -> int | None: ...
    @property
    def interface_hints(self) -> int | None: ...
    @property
    def dependency_hints(self) -> int | None: ...


class ProjectStoragePort(Protocol):
    @property
    def root(self) -> Path: ...
    @property
    def state_dir(self) -> Path: ...
    @property
    def project_id(self) -> str: ...


class WorkspacePathsPort(Protocol):
    workspace: Path | None
    def resolve(self, path: str) -> Path: ...


class EnvironmentServices(Protocol):
    def configuration(self) -> PluginConfiguration: ...
    def plugin_config(self, plugin: str) -> Mapping[str, object]: ...
    def project_storage(self, project_file: str = "", *, workspace_root: str = "", create: bool = True) -> ProjectStoragePort | None: ...
    def project_id(self, project_file: str = "", *, workspace_root: str = "") -> str: ...
    def runtime_path(self, area: str = "") -> Path: ...
    def workspace_paths(self, workspace_root: str = "", source_dir: str = "", test_dir: str = "", design_dir: str = "") -> WorkspacePathsPort: ...
    def decode_output(self, value: bytes) -> str: ...


class ProviderServices(Protocol):
    def schedule_provider(self, provider: ProviderT, plugin: str) -> ProviderT: ...
    def resolve_provider(self, slot: str, *, settings: PluginConfiguration | None = None, **inputs: Unpack[AssemblyInputs]) -> object | None: ...


class ContextServices(Protocol):
    def extension_context(self) -> ExtensionContextPort | None: ...


class RuntimeServices(Protocol):
    def runtime(self) -> RuntimePort: ...
    def runtime_scope(self) -> ContextManager[RuntimePort]: ...
    def run_store(self) -> RunStorePort: ...
    def inspect_file(self, path: str) -> FileInspectionPort | None: ...


class ObservationServices(Protocol):
    def emit_event(self, event_type: str, payload: JsonObject) -> None: ...
    def record_event(self, event_type: str, **payload: JsonValue) -> None: ...
    def trace_span(self, name: str) -> ContextManager[None]: ...
    def suppress_tracing(self) -> ContextManager[None]: ...
    def current_operation(self) -> OperationPort | None: ...
    def operation_scope(self, kind: str, *, run_id: str = "", stage: str = "", scope: str = "run", plugin: str = "", interface_id: str = "", contribution_id: str = "", parent_operation_id: str | None = None) -> ContextManager[OperationPort]: ...
    def submit_background(self, work: Awaitable[WorkResult], *, owner: str, run_id: str = "", source_trace_id: str = "") -> Task[WorkResult | None]: ...
    async def publish(self, stage: HookEvent, *, agent_name: str, run_id: str, payload: JsonObject) -> None: ...
    def install_contributions(self, contributions: Iterable[Contribution], *, plugin: str) -> None: ...
    def record_run(self, status: str) -> None: ...


class ValidationServices(Protocol):
    def validate_design(self, diagrams: Iterable[JsonObject], *, workspace_root: str = "",
                        source_provider: SourceFactsProvider | None = None,
                        requirements: tuple[ValidationRequirement | dict[str, str], ...] = ()) -> ValidationReport: ...


class AgentServices(Protocol):
    def create_model(self, *, settings: PluginConfiguration | None = None, model: str | None = None,
                     temperature: float = 0.7, max_tokens: int | None = None, timeout: int = 120) -> ModelPort: ...
    async def create_agent(self, llm: ModelPort, source_dir: str = '', test_dir: str = '',
        project_file: str = '', user_message: str = '', progress: ProgressPort | None = None,
        restore_history: list[ConversationMessage] | None = None, task_scope: str = '',
        auto_approve_reviews: bool = False, max_tool_calls: int | None = None,
        max_run_seconds: float | None = None, max_total_tokens: int | None = None,
        design_dir: str = '', workspace_root: str = '') -> AgentAssembly: ...
    def is_production_agent(self, agent: object) -> bool: ...
    async def execute_agent(self, agent: AgentPort, review_mgr: ReviewManagerPort | None,
        user_message: str, send: Callable[[JsonObject], Awaitable[bool]], stop_check: Callable[[], bool],
        trace_log: TraceSink | None = None, project_file: str = '', source_dir: str = '',
        test_dir: str = '', design_dir: str = '', workspace_root: str = '', progress: ProgressPort | None = None,
        context: str = '', fallback_review_runs: dict[int, str] | None = None, run_id: str = '',
        run_owner: str = '', session_id: str = '', resume_checkpoint: JsonObject | None = None,
        disconnect_check: Callable[[], bool] | None = None, design_contract_enabled: bool | None = None) -> None: ...
    def create_progress(self) -> ProgressPort: ...
    def trace_session(self, *, session_id: str, user_message: str = '', project_file: str = '',
        source_dir: str = '', test_dir: str = '', design_dir: str = '', trace_dir: str = '',
        env_snapshot: JsonObject | None = None, provider: TraceProvider | None = None,
        sink: TraceSink | None = None, background_timeout_seconds: float | None = None) -> TraceSessionPort: ...
    def bind_task(self, *, scope: str, run_id: str, owner: str, subject: str, description: str = '') -> TaskBindingPort: ...
    def create_loop_agent(self, *, name: str, llm: ModelPort, tool_registry: ToolRegistryPort,
                          system_prompt: str | None = None, max_tool_calls: int = 0) -> AgentPort: ...
    def foundation_registry(self, source_dir: str, test_dir: str, allowed_tools: set[str]) -> ToolRegistryPort: ...


class AssemblyServices(EnvironmentServices, ProviderServices, Protocol):
    """Only configuration and provider loading are needed during assembly."""


class ExecutionServices(EnvironmentServices, ContextServices, RuntimeServices, Protocol):
    """Request configuration, scoped bindings and runtime state."""


class LifecycleServices(ContextServices, RuntimeServices, ObservationServices, Protocol):
    """Capabilities used by lifecycle observers and background archives."""


_services: ContextVar[object | None] = ContextVar("host_services", default=None)
_default_services: object | None = None


def install_host_services(services: object) -> None:
    """Bind the application's implementation at its composition root."""
    global _default_services
    _default_services = services


def _resolve_services() -> object:
    services = _services.get()
    if services is None:
        services = _default_services
    if services is None:
        raise RuntimeError("Host services have not been installed")
    return services


@contextmanager
def host_services_scope(services: object) -> Iterator[object]:
    """Override capabilities for one request; child tasks inherit its binding."""
    token = _services.set(services)
    try:
        yield services
    finally:
        _services.reset(token)


def get_environment_services() -> EnvironmentServices:
    """Resolve this capability against the same active request binding."""
    return cast(EnvironmentServices, _resolve_services())


def get_provider_services() -> ProviderServices:
    """Resolve this capability against the same active request binding."""
    return cast(ProviderServices, _resolve_services())


def get_context_services() -> ContextServices:
    """Resolve this capability against the same active request binding."""
    return cast(ContextServices, _resolve_services())


def get_runtime_services() -> RuntimeServices:
    """Resolve this capability against the same active request binding."""
    return cast(RuntimeServices, _resolve_services())


def get_observation_services() -> ObservationServices:
    """Resolve this capability against the same active request binding."""
    return cast(ObservationServices, _resolve_services())


def get_validation_services() -> ValidationServices:
    """Resolve this capability against the same active request binding."""
    return cast(ValidationServices, _resolve_services())


def get_agent_services() -> AgentServices:
    """Resolve this capability against the same active request binding."""
    return cast(AgentServices, _resolve_services())


def get_assembly_services() -> AssemblyServices:
    """Resolve this capability against the same active request binding."""
    return cast(AssemblyServices, _resolve_services())


def get_execution_services() -> ExecutionServices:
    """Resolve this capability against the same active request binding."""
    return cast(ExecutionServices, _resolve_services())


def get_lifecycle_services() -> LifecycleServices:
    """Resolve this capability against the same active request binding."""
    return cast(LifecycleServices, _resolve_services())
