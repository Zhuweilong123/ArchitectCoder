"""File service – handles .uml / .umlproj file operations and markdown export."""

import json
import logging
import os
import threading
from datetime import datetime
from app.models.uml import UmlDiagram, Project
from backend.config import get_settings
from app.agent_base.core.knowledge_graph import get_knowledge_graph
from app.services.project_repository import ProjectRepository, ProjectSaveResult

settings = get_settings()
logger = logging.getLogger(__name__)
project_repository = ProjectRepository()
knowledge_graph_provider = get_knowledge_graph(settings=settings)


def ensure_dirs():
    os.makedirs(settings.uml_dir, exist_ok=True)


def save_diagram(diagram: UmlDiagram, filepath: str | None = None) -> str:
    """Save a UML diagram to a .uml JSON file. Returns the filepath."""
    ensure_dirs()
    if not filepath:
        filepath = os.path.join(
            settings.uml_dir,
            f"{diagram.name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.uml",
        )
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(diagram.model_dump(), f, indent=2, ensure_ascii=False)
    return filepath


def load_diagram(filepath: str) -> UmlDiagram:
    """Load a UML diagram from a .uml JSON file.

    Also handles project-format data (``diagrams`` key) by extracting the
    first diagram — this can happen when a ``.umlproj`` file was renamed to
    ``.uml`` or when ``save-project`` wrote to a ``.uml`` path.
    """
    ensure_dirs()
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Project-format data → extract the active diagram
    if "diagrams" in data:
        diagrams = data.get("diagrams", [])
        if diagrams:
            active_idx = data.get("active_diagram_index", 0)
            if 0 <= active_idx < len(diagrams):
                logger.info(
                    f"[FileService] Extracted diagram {active_idx} "
                    f"from project-format file: {os.path.basename(filepath)}"
                )
                return UmlDiagram(**diagrams[active_idx])
            return UmlDiagram(**diagrams[0])
    return UmlDiagram(**data)


def list_diagrams() -> list[dict]:
    """List all saved UML diagrams."""
    ensure_dirs()
    files = []
    if os.path.exists(settings.uml_dir):
        for fname in os.listdir(settings.uml_dir):
            if fname.endswith(".uml"):
                fpath = os.path.join(settings.uml_dir, fname)
                stat = os.stat(fpath)
                files.append({
                    "name": fname,
                    "path": fpath,
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                })
    return sorted(files, key=lambda f: f["modified"], reverse=True)


def export_markdown(diagram: UmlDiagram) -> str:
    """Export a UML diagram as a Markdown design document."""
    def cell(value: object) -> str:
        text = str(value if value not in (None, "") else "—")
        return text.replace("|", "\\|").replace("\r\n", "<br>").replace("\n", "<br>")

    diagram_type = diagram.diagram_type or "class"
    image_label = {
        "class": "Class diagram",
        "sequence": "Sequence diagram",
        "component": "Component diagram",
    }.get(diagram_type, "Design diagram")
    lines = [
        f"# {diagram.name} – Design Document",
        "",
        f"> Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Design Diagram",
        "",
        f"![{image_label}](diagrams/diagram.svg)",
        "",
    ]

    if diagram_type == "sequence":
        lifeline_names = {lifeline.id: lifeline.name for lifeline in diagram.lifelines}
        lines.extend(["## Sequence Overview", ""])
        lines.append(f"- Participants: {len(diagram.lifelines)}")
        lines.append(f"- Messages: {len(diagram.messages)}")
        if diagram.fragments:
            lines.append(f"- Combined fragments: {len(diagram.fragments)}")
        lines.extend(["", "## Participants", "", "| Participant | Class reference |", "|---|---|"])
        for lifeline in diagram.lifelines:
            class_name = next(
                (item.name for item in diagram.classes if item.id == lifeline.class_ref),
                lifeline.class_ref,
            )
            lines.append(f"| {cell(lifeline.name)} | {cell(class_name)} |")
        if diagram.messages:
            lines.extend(["", "## Messages", "", "| Order | From | To | Message | Type | Note |", "|---:|---|---|---|---|---|"])
            for message in sorted(diagram.messages, key=lambda item: (item.order, item.y)):
                source = lifeline_names.get(message.from_lifeline, message.from_lifeline)
                target = lifeline_names.get(message.to_lifeline, message.to_lifeline)
                lines.append(f"| {message.order} | {cell(source)} | {cell(target)} | {cell(message.label)} | `{message.type}` | {cell(message.note)} |")
        if diagram.fragments:
            lines.extend(["", "## Combined Fragments", "", "| Type | Guard / Label |", "|---|---|"])
            for fragment in diagram.fragments:
                lines.append(f"| `{fragment.type}` | {cell(fragment.label)} |")

    elif diagram_type == "component":
        component_names = {component.id: component.name for component in diagram.components}
        lines.extend(["## Component Overview", ""])
        lines.append(f"- Components: {len(diagram.components)}")
        lines.append(f"- Connections: {len(diagram.comp_relations)}")
        if diagram.components:
            lines.extend(["", "## Components", "", "| Component | Parent | Provided interfaces | Required interfaces |", "|---|---|---|---|"])
            for component in diagram.components:
                parent = component_names.get(component.parent_id, component.parent_id) or "—"
                provided = ", ".join(component.provided_interfaces) or "—"
                required = ", ".join(component.required_interfaces) or "—"
                lines.append(f"| {cell(component.name)} | {cell(parent)} | {cell(provided)} | {cell(required)} |")
        if diagram.comp_relations:
            lines.extend(["", "## Connections", "", "| Source | Target | Type |", "|---|---|---|"])
            for relation in diagram.comp_relations:
                source = component_names.get(relation.source, relation.source)
                target = component_names.get(relation.target, relation.target)
                lines.append(f"| {cell(source)} | {cell(target)} | `{relation.type}` |")

    else:
        class_names = {item.id: item.name for item in diagram.classes}
        lines.extend(["## Class Overview", ""])
        lines.append(f"- Classes: {len(diagram.classes)}")
        lines.append(f"- Relations: {len(diagram.relations)}")
        for item in diagram.classes:
            stereotype = f"«{item.stereotype.value}» " if item.stereotype.value != "class" else ""
            lines.extend(["", f"### {stereotype}{item.name}"])
            if item.note:
                lines.extend(["", f"> {item.note}"])
            if item.attributes or item.methods:
                lines.extend(["", "| Visibility | Member | Type / Return | Params |", "|---|---|---|---|"])
                for attribute in item.attributes:
                    default = f" = {attribute.default_value}" if attribute.default_value else ""
                    lines.append(f"| `{attribute.visibility.value}` | `{cell(attribute.name)}` | {cell(attribute.type)}{cell(default) if default else ''} | — |")
                for method in item.methods:
                    lines.append(f"| `{method.visibility.value}` | `{cell(method.name)}()` | {cell(method.return_type)} | {cell(method.params)} |")
        if diagram.relations:
            lines.extend(["", "## Relations", "", "| Source | Target | Type | Multiplicity | Role | Note |", "|---|---|---|---|---|---|"])
            for relation in diagram.relations:
                source = class_names.get(relation.source, relation.source)
                target = class_names.get(relation.target, relation.target)
                multiplicity = f"{relation.multiplicity_source}..{relation.multiplicity_target}" if relation.multiplicity_source or relation.multiplicity_target else "—"
                lines.append(f"| {cell(source)} | {cell(target)} | `{relation.type.value}` | {cell(multiplicity)} | {cell(relation.role_name)} | {cell(relation.note)} |")

    lines.extend(["", "---", "", "*Generated by ArchitectCoder*"])
    return "\n".join(lines)


