"""Response normalization helpers for the Function Calling loop."""

from __future__ import annotations

import re


def remove_textual_tool_markup(content: str) -> tuple[str, bool]:
    """Remove provider-specific pseudo tool calls from a tool-free response."""
    text = str(content or "")
    patterns = (
        r"<[^>]*tool_calls[^>]*>.*?</[^>]*tool_calls[^>]*>",
        r"<[^>]*invoke\b[^>]*>.*?</[^>]*invoke[^>]*>",
    )
    removed = False
    for pattern in patterns:
        text, count = re.subn(pattern, "", text, flags=re.IGNORECASE | re.DOTALL)
        removed = removed or bool(count)
    return text.strip(), removed


__all__ = [
    "remove_textual_tool_markup",
]
