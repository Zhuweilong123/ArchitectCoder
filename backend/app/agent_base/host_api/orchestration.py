"""Stable orchestration port owned by the Agent core.

Graph storage and worker scheduling belong to providers.  Loading, validation
and disabled/unavailable fallbacks live in the host adapters package.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class OrchestrationRequest:
    user_message: str
    project_file: str = ""
    source_dir: str = ""
    test_dir: str = ""
    previous_checkpoint: dict[str, Any] = field(default_factory=dict)
    available_tools: tuple[str, ...] = ()
    run_id: str = ""


@dataclass(frozen=True)
class OrchestrationPreparation:
    """The only provider output consumed by the main Agent loop."""

    context_blocks: tuple[str, ...] = ()
    excluded_tools: tuple[str, ...] = ()
    phase: str = "disabled"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def context(self) -> str:
        return "\n\n".join(block for block in self.context_blocks if block)


class OrchestrationPort(Protocol):
    async def prepare(self, request: OrchestrationRequest) -> OrchestrationPreparation:
        """Prepare optional scheduling context and tool boundaries."""
