"""Plugin assembly requests containing values and declared host capabilities."""
from dataclasses import dataclass, field
from typing import Protocol, TypedDict
from subprocess import CompletedProcess
from collections.abc import Sequence
from .agents import ModelPort, EnvironmentPromptPort
from .contexts import SkillContext
from .data import JsonObject, ToolSchema


class CatalogEntryPort(Protocol):
    @property
    def skill_id(self) -> str: ...
    @property
    def name(self) -> str: ...
    @property
    def description(self) -> str: ...
    @property
    def version(self) -> str: ...

class CatalogSnapshotPort(Protocol):
    @property
    def entries(self) -> Sequence[CatalogEntryPort]: ...

class CatalogCapture(Protocol):
    def __call__(self, provider: object | None = None, *, context: SkillContext | None = None) -> CatalogSnapshotPort: ...

class LanguageRunner(Protocol):
    def __call__(self, argv: list[str] | tuple[str, ...], *, cwd: str, capture_output: bool = True,
                 timeout: float = 60.0, check: bool = False) -> CompletedProcess[bytes]: ...

class ExplorerPort(Protocol):
    last_token_usage: int
    last_evidence_summary: list[JsonObject]
    async def _execute(self, params: JsonObject) -> str: ...

class ExplorerFactory(Protocol):
    def __call__(self, *, llm: ModelPort, source_dir: str, test_dir: str, design_dir: str,
        workspace_root: str, project_file: str, toolkits: tuple[str, ...], single_use: bool,
        max_cumulative_tokens: int, max_run_seconds: float, llm_timeout_seconds: float,
        max_tool_calls: int, child_run_name: str) -> ExplorerPort: ...

class AssemblyValues(TypedDict, total=False):
    skill_catalog: CatalogSnapshotPort

class AssemblyInputs(TypedDict, total=False):
    """Host-supplied inputs; optional capabilities may be absent or None."""
    workspace_root: str
    project_file: str
    source_dir: str
    test_dir: str
    design_dir: str
    llm: ModelPort
    environment_context: EnvironmentPromptPort
    explorer_factory: ExplorerFactory
    language_runner: LanguageRunner | None
    capture_catalog: CatalogCapture


class ProviderBinder(Protocol):
    def __call__(self, name: str, provider: object, *, options: JsonObject | None = None) -> None: ...


class AssemblyTool(Protocol):
    name: str
    def to_openai_schema(self) -> ToolSchema: ...


class AssemblySlots:
    CATALOG = "assembly.catalog"
    BIND = "assembly.bind"


@dataclass
class AssemblyRequest:
    inputs: AssemblyInputs
    bind: ProviderBinder
    tools: list[AssemblyTool] = field(default_factory=list)
    required_bindings: dict[str, list[str]] = field(default_factory=dict)
    values: AssemblyValues = field(default_factory=lambda: AssemblyValues())
    interface_id: str = AssemblySlots.BIND

    def validate(self) -> None:
        """Reject malformed contributions before tools enter the production registry."""
        if self.interface_id not in (AssemblySlots.CATALOG, AssemblySlots.BIND):
            raise ValueError(f"Unknown assembly slot: {self.interface_id}")
        names = set()
        for tool in self.tools:
            name = getattr(tool, "name", None)
            if not isinstance(name, str) or not name.strip() or not callable(getattr(tool, "to_openai_schema", None)):
                raise ValueError("Assembly tools require a non-empty name and to_openai_schema")
            for identifier in (name, *getattr(tool, "aliases", ())):
                if not isinstance(identifier, str) or not identifier.strip() or identifier in names:
                    raise ValueError(f"Invalid or duplicate assembly tool name: {identifier}")
                names.add(identifier)
        for slot, identifiers in self.required_bindings.items():
            if not isinstance(slot, str) or not slot.strip() or not isinstance(identifiers, list) or not identifiers:
                raise ValueError("Required bindings must map slot names to non-empty identifier lists")
            if any(not isinstance(identifier, str) or not identifier.strip() for identifier in identifiers):
                raise ValueError(f"Invalid required binding identifier: {slot}")
