"""BaseAgents 工具系统"""

from __future__ import annotations
from typing import TYPE_CHECKING

from .base import Tool as Tool, ToolParameter as ToolParameter
from .async_tool import AsyncTool as AsyncTool

if TYPE_CHECKING:
    from .registry import ToolRegistry as ToolRegistry


def __getattr__(name: str) -> type[ToolRegistry]:
    # Loading the executor first must not re-enter it through this facade.
    if name == "ToolRegistry":
        from .registry import ToolRegistry
        globals()[name] = ToolRegistry
        return ToolRegistry
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "Tool", "ToolParameter",
    "ToolRegistry",
    "AsyncTool",
]
