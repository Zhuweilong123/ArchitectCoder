"""Loading, fallback and validation adapters for optional capabilities."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import ParamSpec, TypeVar, cast

from app.agent_base.core.plugin_types import PluginCallable
from app.agent_base.host_api.configuration import PluginConfiguration

from app.agent_base.host_api.data import JsonObject
from extensions.evals.plugin_api import (
    EvalCase, EvalResult, EvalBatch, EvalBatchRequest, EvalBatchMergeRequest,
    EvalArchiveRequest, EvalPerformanceArchiveRequest, EvalBaseline, EvalBatchRecord,
    EvalArchiveReceipt, EvalArchiveRecord, EvalPerformanceRecord, EvalBatchDeleted,
    EvalPerformanceDeleted, EvalProvider,
)

logger = logging.getLogger(__name__)

_P = ParamSpec("_P")
_Result = TypeVar("_Result")

class NoOpEvalProvider:
    """Explicit empty provider used when evaluations are disabled."""

    def list_cases(self) -> list[EvalCase]:
        return []

    def get_case(self, case_id: str) -> EvalCase | None:
        return None

    def get_baseline(self) -> EvalBaseline:
        raise RuntimeError("evaluation provider is disabled")

    async def run_case(self, case: EvalCase) -> EvalResult:
        raise RuntimeError("evaluation provider is disabled")

    def list_results(self, limit: int = 100) -> list[JsonObject]:
        return []

    async def start_batch(self, request: EvalBatchRequest) -> EvalBatch:
        raise RuntimeError("evaluation provider is disabled")

    def merge_batches(self, request: EvalBatchMergeRequest) -> EvalBatch:
        raise RuntimeError("evaluation provider is disabled")

    def list_batches(self, limit: int = 20) -> list[EvalBatchRecord]:
        return []

    def get_batch(self, batch_id: str) -> EvalBatch | None:
        return None

    def delete_batch(self, batch_id: str) -> EvalBatchDeleted:
        raise RuntimeError("evaluation provider is disabled")

    def trends(self, limit: int = 20) -> list[EvalBatchRecord]:
        return []

    def archive(self, request: EvalArchiveRequest) -> EvalArchiveReceipt:
        raise RuntimeError("evaluation provider is disabled")

    def archive_baseline(self, snapshot: EvalBaseline, note: str = "") -> EvalArchiveReceipt:
        raise RuntimeError("evaluation provider is disabled")

    def list_archives(self, limit: int = 20) -> list[EvalArchiveRecord]:
        return []

    def list_performance_results(self, limit: int = 20) -> list[EvalPerformanceRecord]:
        return []

    def get_performance_result(self, result_id: str) -> EvalPerformanceRecord | None:
        return None

    def delete_performance_result(self, result_id: str) -> EvalPerformanceDeleted:
        raise RuntimeError("evaluation provider is disabled")

    def archive_performance_result(self, request: EvalPerformanceArchiveRequest) -> EvalArchiveReceipt:
        raise RuntimeError("evaluation provider is disabled")


class _ResilientEvalProvider:
    """Contain provider failures at the optional module boundary."""

    def __init__(self, provider: EvalProvider) -> None:
        self.provider = provider

    def _invoke(self, name: str, target: Callable[_P, _Result], *args: _P.args, **kwargs: _P.kwargs) -> _Result:
        try:
            return target(*args, **kwargs)
        except Exception:
            logger.warning("[Evals] provider operation %s failed", name, exc_info=True)
            raise

    async def _invoke_async(self, name: str, target: Callable[_P, Awaitable[_Result]], *args: _P.args, **kwargs: _P.kwargs) -> _Result:
        try:
            return await target(*args, **kwargs)
        except Exception:
            logger.warning("[Evals] provider operation %s failed", name, exc_info=True)
            raise

    def list_cases(self) -> list[EvalCase]:
        return self._invoke("list_cases", self.provider.list_cases)

    def get_case(self, case_id: str) -> EvalCase | None:
        return self._invoke("get_case", self.provider.get_case, case_id)

    def get_baseline(self) -> EvalBaseline:
        return self._invoke("get_baseline", self.provider.get_baseline)

    async def run_case(self, case: EvalCase) -> EvalResult:
        return await self._invoke_async("run_case", self.provider.run_case, case)

    def list_results(self, limit: int = 100) -> list[JsonObject]:
        return self._invoke("list_results", self.provider.list_results, limit)

    async def start_batch(self, request: EvalBatchRequest) -> EvalBatch:
        return await self._invoke_async("start_batch", self.provider.start_batch, request)

    def merge_batches(self, request: EvalBatchMergeRequest) -> EvalBatch:
        return self._invoke("merge_batches", self.provider.merge_batches, request)

    def list_batches(self, limit: int = 20) -> list[EvalBatchRecord]:
        return self._invoke("list_batches", self.provider.list_batches, limit)

    def get_batch(self, batch_id: str) -> EvalBatch | None:
        return self._invoke("get_batch", self.provider.get_batch, batch_id)

    def delete_batch(self, batch_id: str) -> EvalBatchDeleted:
        return self._invoke("delete_batch", self.provider.delete_batch, batch_id)

    def trends(self, limit: int = 20) -> list[EvalBatchRecord]:
        return self._invoke("trends", self.provider.trends, limit)

    def archive(self, request: EvalArchiveRequest) -> EvalArchiveReceipt:
        return self._invoke("archive", self.provider.archive, request)

    def archive_baseline(self, snapshot: EvalBaseline, note: str = "") -> EvalArchiveReceipt:
        return self._invoke("archive_baseline", self.provider.archive_baseline, snapshot, note)

    def list_archives(self, limit: int = 20) -> list[EvalArchiveRecord]:
        return self._invoke("list_archives", self.provider.list_archives, limit)

    def list_performance_results(self, limit: int = 20) -> list[EvalPerformanceRecord]:
        return self._invoke("list_performance_results", self.provider.list_performance_results, limit)

    def get_performance_result(self, result_id: str) -> EvalPerformanceRecord | None:
        return self._invoke("get_performance_result", self.provider.get_performance_result, result_id)

    def delete_performance_result(self, result_id: str) -> EvalPerformanceDeleted:
        return self._invoke("delete_performance_result", self.provider.delete_performance_result, result_id)

    def archive_performance_result(self, request: EvalPerformanceArchiveRequest) -> EvalArchiveReceipt:
        return self._invoke("archive_performance_result", self.provider.archive_performance_result, request)

    def __getattr__(self, name: str) -> PluginCallable:
        """Forward additional plugin-owned operations, including Trace Case APIs.

        Declared EvalProvider operations use the typed methods above. Dynamic
        extensions stay confined to this compatibility boundary.
        """
        target = getattr(self.provider, name)
        if not callable(target):
            raise AttributeError(f"evaluation provider operation {name!r} is not callable")
        operation = cast(PluginCallable, target)

        async def safe_async(*args: object, **kwargs: object) -> object:
            try:
                return await cast(Awaitable[object], operation(*args, **kwargs))
            except Exception:
                logger.warning("[Evals] provider operation %s failed", name, exc_info=True)
                raise

        def safe_sync(*args: object, **kwargs: object) -> object:
            return self._invoke(name, operation, *args, **kwargs)

        return safe_async if inspect.iscoroutinefunction(target) else safe_sync


def _load_factory(provider: str) -> PluginCallable:
    """Compatibility hook; actual loading remains owned by PluginManager."""
    from app.agent_base.core.plugins import PluginManager

    return PluginManager.load_factory(provider)


def load_evals(*, settings: PluginConfiguration | None = None, **kwargs: object) -> EvalProvider:
    """Load evaluations through the central extension manager."""
    from app.agent_base.core.plugins import get_plugin_manager

    instance = get_plugin_manager().load_optional(
        "evals",
        settings=settings,
        kwargs=kwargs,
        factory_loader=_load_factory,
    )
    if instance is None:
        return NoOpEvalProvider()
    # The manager validates the dynamically loaded provider against its manifest.
    return _ResilientEvalProvider(cast(EvalProvider, instance))


__all__ = [
    "NoOpEvalProvider",
    "load_evals",
]
