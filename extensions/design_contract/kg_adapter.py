"""Knowledge-graph enrichment for the design/source/test contract plugin.

This module depends only on the stable knowledge-graph port.  It deliberately
does not import the local SQLite provider, so a remote graph implementation can
be adopted without changing the contract collector.
"""

from __future__ import annotations

from typing import Any

from app.agent_base.core.contracts import ContractEntity, ContractMapping
from app.agent_base.core.knowledge_graph import load_knowledge_graph


class KnowledgeGraphContractAdapter:
    """Read graph facts and turn implementation edges into contract mappings."""

    def __init__(self, *, settings=None, provider=None):
        self.settings = settings
        self.provider = provider

    def collect(self, project_id: str, max_items: int = 5000) -> dict[str, Any]:
        """Return neutral graph facts, never making graph availability fatal."""
        provider = self.provider
        if provider is None:
            provider = load_knowledge_graph(settings=self.settings)
        exporter = getattr(provider, "contract_facts", None)
        if not callable(exporter):
            return {"available": False, "nodes": [], "edges": [], "error": "provider has no contract_facts"}
        try:
            facts = exporter(project_id, max_items=max_items)
        except Exception as exc:  # graph is an optional enrichment
            return {"available": False, "nodes": [], "edges": [], "error": str(exc)}
        if not isinstance(facts, dict):
            return {"available": False, "nodes": [], "edges": [], "error": "invalid graph facts"}
        return facts

    @staticmethod
    def infer_mappings(
        entities: list[ContractEntity], facts: dict[str, Any],
    ) -> list[ContractMapping]:
        """Translate graph ``implements`` edges to normalized contract links."""
        if not isinstance(facts, dict) or not facts.get("available"):
            return []
        nodes = {
            str(node.get("id")): node
            for node in facts.get("nodes") or ()
            if isinstance(node, dict) and node.get("id")
        }
        designs = {
            entity.name: entity
            for entity in entities if entity.entity_type in {"class", "component"}
        }
        source_classes = [entity for entity in entities if entity.entity_type == "source_class"]
        source_modules = [
            entity for entity in entities if entity.entity_type == "source_module"
        ]
        tests = [entity for entity in entities if entity.entity_type == "test_case"]
        tests_by_path = _group_by_path(tests)

        mappings: list[ContractMapping] = []
        for edge in facts.get("edges") or ():
            if not isinstance(edge, dict) or edge.get("edge_type") != "implements":
                continue
            source_node = nodes.get(str(edge.get("source_id")))
            target_node = nodes.get(str(edge.get("target_id")))
            if not source_node or not target_node:
                continue
            design = designs.get(str(target_node.get("name") or ""))
            if design is None:
                continue
            source_path = _node_path(source_node)
            source = next(
                (item for item in source_classes
                 if item.name == design.name and _same_path(item.path, source_path)),
                None,
            )
            source = source or next(
                (item for item in source_classes if item.name == design.name), None,
            )
            if source is None:
                source = next(
                    (item for item in source_modules if _same_path(item.path, source_path)),
                    None,
                )
            if source is None:
                continue
            related = _related_tests(
                str(source_node.get("id")), nodes, facts.get("edges", ()),
                tests_by_path,
            )
            mappings.append(ContractMapping(
                mapping_id=f"mapping:kg:{design.entity_id}:{source.entity_id}",
                design_entity_id=design.entity_id,
                source_entity_id=source.entity_id,
                test_entity_ids=related,
                confidence="graph",
                strategy="knowledge_graph:implements",
            ))
        return _dedupe_mappings(mappings)


def _group_by_path(entities: list[ContractEntity]) -> dict[str, tuple[ContractEntity, ...]]:
    groups: dict[str, list[ContractEntity]] = {}
    for entity in entities:
        groups.setdefault(_clean_path(entity.path), []).append(entity)
    return {path: tuple(items) for path, items in groups.items()}


def _related_tests(
    source_node_id: str,
    nodes: dict[str, dict[str, Any]],
    edges: Any,
    tests_by_path: dict[str, tuple[ContractEntity, ...]],
) -> tuple[str, ...]:
    """Follow graph TESTS edges so unrelated test files are not overlinked."""
    related: list[str] = []
    for edge in edges:
        if not isinstance(edge, dict) or edge.get("edge_type") != "tests":
            continue
        if str(edge.get("target_id")) != source_node_id:
            continue
        test_node = nodes.get(str(edge.get("source_id")))
        if not test_node:
            continue
        test_path = _node_path(test_node)
        for entity_path, test_entities in tests_by_path.items():
            if _same_path(entity_path, test_path):
                related.extend(test.entity_id for test in test_entities)
    return tuple(dict.fromkeys(related))


def _node_path(node: dict[str, Any]) -> str:
    properties = node.get("properties")
    if not isinstance(properties, dict):
        return ""
    return _clean_path(properties.get("path") or properties.get("filename") or "")


def _clean_path(value: Any) -> str:
    return str(value or "").replace("\\", "/").strip("./").lower()


def _same_path(left: str, right: str) -> bool:
    left_clean, right_clean = _clean_path(left), _clean_path(right)
    if not left_clean or not right_clean:
        return False
    return (
        left_clean == right_clean
        or left_clean.endswith("/" + right_clean)
        or right_clean.endswith("/" + left_clean)
    )


def _dedupe_mappings(mappings: list[ContractMapping]) -> list[ContractMapping]:
    seen: set[tuple[str, str]] = set()
    result: list[ContractMapping] = []
    for mapping in mappings:
        key = (mapping.design_entity_id, mapping.source_entity_id)
        if key in seen:
            continue
        seen.add(key)
        result.append(mapping)
    return result


__all__ = ["KnowledgeGraphContractAdapter"]
