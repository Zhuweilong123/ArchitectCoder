"""Default read-only design/source/test contract collector."""

from __future__ import annotations

import ast
import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

from app.agent_base.core.contracts import (
    ArtifactFacts,
    ContractEntity,
    ContractMapping,
    ContractSnapshot,
)

from .kg_adapter import KnowledgeGraphContractAdapter


class DesignContractProvider:
    """Collect normalized facts without judging consistency or editing files."""

    def __init__(self, *, settings=None, **kwargs):
        self.settings = settings
        self._kg = KnowledgeGraphContractAdapter(settings=settings)

    def collect(
        self,
        manifest: Any,
        project_id: str = "",
        scope: str = "project",
    ) -> ContractSnapshot:
        facts = self.collect_facts(manifest, project_id=project_id, scope=scope)
        return self.snapshot_from_facts(facts)

    def snapshot_from_facts(self, facts: ArtifactFacts) -> ContractSnapshot:
        """Project a previously collected fact bundle without reparsing files."""
        graph_facts = self._kg.collect(facts.project_id)
        graph_mappings = self._kg.infer_mappings(list(facts.entities), graph_facts)
        mappings = _merge_mappings(list(facts.mappings), graph_mappings)
        metadata = dict(facts.metadata)
        metadata.update({
            "knowledge_graph": _graph_metadata(graph_facts, graph_mappings),
            "errors": list(facts.diagnostics),
        })
        return ContractSnapshot(
            project_id=facts.project_id,
            scope=facts.scope,
            status=facts.status,
            entities=facts.entities,
            mappings=tuple(mappings),
            metadata=metadata,
        )

    def collect_facts(
        self,
        manifest: Any,
        project_id: str = "",
        scope: str = "project",
    ) -> ArtifactFacts:
        """Parse artifacts once into the shared facts-layer anchor."""
        values = manifest.to_dict() if hasattr(manifest, "to_dict") else dict(manifest or {})
        workspace = _path(values.get("workspace_root", ""))
        design_root = _path(values.get("design_root", ""))
        source_root = _path(values.get("source_root", ""))
        test_root = _path(values.get("test_root", ""))
        project_files = tuple(
            _path(value) for value in (values.get("project_files") or ()) if value
        )
        active = _path(values.get("project_file", ""))
        if active and active not in project_files:
            project_files = tuple(sorted((*project_files, active)))
        if not project_files and design_root:
            project_files = tuple(sorted(
                str(path.resolve())
                for path in Path(design_root).glob("*")
                if path.is_file() and path.suffix.lower() in {".umlproj", ".uml"}
            ))

        entities: list[ContractEntity] = []
        errors: list[dict[str, str]] = []
        for filepath in project_files:
            self._collect_design(filepath, workspace, entities, errors)
        self._collect_source(source_root, workspace, entities, errors)
        self._collect_tests(test_root, workspace, entities, errors)
        mappings = _infer_mappings(entities)
        resolved_project_id = project_id or _project_id(values, workspace)
        status = "collected" if entities else "blocked"
        if errors and entities:
            status = "partial"
        artifacts = _artifact_records(
            project_files, source_root, test_root, workspace, errors,
        )
        return ArtifactFacts(
            project_id=resolved_project_id,
            scope=scope,
            status=status,
            entities=tuple(entities),
            mappings=tuple(mappings),
            diagnostics=tuple(errors),
            metadata={
                "workspace_root": workspace,
                "layout_mode": values.get("layout_mode", ""),
                "design_file_count": len(project_files),
                "entity_counts": _counts(entities),
                "artifacts": artifacts,
                "parser_version": "design-contract-v1",
            },
        )

    @staticmethod
    def _collect_design(
        filepath: str,
        workspace: str,
        entities: list[ContractEntity],
        errors: list[dict[str, str]],
    ) -> None:
        path = Path(filepath)
        relative = _relative(path, workspace)
        design_id = f"design_file:{relative}"
        if not path.is_file():
            errors.append({"path": relative, "error": "design file does not exist"})
            return
        entities.append(ContractEntity(design_id, "design_file", path.stem, relative))
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            errors.append({"path": relative, "error": f"cannot parse design file: {exc}"})
            return
        for index, diagram in enumerate(payload.get("diagrams", [])):
            if not isinstance(diagram, dict):
                continue
            diagram_key = str(diagram.get("name") or diagram.get("id") or index)
            diagram_id = f"diagram:{relative}:{diagram_key}"
            entities.append(ContractEntity(
                diagram_id, "diagram", diagram_key, relative,
                attributes={"diagram_type": diagram.get("diagram_type", "")},
                parent_id=design_id,
            ))
            for item_key, entity_type in (("components", "component"), ("classes", "class")):
                for item in diagram.get(item_key, []) or []:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("name") or item.get("id") or "")
                    if not name:
                        continue
                    entity_id = f"design_{entity_type}:{relative}:{item.get('id') or name}"
                    entities.append(ContractEntity(
                        entity_id, entity_type, name, relative,
                        parent_id=diagram_id,
                        attributes={
                            "uml_id": str(item.get("id") or ""),
                            "methods": tuple(_item_name(v) for v in item.get("methods", []) or []),
                            "attributes": tuple(_item_name(v) for v in item.get("attributes", []) or []),
                        },
                    ))
                    for method in item.get("methods", []) or []:
                        if isinstance(method, dict) and method.get("name"):
                            entities.append(ContractEntity(
                                f"design_method:{relative}:{item.get('id') or name}:{method['name']}",
                                "method", str(method["name"]), relative,
                                parent_id=entity_id,
                                attributes={"params": str(method.get("params") or "")},
                            ))
            for item_key, entity_type in (("lifelines", "lifeline"), ("messages", "sequence_message")):
                for item in diagram.get(item_key, []) or []:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("name") or item.get("label") or item.get("id") or "")
                    if name:
                        entities.append(ContractEntity(
                            f"design_{entity_type}:{relative}:{item.get('id') or index}:{name}",
                            entity_type, name, relative, parent_id=diagram_id,
                            attributes={"order": item.get("order")},
                        ))

    @staticmethod
    def _collect_source(
        source_root: str,
        workspace: str,
        entities: list[ContractEntity],
        errors: list[dict[str, str]],
    ) -> None:
        if not source_root or not Path(source_root).is_dir():
            return
        for path in sorted(Path(source_root).rglob("*.py")):
            relative = _relative(path, workspace)
            module_id = f"source_module:{relative}"
            entities.append(ContractEntity(module_id, "source_module", path.stem, relative))
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (OSError, UnicodeError, SyntaxError) as exc:
                errors.append({"path": relative, "error": f"cannot parse source: {exc}"})
                continue
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    entities.append(_source_method(node, relative, module_id, ""))
                elif isinstance(node, ast.ClassDef):
                    class_id = f"source_class:{relative}:{node.name}"
                    entities.append(ContractEntity(class_id, "source_class", node.name, relative, node.lineno, module_id))
                    for child in node.body:
                        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            entities.append(_source_method(child, relative, class_id, node.name))
                        for attr in _instance_attributes(child):
                            entities.append(ContractEntity(
                                f"source_attribute:{relative}:{node.name}:{attr}",
                                "source_attribute", attr, relative, getattr(child, "lineno", None), class_id,
                            ))

    @staticmethod
    def _collect_tests(
        test_root: str,
        workspace: str,
        entities: list[ContractEntity],
        errors: list[dict[str, str]],
    ) -> None:
        if not test_root or not Path(test_root).is_dir():
            return
        for path in sorted(Path(test_root).rglob("*.py")):
            relative = _relative(path, workspace)
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (OSError, UnicodeError, SyntaxError) as exc:
                errors.append({"path": relative, "error": f"cannot parse test: {exc}"})
                continue
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                    entities.append(ContractEntity(
                        f"test_case:{relative}:{node.name}", "test_case", node.name,
                        relative, node.lineno,
                    ))
                elif isinstance(node, ast.ClassDef):
                    for child in node.body:
                        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name.startswith("test_"):
                            entities.append(ContractEntity(
                                f"test_case:{relative}:{node.name}.{child.name}", "test_case",
                                f"{node.name}.{child.name}", relative, child.lineno,
                            ))


