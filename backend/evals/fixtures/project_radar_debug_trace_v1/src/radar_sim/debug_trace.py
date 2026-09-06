"""Temporary evaluation-only debug tracing feature."""


class DebugTrace:
    """A removable tracing helper seeded only for deletion evaluations."""

    def record(self, event: str) -> str:
        return f"trace:{event}"
