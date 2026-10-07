"""Bounded model-facing tool output with session-backed continuation."""

from __future__ import annotations

from typing import Any

from app.runtime.tool_outputs import current_tool_output_store

from .base import Tool, ToolParameter
from .result import ToolResult


MAX_FED_CHARS = 3000
SKILL_FED_CHARS = 20_000
MAX_PAGE_CONTENT_CHARS = 2600


def tool_output_page_budget(tool_name: str) -> int | None:
    """Keep file and skill retrieval budgets separate from ordinary results."""
    if tool_name == "read_file":
        return None
    if tool_name == "skill":
        return SKILL_FED_CHARS
    return MAX_FED_CHARS


def first_tool_output_page(output: str, output_id: str, *,
                           max_chars: int = MAX_FED_CHARS) -> str:
    """Return an exact prefix and a continuation reference within the cap."""
    if len(output) <= max_chars or not output_id:
        return output
    offset = max_chars
    while True:
        marker = (
            f"\n\n[tool output truncated; total_chars={len(output)}; "
            f"output_id={output_id}; next_offset={offset}; "
            "call read_tool_output with this output_id and offset to continue]"
        )
        next_offset = max_chars - len(marker)
        if next_offset == offset:
            return output[:offset] + marker
        offset = next_offset


class ReadToolOutputTool(Tool):
    """Read a stable character range from this session's original tool result."""

    def __init__(self):
        super().__init__(
            name="read_tool_output",
            description=(
                "Continue a truncated result from a tool other than read_file "
                "using its output_id and next_offset. Reads the original text "
                "from this session's output store without rerunning the tool. For current "
                "workspace file content, use read_file with a line offset."
            ),
        )
        self.read_only = True
        self.can_parallel = True
        self.output_store = None

    def bind_output_store(self, store):
        self.output_store = store

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(name="output_id", type="string",
                          description="Reference printed in the truncated tool result"),
            ToolParameter(name="offset", type="integer",
                          description="Character offset printed as next_offset"),
            ToolParameter(name="limit", type="integer", required=False,
                          default=MAX_PAGE_CONTENT_CHARS,
                          description="Characters to read, at most 2600"),
        ]

    def run(self, parameters: dict[str, Any]) -> str:
        return self.run_result(parameters).text

    def run_result(self, parameters: dict[str, Any]) -> ToolResult:
        output_id = str(parameters.get("output_id") or "").strip()
        try:
            offset = int(parameters.get("offset"))
            limit = int(parameters.get("limit", MAX_PAGE_CONTENT_CHARS))
        except (TypeError, ValueError, OverflowError):
            return ToolResult.error("Error: offset and limit must be integers", "INVALID_ARGUMENT", True)
        if offset < 0 or limit < 1:
            return ToolResult.error("Error: offset must be nonnegative and limit must be positive", "INVALID_ARGUMENT", True)
        store = current_tool_output_store() or self.output_store
        if store is None:
            return ToolResult.error("Error: no active tool output store", "TOOL_OUTPUT_NOT_AVAILABLE")
        output = store.read(output_id, excluded_tool_names=frozenset({"read_file"}))
        if output is None:
            return ToolResult.error("Error: output_id was not found or is a read_file result "
                    "in the current session; use read_file for file content", "TOOL_OUTPUT_NOT_FOUND", True)
        if offset > len(output):
            return ToolResult.error(f"Error: offset exceeds the {len(output)}-character result", "INVALID_ARGUMENT", True)
        end = min(len(output), offset + min(limit, MAX_PAGE_CONTENT_CHARS))
        next_offset = str(end) if end < len(output) else "none"
        return ToolResult.success(
            f"[tool output {output_id}; chars {offset}:{end} of {len(output)}]\n"
            + output[offset:end]
            + f"\n[next_offset={next_offset}]"
        )


__all__ = [
    "MAX_FED_CHARS", "SKILL_FED_CHARS", "ReadToolOutputTool",
    "first_tool_output_page", "tool_output_page_budget",
]
