"""Compatibility facade for the split UML service modules.

New code should import from the focused module directly. This facade remains
for existing Agent tools and third-party callers that used the historical
``uml_common`` import path.
"""

import os

from .uml_index import (
    _build_focused_index,
    _build_reference_index,
    _format_index_for_prompt,
)
from app.agent_base.core.knowledge_graph import get_knowledge_graph
from .uml_stream import JsonElementExtractor
from .uml_validation import (
    _apply_auto_fixes,
    _fuzzy_match_class,
    _normalize_llm_output,
    _validate_cross_references,
)


def _fetch_kg_hits(
    project_file: str,
    instructions: str,
    out: dict[str, set[str]],
) -> None:
    """Compatibility wrapper that preserves monkeypatching of this module."""
    try:
        project_id = os.path.splitext(os.path.basename(project_file))[0]
        queries = [instructions]
        words = instructions.split()
        if len(words) >= 2:
            queries.extend(" ".join(words[i:i + 3]) for i in range(0, len(words), 2))
        seen: set[str] = set()
        queries = [q for q in queries if q and not (q in seen or seen.add(q))][:5]
        matches = get_knowledge_graph().search_diagrams(project_id, queries, top_k=6)
        for diagram_name, reasons in matches.items():
            out.setdefault(diagram_name, set()).update(reasons)
    except Exception:
        pass


__all__ = [
    "_build_reference_index",
    "_format_index_for_prompt",
    "_fetch_kg_hits",
    "_build_focused_index",
    "_fuzzy_match_class",
    "_apply_auto_fixes",
    "_validate_cross_references",
    "_normalize_llm_output",
    "JsonElementExtractor",
]
