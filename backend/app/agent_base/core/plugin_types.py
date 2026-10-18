"""Structured catalog diagnostics and execution-plan reports."""
from typing import Literal, NotRequired, Protocol, TypedDict
from ..host_api.data import JsonObject


class PluginCallable(Protocol):
    """Dynamic import boundary; domain callers use their plugin's typed port."""
    def __call__(self, *args: object, **kwargs: object) -> object: ...


class PluginDiagnostic(TypedDict):
    plugin: str
    code: str
    message: str
    component: NotRequired[str]
    phase: NotRequired[str]
    provider: NotRequired[str]
    source: NotRequired[str]
    version: NotRequired[str]
    revision: NotRequired[str]


class InterfaceBinding(TypedDict):
    method: str
    stage: str
    contribution_id: str


class PlanPlugin(TypedDict):
    name: str
    provider: str
    source: str
    status: str
    error: str
    version: str
    slot: str
    revision: str
    manifest_digest: str
    implementation_digest: str
    diagnostics: list[PluginDiagnostic]
    dependencies: list[str]
    optional_dependencies: list[str]
    config_keys: list[str]
    interfaces: list[str]
    contributions: list[str]
    interface_bindings: list[InterfaceBinding]


class PluginRefreshResult(TypedDict):
    status: Literal["rejected", "unchanged", "published"]
    plan_id: str
    previous_plan_id: NotRequired[str]
    added: list[str]
    diagnostics: list[PluginDiagnostic]


class PlanStage(TypedDict):
    stage: str
    supported_modes: list[str]
    contributions: list[JsonObject]


class PlanBody(TypedDict):
    schema_version: int
    dispatch: str
    plugins: list[PlanPlugin]
    stages: list[PlanStage]
    notifications: list[PlanStage]
    run_end_semantics: str


class PlanReport(PlanBody):
    plan_id: str
