"""Result contract shared by contract collection and derived projections."""
from dataclasses import dataclass
from typing import Any
from .plugin_api import ArtifactFacts, ContractSnapshot

@dataclass(frozen=True)
class ContractAssembly:
    """Results and lifecycle status from one facts-first contract pass."""

    snapshot: ContractSnapshot
    facts: ArtifactFacts | None = None
    graph_status: str = "not_requested"
    graph_result: Any = None
    graph_error: str = ""
