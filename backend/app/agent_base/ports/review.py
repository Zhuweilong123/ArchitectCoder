"""Transport-independent request for a host-mediated human review."""
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ReviewPrompt:
    review_type: str
    title: str
    content: str
    question: str
    timeout_seconds: float
    metadata: dict[str, Any] = field(default_factory=dict)
