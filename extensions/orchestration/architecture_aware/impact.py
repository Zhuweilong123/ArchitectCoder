"""Extract a bounded, evidence-bearing task slice through the KG provider."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any


class GraphUnavailable(RuntimeError):
    """The configured graph cannot ground this request."""


@dataclass(frozen=True)
class ImpactSlice:
    project_id: str
    nodes: dict[str, dict[str, Any]]
    # source, target, relation; these are observed graph-neighborhood links.
    edges: tuple[tuple[str, str, str], ...]
    seed_ids: tuple[str, ...]
    queries: tuple[str, ...]
    truncated: bool = False


async def collect_impact(
    provider: Any,
    project_id: str,
    queries: tuple[str, ...],
    *,
    max_seeds: int = 6,
    max_nodes: int = 36,
) -> ImpactSlice:
    """Locate architecture/source seeds, then follow direct typed relations.

    The graph is a read-side index. A missing seed is not evidence that the
    requirement has no effect; the caller returns to the existing explorer.
    """

    facts = await asyncio.to_thread(provider.contract_facts, project_id, 1)
    if not isinstance(facts, dict) or not facts.get("available"):
        raise GraphUnavailable("project graph is unavailable or empty")

    seeds: list[dict[str, Any]] = []
    seen: set[str] = set()
    for query in queries[:4]:
        result = await asyncio.to_thread(
            provider.locate, project_id, query[:80], top_k=3,
        )
        if not isinstance(result, dict) or result.get("error"):
            continue
        for item in result.get("results", ()):
            if not isinstance(item, dict):
                continue
            node_id = str(item.get("id") or "")
            if node_id and node_id not in seen:
                seeds.append(item)
                seen.add(node_id)
            if len(seeds) >= max_seeds:
                break
        if len(seeds) >= max_seeds:
            break
    if not seeds:
        raise GraphUnavailable("no graph entity matches the requirement")

    nodes: dict[str, dict[str, Any]] = {
        str(node["id"]): node for node in seeds
    }
    edges: set[tuple[str, str, str]] = set()
    # contract_facts(limit=1) is only an availability probe. Its export is
    # intentionally truncated and says nothing about this selected slice.
    truncated = False

    def add_node(item: dict[str, Any]) -> str:
        nonlocal truncated
        node_id = str(item.get("id") or "")
        if not node_id:
            return ""
        if node_id not in nodes:
            if len(nodes) >= max_nodes:
                truncated = True
                return ""
            nodes[node_id] = item
        return node_id

    for seed in seeds:
        seed_id = str(seed["id"])
        outward = await asyncio.to_thread(
            provider.expand, project_id, [seed_id],
            direction="outgoing", max_depth=1, max_nodes=8,
        )
        if isinstance(outward, dict) and not outward.get("error"):
            records = outward.get("results") or {}
            if isinstance(records, dict):
                truncated = (
                    truncated or bool(records.get("truncated"))
                    or int(records.get("returned") or 0) >= 8
                )
                for item in records.get("items", ()):
                    if not isinstance(item, dict):
                        continue
                    node_id = add_node(item)
                    if node_id:
                        for relation in item.get("edge_types", ()):
                            edges.add((seed_id, node_id, str(relation)))

        incoming = await asyncio.to_thread(
            provider.impact, project_id, seed_id,
            max_depth=1, max_nodes=8,
        )
        if isinstance(incoming, dict) and not incoming.get("error"):
            truncated = truncated or int(incoming.get("total_affected") or 0) >= 7
            groups = incoming.get("direct_dependents") or {}
            if isinstance(groups, dict):
                for group in groups.values():
                    for item in group:
                        if not isinstance(item, dict):
                            continue
                        node_id = add_node(item)
                        if node_id:
                            for relation in item.get("edge_types", ()):
                                edges.add((node_id, seed_id, str(relation)))

    return ImpactSlice(
        project_id=project_id,
        nodes=nodes,
        edges=tuple(sorted(edges)),
        seed_ids=tuple(str(seed["id"]) for seed in seeds),
        queries=queries,
        truncated=truncated,
    )
