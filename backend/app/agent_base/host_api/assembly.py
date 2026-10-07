"""Plugin assembly requests containing values and declared host capabilities."""
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class AssemblyRequest:
    inputs: dict[str, Any]
    bind: Callable
    tools: list[Any] = field(default_factory=list)
    required_bindings: dict[str, list[str]] = field(default_factory=dict)
    values: dict[str, Any] = field(default_factory=dict)
    interface_id: str = "assembly.bind"
