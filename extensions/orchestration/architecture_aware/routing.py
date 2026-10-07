"""Small graph map for the main Agent's exploration routing decision."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from app.agent_base.adapters.knowledge_graph import (load_knowledge_graph)
from backend.config.project_storage import project_id_for


def _label(value: Any) -> str:
    return re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or "")).strip()[:80]


def _compact_map(raw: Any, requirement: str) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("error"):
        return {}
    diagrams = [
        _label(item.get("name")) for item in raw.get("diagrams", ())
        if isinstance(item, dict) and _label(item.get("name"))
    ][:12]
    classes = [
        _label(item.get("name")) for item in raw.get("key_classes", ())
        if isinstance(item, dict) and _label(item.get("name"))
    ][:12]
    names = list(dict.fromkeys(diagrams + classes))
    lowered = requirement.casefold()
    matches = [name for name in names if len(name) >= 3 and name.casefold() in lowered]
    anchors = list(dict.fromkeys(matches + diagrams[:3] + classes[:5]))[:8]
    stats = raw.get("stats") or {}
    return {
        "graph_nodes": max(0, int(stats.get("total_nodes") or 0)),
        "graph_edges": max(0, int(stats.get("total_edges") or 0)),
        "matching_names": matches[:4],
        "candidate_names": anchors,
    } if anchors else {}


async def routing_map(
    settings: Any, project_file: str, requirement: str, source_dir: str = "",
) -> tuple[dict[str, Any], str]:
    """Read a bounded map and distinguish an empty map from an unavailable graph."""
    if not project_file:
        return {}, "project file is unavailable"
    if not getattr(settings, "agent_knowledge_graph_enabled", False):
        return {}, "knowledge graph is disabled"
    try:
        provider = load_knowledge_graph(settings=settings, project_file=project_file)
        project_id = project_id_for(project_file)
        facts = await asyncio.wait_for(
            asyncio.to_thread(provider.contract_facts, project_id, 1), timeout=3.0,
        )
        if not isinstance(facts, dict) or not facts.get("available"):
            reason = str((facts or {}).get("error") or "project graph is unavailable or empty")
            return {}, reason[:300]
        raw = await asyncio.wait_for(
            asyncio.to_thread(provider.map_project, project_id, 8), timeout=3.0,
        )
        if not isinstance(raw, dict) or raw.get("error"):
            reason = str(raw.get("error") if isinstance(raw, dict) else "invalid graph response")
            return {}, reason[:300]
        files = raw.get("files") or {}
        if source_dir and int(files.get("source_count") or 0) == 0:
            return {}, "source graph has no indexed source files"
        return _compact_map(raw, requirement), ""
    except Exception as exc:
        return {}, f"graph routing lookup failed: {type(exc).__name__}: {exc}"[:300]


def routing_context(graph_map: dict[str, Any]) -> str:
    if not graph_map:
        return "The project graph has no available routing map; use the request or a narrow lookup to identify search terms."
    return (
        "Bounded project graph map (candidate names, not verified impact or source evidence): "
        + json.dumps(graph_map, ensure_ascii=False, separators=(",", ":"))
    )
