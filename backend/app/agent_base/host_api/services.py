"""Injectable host capabilities; this module never imports their implementations."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Awaitable, ContextManager, Protocol
from pathlib import Path
from collections.abc import Mapping
from .runs import RunStorePort


class RuntimePort(Protocol):
    run_id: str
    run_operation_id: str
    plugin_plan_id: str
    policy_metadata: dict[str, Any]
    todos: list


class ExtensionContextPort(Protocol):
    providers: dict[str, Any]
    metadata: dict[str, Any]

    def state(self, name: str) -> dict: ...


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


class HostServices(Protocol):
    """Capabilities resolved against the active request when they are called."""

    def emit_event(self, event_type: str, payload: dict[str, Any]) -> None: ...
    def record_event(self, event_type: str, **payload: Any) -> None: ...
    def trace_span(self, name: str) -> ContextManager: ...
    def suppress_tracing(self) -> ContextManager: ...
    def runtime(self) -> RuntimePort: ...
    def runtime_scope(self) -> ContextManager[RuntimePort]: ...
    def extension_context(self) -> ExtensionContextPort | None: ...
    def current_operation(self) -> OperationPort | None: ...
    def operation_scope(self, kind: str, **kwargs) -> ContextManager[OperationPort]: ...
    def submit_background(self, work: Awaitable, **kwargs) -> Any: ...
    async def publish(self, stage, *, agent_name: str, run_id: str, payload: dict) -> None: ...
    def install_contributions(self, contributions, *, plugin: str) -> None: ...
    def schedule_provider(self, provider, plugin: str) -> Any: ...
    def create_model(self, **kwargs) -> ModelPort: ...
    def resolve_provider(self, slot: str, *, settings=None, **kwargs) -> Any | None: ...
    def validate_design(self, diagrams, *, workspace_root="", source_provider=None, requirements=()): ...
    def run_store(self) -> RunStorePort: ...
    def inspect_file(self, path: str) -> FileInspectionPort | None: ...
    async def create_agent(self, *args, **kwargs) -> tuple: ...
    def is_production_agent(self, agent: Any) -> bool: ...
    async def execute_agent(self, *args, **kwargs) -> None: ...
    def create_progress(self) -> Any: ...
    def trace_session(self, **kwargs) -> Any: ...
    def record_run(self, status: str) -> None: ...
    def bind_task(self, **kwargs) -> Any: ...
    def create_loop_agent(self, **kwargs) -> Any: ...
    def foundation_registry(self, source_dir: str, test_dir: str, allowed_tools: set[str]) -> Any: ...
    def configuration(self) -> Any: ...
    def plugin_config(self, plugin: str) -> Mapping[str, Any]: ...
    def project_storage(self, project_file: str = "", **kwargs) -> ProjectStoragePort | None: ...
    def project_id(self, project_file: str = "", **kwargs) -> str: ...
    def runtime_path(self, area: str = "") -> Path: ...
    def workspace_paths(self, *args, **kwargs) -> WorkspacePathsPort: ...
    def decode_output(self, value: bytes) -> str: ...


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