# ── Project file operations (.umlproj) ──────────────────


def save_project_with_result(
    project: Project,
    filepath: str | None = None,
    *,
    expected_revision: int | None = None,
) -> ProjectSaveResult:
    """Persist a project through the repository and return its new revision."""
    ensure_dirs()
    result = project_repository.save(
        project,
        filepath,
        expected_revision=expected_revision,
    )
    logger.info(
        "[Project] Saved project '%s' (%d diagrams, revision=%d) -> %s",
        result.project.name,
        len(result.project.diagrams),
        result.revision,
        result.filepath,
    )
    _rebuild_kg_async(result.project, result.filepath)
    return result


def save_project(
    project: Project,
    filepath: str | None = None,
    *,
    expected_revision: int | None = None,
) -> str:
    """Backward-compatible facade returning only the filepath."""
    return save_project_with_result(
        project,
        filepath,
        expected_revision=expected_revision,
    ).filepath


def load_project(filepath: str) -> Project:
    """Load a project through the repository boundary."""
    ensure_dirs()
    project = project_repository.load(filepath)
    logger.info(
        "[Project] Loaded project '%s' (%d diagrams, revision=%d) from %s",
        project.name,
        len(project.diagrams),
        project.revision,
        filepath,
    )
    return project


def list_projects() -> list[dict]:
    """List projects through the repository boundary."""
    files = project_repository.list_projects()
    logger.debug("[Project] Listed %d .umlproj projects", len(files))
    return files


# ── Knowledge Graph rebuild hook ──────────────────────────

def _rebuild_kg_async(project: Project, filepath: str) -> None:
    """在后台线程重建知识图谱，不阻塞 HTTP 保存操作.

    每次 save_project() 成功后自动触发。
    设计层采用增量重建 (rebuild_project): 只更新变更的图, 不整库清空。

    因 builder 使用独立 DB 连接 + WAL 模式 + executemany 批量写入，
    并发保存同一项目时后者覆盖前者 (upsert 语义), 不会丢数据。
    """
    project_id = os.path.splitext(os.path.basename(filepath))[0]

    def _run():
        try:
            stats = knowledge_graph_provider.rebuild_project(
                project, project_id, filepath=filepath,
            )
            if stats is not None:
                logger.info(
                    "[KG] Declarative incremental rebuild for '%s': +%s nodes, "
                    "+%s edges, -%s old nodes, %sms",
                    project_id,
                    getattr(stats, "nodes_added", "?"),
                    getattr(stats, "edges_added", "?"),
                    getattr(stats, "nodes_removed", "?"),
                    getattr(stats, "elapsed_ms", "?"),
                )
        except Exception:
            logger.exception(f"[KG] Rebuild failed for project '{project_id}'")

    threading.Thread(target=_run, daemon=True, name=f"kg-rebuild-{project_id}").start()
