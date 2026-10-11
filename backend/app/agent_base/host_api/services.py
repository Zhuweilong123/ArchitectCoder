"""Injectable host capabilities; this module never imports their implementations."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Awaitable, ContextManager, Protocol, TypeVar, cast
from pathlib import Path
from collections.abc import Iterable, Mapping
from .runs import RunStorePort
from .configuration import PluginConfiguration
from .validation import SourceFactsProvider, ValidationReport, ValidationRequirement

ProviderT = TypeVar("ProviderT")


class RuntimePort(Protocol):
    run_id: str
    run_operation_id: str
    plugin_plan_id: str
    policy_metadata: dict[str, Any]
    todos: list


class ExtensionContextPort(Protocol):
    @property
    def project_id(self) -> str: ...

    def provider(self, name: str, contract: type[ProviderT]) -> ProviderT | None: ...
    def provider_options(self, name: str) -> Mapping[str, object]: ...
    def state(self, name: str) -> dict[str, Any]: ...


class OperationPort(Protocol):
    operation_id: str
    parent_operation_id: str
    operation_kind: str
    scope: str
    status: str


class ModelPort(Protocol):
    model: str
    provider: str

    async def ainvoke(self, messages, **kwargs) -> str: ...
    async def ainvoke_with_tools(self, messages, tools, **kwargs) -> dict: ...


class FileInspectionPort(Protocol):
    bytes: int
    lines: int | None
    symbol_hints: int | None
    interface_hints: int | None
    dependency_hints: int | None


class ProjectStoragePort(Protocol):
    root: Path
    state_dir: Path
    project_id: str


class WorkspacePathsPort(Protocol):
    workspace: Path | None
    def resolve(self, path: str) -> Path: ...


class EnvironmentServices(Protocol):
    def configuration(self) -> PluginConfiguration: ...
    def plugin_config(self, plugin: str) -> Mapping[str, object]: ...
    def project_storage(self, project_file: str = "", **kwargs) -> ProjectStoragePort | None: ...
    def project_id(self, project_file: str = "", **kwargs) -> str: ...
    def runtime_path(self, area: str = "") -> Path: ...
    def workspace_paths(self, *args, **kwargs) -> WorkspacePathsPort: ...
    def decode_output(self, value: bytes) -> str: ...


class ProviderServices(Protocol):
    def schedule_provider(self, provider: ProviderT, plugin: str) -> ProviderT: ...
    def resolve_provider(self, slot: str, *, settings: PluginConfiguration | None = None, **kwargs: object) -> object | None: ...


class ContextServices(Protocol):
    def extension_context(self) -> ExtensionContextPort | None: ...


class RuntimeServices(Protocol):
    def runtime(self) -> RuntimePort: ...
    def runtime_scope(self) -> ContextManager[RuntimePort]: ...
    def run_store(self) -> RunStorePort: ...
    def inspect_file(self, path: str) -> FileInspectionPort | None: ...


class ObservationServices(Protocol):
    def emit_event(self, event_type: str, payload: dict[str, Any]) -> None: ...
    def record_event(self, event_type: str, **payload: Any) -> None: ...
    def trace_span(self, name: str) -> ContextManager: ...
    def suppress_tracing(self) -> ContextManager: ...
    def current_operation(self) -> OperationPort | None: ...
    def operation_scope(self, kind: str, **kwargs) -> ContextManager[OperationPort]: ...
    def submit_background(self, work: Awaitable, **kwargs) -> Any: ...
    async def publish(self, stage, *, agent_name: str, run_id: str, payload: dict) -> None: ...
    def install_contributions(self, contributions, *, plugin: str) -> None: ...
    def record_run(self, status: str) -> None: ...


class ValidationServices(Protocol):
    def validate_design(self, diagrams: Iterable[object], *, workspace_root: str = "",
                        source_provider: SourceFactsProvider | None = None,
                        requirements: tuple[ValidationRequirement | dict[str, str], ...] = ()) -> ValidationReport: ...


class AgentServices(Protocol):
    def create_model(self, **kwargs) -> ModelPort: ...
    async def create_agent(self, *args, **kwargs) -> tuple: ...
    def is_production_agent(self, agent: Any) -> bool: ...
    async def execute_agent(self, *args, **kwargs) -> None: ...
    def create_progress(self) -> Any: ...
    def trace_session(self, **kwargs) -> Any: ...
    def bind_task(self, **kwargs) -> Any: ...
    def create_loop_agent(self, **kwargs) -> Any: ...
    def foundation_registry(self, source_dir: str, test_dir: str, allowed_tools: set[str]) -> Any: ...


class AssemblyServices(EnvironmentServices, ProviderServices, Protocol):
    """Only configuration and provider loading are needed during assembly."""


class ExecutionServices(EnvironmentServices, ContextServices, RuntimeServices, Protocol):
    """Request configuration, scoped bindings and runtime state."""


class LifecycleServices(ContextServices, RuntimeServices, ObservationServices, Protocol):
    """Capabilities used by lifecycle observers and background archives."""


class HostServices(EnvironmentServices, ProviderServices, ContextServices, RuntimeServices,
                   ObservationServices, ValidationServices, AgentServices, Protocol):
    """Compatibility aggregate; new integrations should use a narrow service port."""


_services: ContextVar[HostServices | None] = ContextVar("host_services", default=None)
_default_services: HostServices | None = None


def install_host_services(services: HostServices) -> None:
    """Bind the application's implementation at its composition root."""
    global _default_services
    _default_services = services


def get_host_services() -> HostServices:
    services = _services.get()
    if services is None:
        services = _default_services
    if services is None:
        raise RuntimeError("Host services have not been installed")
    return services


@contextmanager
def host_services_scope(services: HostServices):
    """Override capabilities for one request; child tasks inherit its binding."""
    token = _services.set(services)
    try:
        yield services
    finally:
        _services.reset(token)


def get_environment_services() -> EnvironmentServices:
    """Resolve this capability against the same active request binding."""
    return cast(EnvironmentServices, get_host_services())


def get_provider_services() -> ProviderServices:
    """Resolve this capability against the same active request binding."""
    return cast(ProviderServices, get_host_services())


def get_context_services() -> ContextServices:
    """Resolve this capability against the same active request binding."""
    return cast(ContextServices, get_host_services())


def get_runtime_services() -> RuntimeServices:
    """Resolve this capability against the same active request binding."""
    return cast(RuntimeServices, get_host_services())


def get_observation_services() -> ObservationServices:
    """Resolve this capability against the same active request binding."""
    return cast(ObservationServices, get_host_services())


def get_validation_services() -> ValidationServices:
    """Resolve this capability against the same active request binding."""
    return cast(ValidationServices, get_host_services())


def get_agent_services() -> AgentServices:
    """Resolve this capability against the same active request binding."""
    return cast(AgentServices, get_host_services())


def get_assembly_services() -> AssemblyServices:
    """Resolve this capability against the same active request binding."""
    return cast(AssemblyServices, get_host_services())


def get_execution_services() -> ExecutionServices:
    """Resolve this capability against the same active request binding."""
    return cast(ExecutionServices, get_host_services())


def get_lifecycle_services() -> LifecycleServices:
    """Resolve this capability against the same active request binding."""
    return cast(LifecycleServices, get_host_services())