def _source_method(node: ast.FunctionDef | ast.AsyncFunctionDef, path: str, parent: str, owner: str) -> ContractEntity:
    args = [arg.arg for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)]
    if owner and args and args[0] == "self":
        args = args[1:]
    return ContractEntity(
        f"source_method:{path}:{owner + '.' if owner else ''}{node.name}",
        "source_method", f"{owner + '.' if owner else ''}{node.name}", path,
        node.lineno, parent, {"params": tuple(args), "async": isinstance(node, ast.AsyncFunctionDef)},
    )


def _instance_attributes(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        targets = []
        if isinstance(child, ast.Assign):
            targets = child.targets
        elif isinstance(child, ast.AnnAssign):
            targets = [child.target]
        for target in targets:
            if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self":
                names.add(target.attr)
    return names


def _infer_mappings(entities: list[ContractEntity]) -> list[ContractMapping]:
    designs = {e.name: e for e in entities if e.entity_type == "class"}
    sources = {e.name.split(".")[-1]: e for e in entities if e.entity_type == "source_class"}
    tests = [e for e in entities if e.entity_type == "test_case"]
    mappings: list[ContractMapping] = []
    for name, design in designs.items():
        source = sources.get(name)
        if source is None:
            continue
        related = tuple(test.entity_id for test in tests if name.lower() in test.name.lower())
        mappings.append(ContractMapping(
            f"mapping:class:{design.entity_id}:{source.entity_id}",
            design.entity_id, source.entity_id, related, "inferred", "normalized_name",
        ))
    return mappings


def _counts(entities: list[ContractEntity]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entity in entities:
        counts[entity.entity_type] = counts.get(entity.entity_type, 0) + 1
    return counts


def _artifact_records(
    project_files: tuple[str, ...],
    source_root: str,
    test_root: str,
    workspace: str,
    errors: list[dict[str, str]],
) -> list[dict[str, Any]]:
    error_paths = {str(item.get("path", "")) for item in errors}
    records: list[dict[str, Any]] = []

    def add(artifact_type: str, path: Path) -> None:
        relative = _relative(path, workspace)
        exists = path.is_file()
        records.append({
            "artifact_id": f"{artifact_type}:{relative}",
            "artifact_type": artifact_type,
            "path": relative,
            "fingerprint": _file_fingerprint(path) if exists else "missing",
            "parser_version": "design-contract-v1",
            "status": "error" if relative in error_paths else "ready",
        })

    for filepath in project_files:
        add("design", Path(filepath))
    for artifact_type, root in (("source", source_root), ("test", test_root)):
        if not root or not Path(root).is_dir():
            continue
        for path in sorted(Path(root).rglob("*.py")):
            if path.is_file():
                add(artifact_type, path)
    return records


def _file_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return "unreadable"
    return digest.hexdigest()


def _merge_mappings(
    inferred: list[ContractMapping], graph: list[ContractMapping],
) -> list[ContractMapping]:
    """Prefer graph-backed evidence for a pair while retaining other inference."""
    inferred_by_pair = {
        (item.design_entity_id, item.source_entity_id): item for item in inferred
    }
    enriched_graph: list[ContractMapping] = []
    for item in graph:
        prior = inferred_by_pair.get((item.design_entity_id, item.source_entity_id))
        if prior is not None:
            item = replace(item, test_entity_ids=tuple(dict.fromkeys(
                (*prior.test_entity_ids, *item.test_entity_ids),
            )))
        enriched_graph.append(item)
    graph_pairs = {
        (item.design_entity_id, item.source_entity_id) for item in enriched_graph
    }
    retained = [
        item for item in inferred
        if (item.design_entity_id, item.source_entity_id) not in graph_pairs
    ]
    return [*retained, *enriched_graph]


def _graph_metadata(facts: dict[str, Any], mappings: list[ContractMapping]) -> dict[str, Any]:
    stats = facts.get("stats") if isinstance(facts, dict) else {}
    return {
        "available": bool(facts.get("available")) if isinstance(facts, dict) else False,
        "node_count": len(facts.get("nodes", ())) if isinstance(facts, dict) else 0,
        "edge_count": len(facts.get("edges", ())) if isinstance(facts, dict) else 0,
        "mapping_count": len(mappings),
        "truncated": facts.get("truncated", {}) if isinstance(facts, dict) else {},
        "stats": stats if isinstance(stats, dict) else {},
        "error": str(facts.get("error", "")) if isinstance(facts, dict) else "",
    }


def _item_name(item: Any) -> str:
    return str(item.get("name") if isinstance(item, dict) else item or "")


def _path(value: Any) -> str:
    return str(Path(value).resolve()) if value else ""


def _relative(path: Path, workspace: str) -> str:
    try:
        return os.path.relpath(path.resolve(), workspace or path.resolve()).replace(os.sep, "/")
    except ValueError:
        return str(path.resolve()).replace(os.sep, "/")


def _project_id(values: dict[str, Any], workspace: str) -> str:
    return Path(workspace).name if workspace else ""


def create(*, settings=None, **kwargs) -> DesignContractProvider:
    return DesignContractProvider(settings=settings, **kwargs)


__all__ = ["DesignContractProvider", "create"]
