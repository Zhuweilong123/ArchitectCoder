"""Temporary evaluation-only legacy feature."""


class LegacyDebugProbe:
    """A removable debug probe seeded only for deletion evaluations."""

    def emit(self, message: str) -> str:
        return f"[legacy] {message}"
