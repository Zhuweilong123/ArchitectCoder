"""UML data models based on the design document specifications."""

from __future__ import annotations
from pydantic import BaseModel, Field, model_validator
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
    # Presentation state; None identifies older projects without member folding.
    expanded_attributes: Optional[bool] = None
    expanded_methods: Optional[bool] = None
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
    # User-adjusted orthogonal routing points; None keeps automatic routing.
    vertices: Optional[list[Position]] = None
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


class SeqOperand(BaseModel):
    """One guarded region of a combined fragment; membership is explicit."""
    model_config = {"extra": "ignore"}
    id: str
    guard: str = ""
    message_ids: list[str] = Field(default_factory=list)
    y_start: float
    y_end: float


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
    # Empty operands identify a legacy visual frame, not inferred branches.
    operands: list[SeqOperand] = Field(default_factory=list)
    lifeline_ids: list[str] = Field(default_factory=list)
    parent_fragment_id: str = ""
    parent_operand_id: str = ""


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
    # User-adjusted orthogonal routing points; None keeps automatic routing.
    vertices: Optional[list[Position]] = None


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

class GridSettings(BaseModel):
    grid_visible: bool = True
    grid_size: int = Field(default=20, ge=4, le=200)
    grid_color: str = "#f59e0b"
    grid_thickness: int = Field(default=1, ge=1, le=10)
    snap_to_grid: bool = True


class Project(BaseModel):
    """A project contains multiple diagrams of different types.

    The active_diagram_index points to the diagram currently being edited.
    Backward-compatible: opening a single .uml file wraps it in a Project
    with one diagram.
    """
    model_config = {"extra": "ignore"}
    version: str = "1.0"
    revision: int = Field(default=0, ge=0)
    name: str = "Untitled"
    grid_settings: GridSettings = Field(default_factory=GridSettings)
    diagrams: list[UmlDiagram] = Field(default_factory=list)
    active_diagram_index: int = 0

    @model_validator(mode="before")
    @classmethod
    def migrate_diagram_grid(cls, value):
        if not isinstance(value, dict) or "grid_settings" in value:
            return value
        diagrams = value.get("diagrams") or []
        if not isinstance(diagrams, list) or not diagrams:
            return value
        index = value.get("active_diagram_index", 0)
        try:
            index = max(0, min(int(index), len(diagrams) - 1))
        except (TypeError, ValueError):
            index = 0
        active = diagrams[index]
        if not isinstance(active, dict):
            return value
        fields = GridSettings.model_fields
        return {
            **value,
            "grid_settings": {key: active[key] for key in fields if key in active},
        }

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


# ---------- Markdown Export ----------

class ExportRequest(BaseModel):
    diagram: UmlDiagram
