"""Reusable Knowledge Graph fixture construction."""

from __future__ import annotations

from pathlib import Path

from extensions.knowledge_graph.tools import create_kg_v2_tools
from app.models.uml import (
    Project,
    RelationType,
    Stereotype,
    UmlClass,
    UmlDiagram,
    UmlMethod,
    UmlRelation,
)
from extensions.knowledge_graph.builder import GraphBuilder


def build_knowledge_graph(tmp_path: Path) -> tuple[str, str, str]:
    """Build a compact design+code graph and return its paths and project id."""

    db_path = str(tmp_path / "kg.db")
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    (source_dir / "a.py").write_text(
        "class User:\n"
        "    def getName(self) -> str:\n"
        "        return self.uid\n"
        "class Admin:\n"
        "    pass\n",
        encoding="utf-8",
    )
    (source_dir / "b.py").write_text(
        "class Order:\n"
        "    pass\n",
        encoding="utf-8",
    )

    diagram = UmlDiagram(
        name="Domain",
        diagram_type="class",
        classes=[
            UmlClass(
                id="class_user",
                name="User",
                stereotype=Stereotype.CLASS,
                methods=[UmlMethod(name="getName", return_type="str")],
            ),
            UmlClass(id="class_order", name="Order"),
        ],
        relations=[UmlRelation(
            id="rel_1",
            source="class_user",
            target="class_order",
            type=RelationType.ASSOCIATION,
        )],
    )
    project_id = "proj"

    builder = GraphBuilder(db_path=db_path)
    builder.build_from_project(
        Project(name=project_id, diagrams=[diagram]), project_id,
    )
    builder.build_from_source_dir(str(source_dir), project_id)
    builder.close()

    return db_path, str(source_dir), project_id


def knowledge_graph_tools(db_path: str, source_dir: str, include_compare: bool = False):
    project_file = str(Path(source_dir).parent / "proj.umlproj")
    return create_kg_v2_tools(
        db_path=db_path,
        project_file=project_file,
        source_dir=source_dir,
        include_compare=include_compare,
    )
