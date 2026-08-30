"""UML data models based on the design document specifications."""

from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum


class Visibility(str, Enum):
    PUBLIC = "+"
    PRIVATE = "-"
    PROTECTED = "#"


class Stereotype(str, Enum):
    CLASS = "class"
    INTERFACE = "interface"
    ABSTRACT = "abstract"
    ENUM = "enum"


class RelationType(str, Enum):
    INHERITANCE = "inheritance"
    COMPOSITION = "composition"
    AGGREGATION = "aggregation"
    ASSOCIATION = "association"
    REALIZATION = "realization"
    DEPENDENCY = "dependency"


# ---------- UML Attribute / Method ----------

class UmlAttribute(BaseModel):
    model_config = {"extra": "ignore"}
    name: str = ""
    type: str = ""
    visibility: Visibility = Visibility.PUBLIC
    default_value: Optional[str] = None
    is_static: bool = False


class UmlMethod(BaseModel):
    model_config = {"extra": "ignore"}
    name: str = ""
    return_type: str = ""
    params: str = ""
    visibility: Visibility = Visibility.PUBLIC
    is_static: bool = False
    is_abstract: bool = False


# ---------- UML Class ----------

class Position(BaseModel):
    x: float = 0
    y: float = 0


class Size(BaseModel):
    width: float = 200
    height: float = 150


class UmlClass(BaseModel):
    model_config = {"extra": "ignore"}
    id: str
    name: str = "NewClass"
    stereotype: Stereotype = Stereotype.CLASS
    attributes: list[UmlAttribute] = Field(default_factory=list)
    methods: list[UmlMethod] = Field(default_factory=list)
    position: Position = Field(default_factory=Position)
    size: Size = Field(default_factory=Size)
    note: str = ""
    provided_interfaces: list[str] = Field(default_factory=list)  # UML 2.5.1 lollipop
    required_interfaces: list[str] = Field(default_factory=list)  # UML 2.5.1 socket


# ---------- UML Relation ----------

class UmlRelation(BaseModel):
    model_config = {"extra": "ignore"}
    id: str
    source: str
    target: str
    type: RelationType = RelationType.ASSOCIATION
    multiplicity_source: str = ""
    multiplicity_target: str = ""
    role_name: str = ""
    note: str = ""


# ---------- Sequence Diagram models ----------


class SeqLifeline(BaseModel):
    model_config = {"extra": "ignore"}
    id: str
    name: str = "Participant"
    class_ref: str = ""   # optional: UmlClass.id from class diagram
    x: float = 100
    activations: list[float] = Field(default_factory=list)  # y-offsets of activation bars


class SeqFragment(BaseModel):
    """UML 2.5.1 Combined Fragment: loop, alt, opt, break, par, etc."""
    model_config = {"extra": "ignore"}
    id: str
    type: str = "loop"  # loop | alt | opt | break | par | critical | neg
    label: str = ""      # guard condition, e.g. "[for each item]"
    x: float = 80        # left X position
    width: float = 280    # fragment width
    y_start: float = 0   # top Y position
    y_end: float = 100    # bottom Y position


class SeqMessage(BaseModel):
    model_config = {"extra": "ignore"}
    id: str
    from_lifeline: str    # SeqLifeline.id
    to_lifeline: str      # SeqLifeline.id
    label: str = ""        # method name
    type: str = "sync"     # "sync" | "async" | "return" | "simple" | "self"
    order: int = 0         # vertical sequence number
    y: float = 0           # persisted Y position
    note: str = ""         # functional comment


# ---------- Component Diagram models ----------


class CompNode(BaseModel):
    model_config = {"extra": "ignore"}
    id: str
    name: str = "Component"
    x: float = 100
    y: float = 100
    width: float = 200
    height: float = 160
    parent_id: str = ""  # empty = top-level; populated = child of that component
    provided_interfaces: list[str] = Field(default_factory=list)
    required_interfaces: list[str] = Field(default_factory=list)


