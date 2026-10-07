"""Host policy for per-run contract enablement; no provider loading."""
from typing import Any


def resolve_contract_enabled(requested: bool | None, settings: Any = None) -> bool:
    """Resolve a per-run request without mutating process-wide settings.

    The server setting is an upper bound.  Strict production mode deliberately
    ignores a client-side disable request.
    """
    configured = bool(getattr(settings, "agent_design_contract_enabled", True))
    if not configured or getattr(settings, "strict_production", False):
        return configured
    return configured if requested is None else bool(requested)
