"""Helpers for separating a full UML snapshot from changed diagrams."""

from __future__ import annotations

import json
from typing import Any


def diagram_key(diagram: dict[str, Any]) -> str:
    """Return the stable identity used by the frontend for a diagram."""
    dtype = diagram.get("diagram_type") or diagram.get("type") or "class"
    name = diagram.get("name") or ""
    return f"{dtype}:{name}"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def changed_diagrams(
    after: list[dict[str, Any]] | None,
    before: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Return added or semantically changed diagrams from ``after``."""
    before_by_key = {diagram_key(item): item for item in (before or [])}
    return [
        item for item in (after or [])
        if diagram_key(item) not in before_by_key
        or _canonical_json(before_by_key[diagram_key(item)]) != _canonical_json(item)
    ]

