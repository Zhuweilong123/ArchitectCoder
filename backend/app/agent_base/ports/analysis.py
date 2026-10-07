"""Data and invocation contract for a tool-free analysis request."""
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AnalysisRequest:
    prompt: str
    evidence: str = ""
    run_id: str = ""
    allowed_tools: tuple[str, ...] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
