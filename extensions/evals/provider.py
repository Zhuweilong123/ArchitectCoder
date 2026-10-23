"""Default evaluation provider backed by the local Eval MVP implementation."""

from __future__ import annotations

from app.agent_base.host_api.data import JsonObject
from extensions.evals.plugin_api import (
    EvalCase, EvalResult, EvalBatch, EvalBatchRequest, EvalBatchMergeRequest,
    EvalArchiveRequest, EvalPerformanceArchiveRequest, EvalBaseline, EvalBatchRecord,
    EvalArchiveReceipt, EvalArchiveRecord, EvalPerformanceRecord, EvalBatchDeleted,
    EvalPerformanceDeleted,
)

from .trace_cases import (TraceCaseCaptureRequest, TraceCaseDraftRequest, TraceCaseFactory, TraceCasePublishRequest, TraceCaseReviewRequest)

from .batches import get_batch_manager
from .registry import load_cases
from .persistence import read_baseline
from .runner import EvalRunner


class LocalEvalProvider:
    """Adapter that exposes the current local runner through the Eval Port."""

    def __init__(self, settings=None, results_path=None, **kwargs):
        self.settings = settings
        self.results_path = results_path

    def _runner(self):
        return EvalRunner(self.results_path)

    def list_cases(self) -> list[EvalCase]:
        return list(load_cases().values())

    def _trace_case_factory(self):
        return TraceCaseFactory(self._runner())

    def list_trace_case_projects(self):
        return self._trace_case_factory().list_projects()

    def list_trace_case_drafts(self):
        return self._trace_case_factory().list_drafts()

    def delete_trace_case_draft(self, draft_id: str):
        return self._trace_case_factory().delete_draft(draft_id)

    async def create_trace_case_draft(self, request: TraceCaseDraftRequest):
        return await self._trace_case_factory().create(request)

    def get_trace_case_draft(self, draft_id: str):
        return self._trace_case_factory().get(draft_id)

    def review_trace_case_draft(self, draft_id: str, request: TraceCaseReviewRequest):
        return self._trace_case_factory().review(draft_id, request)

    def capture_trace_case_fixture(self, draft_id: str, request: TraceCaseCaptureRequest):
        return self._trace_case_factory().capture(draft_id, request)

    def preview_trace_case_fixture(self, draft_id: str, request: TraceCaseCaptureRequest):
        return self._trace_case_factory().preview(draft_id, request)

    async def validate_trace_case_draft(self, draft_id: str):
        return await self._trace_case_factory().validate(draft_id)

    def publish_trace_case_draft(self, draft_id: str, request: TraceCasePublishRequest):
        return self._trace_case_factory().publish(draft_id, request)

    def get_case(self, case_id: str) -> EvalCase | None:
        return load_cases().get(case_id)

    def get_baseline(self) -> EvalBaseline:
        from .paths import baseline_path

        path = baseline_path()
        return read_baseline(path)

    async def run_case(self, case: EvalCase) -> EvalResult:
        return await self._runner().run_case(case)

    def list_results(self, limit: int = 100) -> list[JsonObject]:
        return self._runner().list_results(limit)

    async def start_batch(self, request: EvalBatchRequest) -> EvalBatch:
        return await get_batch_manager().start(request)

    def merge_batches(self, request: EvalBatchMergeRequest) -> EvalBatch:
        return get_batch_manager().merge(request)

    def list_batches(self, limit: int = 20) -> list[EvalBatchRecord]:
        return get_batch_manager().list_batches(limit)

    def get_batch(self, batch_id: str) -> EvalBatch | None:
        return get_batch_manager().get(batch_id)

    def delete_batch(self, batch_id: str) -> EvalBatchDeleted:
        return get_batch_manager().delete(batch_id)

    def trends(self, limit: int = 20) -> list[EvalBatchRecord]:
        return get_batch_manager().trends(limit)

    def archive(self, request: EvalArchiveRequest) -> EvalArchiveReceipt:
        return get_batch_manager().archive(request)

    def archive_baseline(self, snapshot: EvalBaseline, note: str = "") -> EvalArchiveReceipt:
        return get_batch_manager().archive_baseline(snapshot, note)

    def list_archives(self, limit: int = 20) -> list[EvalArchiveRecord]:
        return get_batch_manager().list_archives(limit)

    def list_performance_results(self, limit: int = 20) -> list[EvalPerformanceRecord]:
        from .batches import _eval_root
        from .performance import list_performance_results

        return list_performance_results(_eval_root(), limit)

    def get_performance_result(self, result_id: str) -> EvalPerformanceRecord | None:
        from .batches import _eval_root
        from .performance import get_performance_result

        return get_performance_result(_eval_root(), result_id)

    def delete_performance_result(self, result_id: str) -> EvalPerformanceDeleted:
        from .batches import _eval_root
        from .performance import delete_performance_result

        return delete_performance_result(_eval_root(), result_id)

    def archive_performance_result(self, request: EvalPerformanceArchiveRequest) -> EvalArchiveReceipt:
        from .batches import _eval_root, get_batch_manager
        from .performance import archive_performance_result

        return archive_performance_result(
            _eval_root(), request.result_id, request.version, request.note,
            get_batch_manager()._write_archive,
        )


def create(*, settings=None, **kwargs):
    return LocalEvalProvider(settings=settings, **kwargs)