class CompRelation(BaseModel):
    model_config = {"extra": "ignore"}
    id: str
    source: str  # CompNode.id
    target: str  # CompNode.id
    type: str = "dependency"  # "dependency" | "delegation"


# ---------- UML Diagram (file format) ----------

class UmlDiagram(BaseModel):
    model_config = {"extra": "ignore"}
    version: str = "1.0"
    name: str = "Untitled"
    diagram_type: str = "class"  # "class" | "sequence" | "component"
    component_id: str = ""  # CompNode.id — links this diagram to a component diagram node
    # --- Class diagram fields ---
    classes: list[UmlClass] = Field(default_factory=list)
    relations: list[UmlRelation] = Field(default_factory=list)
    # --- Sequence diagram fields ---
    lifelines: list[SeqLifeline] = Field(default_factory=list)
    messages: list[SeqMessage] = Field(default_factory=list)
    fragments: list[SeqFragment] = Field(default_factory=list)
    # --- Component diagram fields ---
    components: list[CompNode] = Field(default_factory=list)
    comp_relations: list[CompRelation] = Field(default_factory=list)
    # --- Shared view state ---
    grid_visible: bool = True
    grid_size: int = 20
    grid_color: str = "#e0e0e0"
    grid_thickness: int = 1
    snap_to_grid: bool = True
    zoom: float = 1.0
    pan_x: float = 0
    pan_y: float = 0


# ---------- Project (multi-diagram container) ----------

class Project(BaseModel):
    """A project contains multiple diagrams of different types.

    The active_diagram_index points to the diagram currently being edited.
    Backward-compatible: opening a single .uml file wraps it in a Project
    with one diagram.
    """
    model_config = {"extra": "ignore"}
    version: str = "1.0"
    name: str = "Untitled"
    diagrams: list[UmlDiagram] = Field(default_factory=list)
    active_diagram_index: int = 0

    @property
    def active_diagram(self) -> UmlDiagram | None:
        """Return the currently active diagram, or None if the project has no diagrams."""
        if 0 <= self.active_diagram_index < len(self.diagrams):
            return self.diagrams[self.active_diagram_index]
        return None


def create_default_project(name: str = "Untitled") -> Project:
    """Create a new empty project. Use addDiagram() to add diagrams later."""
    import logging
    logger = logging.getLogger(__name__)
    project = Project(name=name, diagrams=[], active_diagram_index=0)
    logger.info(f"[Project] Created new empty project '{name}'")
    return project


# ---------- LLM ----------

class LlmRequest(BaseModel):
    prompt: str
    system_prompt: Optional[str] = None
    temperature: float = 0.7
    max_tokens: int = 4096


class LlmResponse(BaseModel):
    content: str
    usage: dict = Field(default_factory=dict)


# ---------- Global Optimization ----------


class DiagramSpec(BaseModel):
    """A single diagram generated by the LLM in a global optimization result.

    Supports multiple diagrams per type (e.g. two sequence diagrams for
    different scenarios). The ``data`` field contains the diagram content
    (classes/relations for class diagrams, lifelines/messages for sequence, etc.).
    """
    model_config = {"extra": "ignore"}
    type: str = "class"  # "class" | "sequence" | "component"
    name: str = "Untitled"
    component_id: str = ""  # CompNode.id — links this diagram to a component
    data: dict = Field(default_factory=dict)


class GlobalOptimizeResult(BaseModel):
    """New-format LLM response for global optimization.

    Replaces the old fixed-structure ``{optimized: {class, sequence, component}}``
    with a flexible ``diagrams`` array.
    """
    model_config = {"extra": "ignore"}
    diagrams: list[DiagramSpec] = Field(default_factory=list)
    consistency_report: list[dict] = Field(default_factory=list)
    changes_summary: str = ""
    design_constraints: dict = Field(default_factory=dict)
    diff: str = ""


# ---------- Markdown Export ----------

class ExportRequest(BaseModel):
    diagram: UmlDiagram
