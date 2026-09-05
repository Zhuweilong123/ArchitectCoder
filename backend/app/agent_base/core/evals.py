"""Stable evaluation port owned by the Agent core.

Evaluation catalogs, runners, checkers and batch persistence are concrete
provider concerns.  The application layer only uses this contract, allowing a
different evaluation backend to be installed without changing the API.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class EvalBatchRequest(BaseModel):
    """Provider-neutral request for starting an evaluation batch."""

    suite: str = ""
    case_ids: list[str] = Field(default_factory=list, max_length=200)
    version: str = Field(default="working-tree", min_length=1, max_length=100)
    label: str = Field(default="", max_length=200)


class EvalArchiveRequest(BaseModel):
    """Provider-neutral request for archiving an evaluation batch."""

    batch_id: str = Field(min_length=1, max_length=100)
    note: str = Field(default="", max_length=500)


class EvalPerformanceArchiveRequest(BaseModel):
    """Request for importing one persisted performance JSONL as a snapshot."""

    result_id: str = Field(min_length=1, max_length=300)
    version: str = Field(default="", max_length=100)
    note: str = Field(default="", max_length=500)


class EvalProvider(Protocol):
    """Provider contract for case execution and result management."""

    def list_cases(self) -> list[Any]: ...

    def get_case(self, case_id: str) -> Any | None: ...

    def get_baseline(self) -> dict[str, Any]: ...

    async def run_case(self, case: Any) -> Any: ...

    def list_results(self, limit: int = 100) -> list[dict[str, Any]]: ...

    async def start_batch(self, request: Any) -> Any: ...

    def list_batches(self, limit: int = 20) -> list[dict[str, Any]]: ...

    def get_batch(self, batch_id: str) -> Any | None: ...

    def trends(self, limit: int = 20) -> list[dict[str, Any]]: ...

    def archive(self, request: Any) -> dict[str, Any]: ...

    def archive_baseline(self, snapshot: dict[str, Any], note: str = "") -> dict[str, Any]: ...

    def list_archives(self, limit: int = 20) -> list[dict[str, Any]]: ...

    def list_performance_results(self, limit: int = 20) -> list[dict[str, Any]]: ...

    def get_performance_result(self, result_id: str) -> dict[str, Any] | None: ...

    def archive_performance_result(self, request: Any) -> dict[str, Any]: ...


class NoOpEvalProvider:
    """Explicit empty provider used when evaluations are disabled."""

    def list_cases(self) -> list[Any]:
        return []

    def get_case(self, case_id: str) -> Any | None:
        return None

    def get_baseline(self) -> dict[str, Any]:
        raise RuntimeError("evaluation provider is disabled")

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

    def list_performance_results(self, limit: int = 20) -> list[dict[str, Any]]:
        return []

    def get_performance_result(self, result_id: str) -> dict[str, Any] | None:
        return None

    def archive_performance_result(self, request: Any) -> dict[str, Any]:
        raise RuntimeError("evaluation provider is disabled")


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
    """Compatibility hook; actual loading remains owned by PluginManager."""
    from .plugins import PluginManager

    return PluginManager._load_factory(provider)


def load_evals(*, settings=None, **kwargs) -> EvalProvider:
    """Load evaluations through the central extension manager."""
    from .plugins import get_plugin_manager

    instance = get_plugin_manager().load(
        "evals",
        settings=settings,
        kwargs=kwargs,
        factory_loader=_load_factory,
    )
    if instance is None:
        return NoOpEvalProvider()
    return _ResilientEvalProvider(instance)


__all__ = [
    "EvalArchiveRequest",
    "EvalBatchRequest",
    "EvalPerformanceArchiveRequest",
    "EvalProvider",
    "NoOpEvalProvider",
    "load_evals",
]
