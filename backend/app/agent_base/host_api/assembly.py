"""Plugin assembly requests containing values and declared host capabilities."""
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, TypedDict


class AssemblyInputs(TypedDict, total=False):
    """Host-supplied inputs; optional capabilities may be absent or None."""
    workspace_root: str
    project_file: str
    source_dir: str
    test_dir: str
    design_dir: str
    llm: Any
    environment_context: Any
    explorer_factory: Callable
    language_runner: Callable | None
    capture_catalog: Callable


class ProviderBinder(Protocol):
    def __call__(self, name: str, provider: Any, *, options: dict | None = None) -> None: ...


class AssemblyTool(Protocol):
    name: str
    def to_openai_schema(self) -> dict: ...


class AssemblySlots:
    CATALOG = "assembly.catalog"
    BIND = "assembly.bind"


@dataclass
class AssemblyRequest:
    inputs: AssemblyInputs
    bind: ProviderBinder
    tools: list[AssemblyTool] = field(default_factory=list)
    required_bindings: dict[str, list[str]] = field(default_factory=dict)
    values: dict[str, Any] = field(default_factory=dict)
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
