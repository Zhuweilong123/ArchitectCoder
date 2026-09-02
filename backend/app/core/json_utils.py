"""Small JSON response normalization helpers shared by LLM-facing services."""

from __future__ import annotations

import re


def clean_llm_json_response(response: str) -> str:
    """Extract a JSON object from a model response with optional prose/fences."""
    text = str(response or "").strip()

    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()

    match = re.search(r"```\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        return match.group(1).strip()

    first_brace = text.find("{")
    if first_brace >= 0:
        depth = 0
        in_string = False
        escaped = False
        for index in range(first_brace, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[first_brace:index + 1]

    return text
