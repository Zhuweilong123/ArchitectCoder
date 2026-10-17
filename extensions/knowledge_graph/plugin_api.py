"""Public provider contract for the optional knowledge graph capability."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TYPE_CHECKING, TypedDict

from app.agent_base.host_api.data import JsonObject

from .models import BuildStats as BuildStats

if TYPE_CHECKING:
    from app.models.uml import Project
    from extensions.design_contract.plugin_api import ArtifactFacts


@dataclass(frozen=True)
class GraphPreindexRequest:
    project: Project
    project_id: str
    project_file: str
    source_dir: str
    workspace_root: str


@dataclass(frozen=True)
class GraphPreindexResult:
    design_nodes: int
    source_nodes: int
    design_edges: int
    source_edges: int


class GraphPreindexProvider(Protocol):
    """Optional fixture indexing capability; the provider owns storage and parsing."""

    def preindex_project(self, request: GraphPreindexRequest) -> GraphPreindexResult: ...


class BoundedGraphItems(TypedDict):
    items: list[JsonObject]
    total: int
    returned: int
    truncated: bool


class GraphMap(TypedDict, total=False):
    error: str
    project_id: str
    stats: JsonObject
    diagrams: list[JsonObject]
    key_classes: list[JsonObject]
    files: JsonObject


class GraphFacts(TypedDict, total=False):
    available: bool
    project_id: str
    nodes: list[JsonObject]
    edges: list[JsonObject]
    stats: JsonObject
    truncated: dict[str, bool]
    error: str


class GraphLocations(TypedDict, total=False):
    error: str
    results: list[JsonObject]


class GraphExpansion(TypedDict, total=False):
    error: str
    direction: str
    results: BoundedGraphItems


class GraphImpact(TypedDict, total=False):
    error: str
    target: JsonObject
    direct_dependents: dict[str, list[JsonObject]]
    transitive_dependents: dict[str, list[JsonObject]]
    total_affected: int


class GraphDiff(TypedDict, total=False):
    error: str
    schema_version: int
    summary: JsonObject
    items: BoundedGraphItems


class KnowledgeGraphProvider(Protocol):
    def rebuild_project(self, project: Project, project_id: str, filepath: str = "") -> BuildStats | None: ...
    def search_diagrams(self, project_id: str, queries: list[str], top_k: int = 6) -> dict[str, set[str]]: ...
    def map_project(self, project_id: str, top_classes: int = 15) -> GraphMap: ...
    def contract_facts(self, project_id: str, max_items: int = 5000) -> GraphFacts: ...
    def index_facts(self, facts: ArtifactFacts) -> BuildStats | None: ...
    def sync_facts(self, facts: ArtifactFacts) -> BuildStats | None: ...
    def locate(self, project_id: str, pattern: str, node_types: list[str] | None = None, source: str | None = None, top_k: int = 10) -> GraphLocations: ...
    def expand(self, project_id: str, node_ids: list[str], direction: str = "outgoing", edge_types: list[str] | None = None, max_depth: int = 2, max_nodes: int = 50) -> GraphExpansion: ...
    def impact(self, project_id: str, node_id: str, max_depth: int = 2, max_nodes: int = 50) -> GraphImpact: ...
    def diff(self, project_id: str, source_dir: str | None = None, force_rebuild: bool = False, max_items: int = 30) -> GraphDiff: ...
