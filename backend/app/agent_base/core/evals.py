"""Stable evaluation port owned by the Agent core.

Evaluation catalogs, runners, checkers and batch persistence are concrete
provider concerns.  The application layer only uses this contract, allowing a
different evaluation backend to be installed without changing the API.
"""

from __future__ import annotations

import importlib
import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class EvalProvider(Protocol):
    """Provider contract for case execution and result management."""

    def list_cases(self) -> list[Any]: ...

    def get_case(self, case_id: str) -> Any | None: ...

    async def run_case(self, case: Any) -> Any: ...

    def list_results(self, limit: int = 100) -> list[dict[str, Any]]: ...

    async def start_batch(self, request: Any) -> Any: ...

    def list_batches(self, limit: int = 20) -> list[dict[str, Any]]: ...

    def get_batch(self, batch_id: str) -> Any | None: ...

    def trends(self, limit: int = 20) -> list[dict[str, Any]]: ...

    def archive(self, request: Any) -> dict[str, Any]: ...

    def archive_baseline(self, snapshot: dict[str, Any], note: str = "") -> dict[str, Any]: ...

    def list_archives(self, limit: int = 20) -> list[dict[str, Any]]: ...


class NoOpEvalProvider:
    """Explicit empty provider used when evaluations are disabled."""

    def list_cases(self) -> list[Any]:
        return []

    def get_case(self, case_id: str) -> Any | None:
        return None

    async def run_case(self, case: Any) -> Any:
        raise RuntimeError("evaluation provider is disabled")

    def list_results(self, limit: int = 100) -> list[dict[str, Any]]:
        return []

    async def start_batch(self, request: Any) -> Any:
        raise RuntimeError("evaluation provider is disabled")

    def list_batches(self, limit: int = 20) -> list[dict[str, Any]]:
        return []

    def get_batch(self, batch_id: str) -> Any | None:
        return None

    def trends(self, limit: int = 20) -> list[dict[str, Any]]:
        return []

    def archive(self, request: Any) -> dict[str, Any]:
        raise RuntimeError("evaluation provider is disabled")

    def archive_baseline(self, snapshot: dict[str, Any], note: str = "") -> dict[str, Any]:
        raise RuntimeError("evaluation provider is disabled")

    def list_archives(self, limit: int = 20) -> list[dict[str, Any]]:
        return []


class _ResilientEvalProvider:
    """Contain provider failures at the optional module boundary."""

    def __init__(self, provider: EvalProvider):
        self.provider = provider

    def __getattr__(self, name: str):
        target = getattr(self.provider, name)

        async def safe_async(*args, **kwargs):
            try:
                return await target(*args, **kwargs)
            except Exception:
                logger.warning("[Evals] provider operation %s failed", name, exc_info=True)
                raise

        def safe_sync(*args, **kwargs):
            try:
                return target(*args, **kwargs)
            except Exception:
                logger.warning("[Evals] provider operation %s failed", name, exc_info=True)
                raise

        import inspect
        return safe_async if inspect.iscoroutinefunction(target) else safe_sync


def _load_factory(provider: str):
    module_name, separator, attribute = provider.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("eval provider must use 'module:factory' syntax")
    module = importlib.import_module(module_name)
    factory = getattr(module, attribute)
    if not callable(factory):
        raise TypeError(f"eval provider is not callable: {provider}")
    return factory


def load_evals(*, settings=None, **kwargs) -> EvalProvider:
    """Load the configured evaluation provider without a core import dependency."""

    if settings is None:
        try:
            from app.core.config import get_settings

            settings = get_settings()
        except Exception:
            settings = None

    if settings is not None and not getattr(settings, "agent_evals_enabled", True):
        return NoOpEvalProvider()

    provider = str(
        getattr(settings, "agent_evals_provider", "app.evals.provider:create") or ""
    ).strip()
    if not provider or provider.lower() in {"none", "noop", "disabled"}:
        return NoOpEvalProvider()

    try:
        factory = _load_factory(provider)
        instance = factory(settings=settings, **kwargs)
        required = ("list_cases", "get_case", "run_case", "list_results")
        if not all(callable(getattr(instance, name, None)) for name in required):
            raise TypeError("eval provider must expose the case and result operations")
        return _ResilientEvalProvider(instance)
    except Exception:
        logger.warning("[Evals] provider unavailable; using no-op", exc_info=True)
        return NoOpEvalProvider()


__all__ = ["EvalProvider", "NoOpEvalProvider", "load_evals"]
