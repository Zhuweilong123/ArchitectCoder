"""Small graph map for the main Agent's exploration routing decision."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from app.agent_base.core.knowledge_graph import load_knowledge_graph
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


async def routing_map(settings: Any, project_file: str, requirement: str) -> dict[str, Any]:
    """Read only a bounded architecture map; never schedule workers here."""
    if not project_file or not getattr(settings, "agent_knowledge_graph_enabled", False):
        return {}
    try:
        provider = load_knowledge_graph(settings=settings, project_file=project_file)
        project_id = project_id_for(project_file)
        raw = await asyncio.wait_for(
            asyncio.to_thread(provider.map_project, project_id, 8), timeout=3.0,
        )
        return _compact_map(raw, requirement)
    except Exception:
        return {}


def routing_context(graph_map: dict[str, Any]) -> str:
    if not graph_map:
        return "The project graph has no available routing map; use the request or a narrow lookup to identify search terms."
    return (
        "Bounded project graph map (candidate names, not verified impact or source evidence): "
        + json.dumps(graph_map, ensure_ascii=False, separators=(",", ":"))
    )
