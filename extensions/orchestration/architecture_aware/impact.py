"""Extract a bounded, evidence-bearing task slice through the KG provider."""

from __future__ import annotations

import asyncio
import math
import re
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


def _seed_score(item: dict[str, Any], query: str, rank: int) -> float:
    """Prefer exact, source-grounded symbols over broad text hits."""
    name = re.sub(r"[^\w]+", "", str(item.get("name") or "").casefold())
    term = re.sub(r"[^\w]+", "", query.casefold())
    lexical = 0.0
    if name and term and name == term:
        lexical = 100.0
    elif name and len(term) >= 4 and term in name:
        lexical = 35.0
    elif term and len(name) >= 4 and name in term:
        lexical = 15.0
    node_type = str(item.get("node_type") or "")
    try:
        retrieval = max(0.0, float(item.get("score") or 0))
    except (TypeError, ValueError):
        retrieval = 0.0
    return (
        lexical + (8.0 if item.get("source") == "code" else 0.0)
        + (4.0 if node_type == "method" else 2.0 if node_type == "class" else 0.0)
        + max(0, 5 - rank) + min(5.0, math.log1p(retrieval))
    )


async def collect_impact(
    provider: Any,
    project_id: str,
    queries: tuple[str, ...],
    *,
    max_seeds: int = 4,
    max_nodes: int = 36,
) -> ImpactSlice:
    """Locate architecture/source seeds, then follow direct typed relations.

    The graph is a read-side index. A missing seed is not evidence that the
    requirement has no effect; the caller reports uncertainty to the main Agent.
    """

    facts = await asyncio.to_thread(provider.contract_facts, project_id, 1)
    if not isinstance(facts, dict) or not facts.get("available"):
        raise GraphUnavailable("project graph is unavailable or empty")

    candidates: list[list[tuple[float, dict[str, Any]]]] = []
    for query in queries[:4]:
        result = await asyncio.to_thread(
            provider.locate, project_id, query[:80], top_k=5,
        )
        if not isinstance(result, dict) or result.get("error"):
            continue
        ranked: list[tuple[float, dict[str, Any]]] = []
        for rank, item in enumerate(result.get("results", ())):
            if not isinstance(item, dict):
                continue
            if item.get("id"):
                ranked.append((_seed_score(item, query, rank), item))
        if ranked:
            candidates.append(sorted(ranked, key=lambda pair: -pair[0]))
    seeds: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_seed(item: dict[str, Any]) -> None:
        node_id = str(item["id"])
        if node_id not in seen and len(seeds) < max_seeds:
            seeds.append(item)
            seen.add(node_id)

    # Reserve one relevant seed for each distinct search term before filling
    # the remaining slots. A broad term must not crowd out later exact names.
    for ranked in candidates:
        for _, item in ranked:
            if str(item["id"]) not in seen:
                add_seed(item)
                break
    remaining = sorted(
        (pair for ranked in candidates for pair in ranked),
        key=lambda pair: -pair[0],
    )
    for _, item in remaining:
        add_seed(item)
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
