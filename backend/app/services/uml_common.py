"""Compatibility facade for the split UML service modules.

New code should import from the focused module directly. This facade remains
for existing Agent tools and third-party callers that used the historical
``uml_common`` import path.
"""

from .uml_index import (
    _build_focused_index,
    _build_reference_index,
    _format_index_for_prompt,
)
from .uml_prompting import (
    _analyze_scope,
    _build_global_prompt,
    _build_project_summary,
    _detect_existing_types,
    _load_example,
    _load_guide,
    _fetch_kg_hits as _fetch_kg_hits_impl,
)
from .uml_stream import JsonElementExtractor
from .uml_validation import (
    _apply_auto_fixes,
    _fuzzy_match_class,
    _normalize_llm_output,
    _normalize_optimize_result,
    _validate_cross_references,
)
from app.agent_base.core.knowledge_graph import get_knowledge_graph


def _fetch_kg_hits(
    project_file: str,
    instructions: str,
    out: dict[str, set[str]],
) -> None:
    """Compatibility wrapper that preserves monkeypatching of this module."""
    return _fetch_kg_hits_impl(
        project_file,
        instructions,
        out,
        graph_loader=get_knowledge_graph,
    )


__all__ = [
    "_build_reference_index",
    "_format_index_for_prompt",
    "_load_guide",
    "_load_example",
    "_fetch_kg_hits",
    "_build_project_summary",
    "_analyze_scope",
    "_build_focused_index",
    "_build_global_prompt",
    "_normalize_optimize_result",
    "_fuzzy_match_class",
    "_apply_auto_fixes",
    "_validate_cross_references",
    "_normalize_llm_output",
    "JsonElementExtractor",
]
