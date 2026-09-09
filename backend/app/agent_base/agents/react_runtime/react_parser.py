"""Parsing helpers for the legacy textual ReAct protocol."""

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


def parse_output(text: str) -> tuple:
    """Extract ``Thought`` and ``Action`` from a textual model response."""
    thought = None
    action = None

    thought_match = re.search(
        r"Thought:\s*(.+?)(?=\n\s*(?:Action:|$))", text, re.DOTALL,
    )
    if thought_match:
        thought = thought_match.group(1).strip()

    action_match = re.search(r"Action:\s*(.+)", text)
    if action_match:
        action = action_match.group(1).strip()

    return thought, action


def parse_action(action_text: str) -> tuple:
    """Extract a tool name and bracket-delimited input."""
    match = re.match(r"(\w+)\[(.*)\]", action_text)
    if match:
        return match.group(1), match.group(2)
    return None, None


def parse_action_input(action_text: str) -> str:
    """Extract the answer from a textual ``Finish[answer]`` action."""
    match = re.match(r"Finish\[(.*)\]", action_text, re.DOTALL)
    if match:
        return match.group(1)
    return action_text


__all__ = [
    "remove_textual_tool_markup",
    "parse_output",
    "parse_action",
    "parse_action_input",
]
