"""Default evaluation provider backed by the local Eval MVP implementation."""

from __future__ import annotations

import json

from .trace_cases import (TraceCaseCaptureRequest, TraceCaseDraftRequest, TraceCaseFactory, TraceCasePublishRequest, TraceCaseReviewRequest)

from .batches import get_batch_manager
from .registry import load_cases
from .runner import EvalRunner


class LocalEvalProvider:
    """Adapter that exposes the current local runner through the Eval Port."""

    def __init__(self, settings=None, results_path=None, **kwargs):
        self.settings = settings
        self.results_path = results_path

    def _runner(self):
        return EvalRunner(self.results_path)

    def list_cases(self):
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

    def get_case(self, case_id: str):
        return load_cases().get(case_id)

    def get_baseline(self):
        from .paths import baseline_path

        path = baseline_path()
        if not path.is_file():
            raise FileNotFoundError(path)
        return json.loads(path.read_text(encoding="utf-8"))

    async def run_case(self, case):
        return await self._runner().run_case(case)

    def list_results(self, limit: int = 100):
        return self._runner().list_results(limit)

    async def start_batch(self, request):
        return await get_batch_manager().start(request)

    def merge_batches(self, request):
        return get_batch_manager().merge(request)

    def list_batches(self, limit: int = 20):
        return get_batch_manager().list_batches(limit)

    def get_batch(self, batch_id: str):
        return get_batch_manager().get(batch_id)

    def delete_batch(self, batch_id: str):
        return get_batch_manager().delete(batch_id)

    def trends(self, limit: int = 20):
        return get_batch_manager().trends(limit)

    def archive(self, request):
        return get_batch_manager().archive(request)

    def archive_baseline(self, snapshot, note: str = ""):
        return get_batch_manager().archive_baseline(snapshot, note)

    def list_archives(self, limit: int = 20):
        return get_batch_manager().list_archives(limit)

    def list_performance_results(self, limit: int = 20):
        from .batches import _eval_root
        from .performance import list_performance_results

        return list_performance_results(_eval_root(), limit)

    def get_performance_result(self, result_id: str):
        from .batches import _eval_root
        from .performance import get_performance_result

        return get_performance_result(_eval_root(), result_id)

    def delete_performance_result(self, result_id: str):
        from .batches import _eval_root
        from .performance import delete_performance_result

        return delete_performance_result(_eval_root(), result_id)

    def archive_performance_result(self, request):
        from .batches import _eval_root, get_batch_manager
        from .performance import archive_performance_result

        return archive_performance_result(
            _eval_root(), request.result_id, request.version, request.note,
            get_batch_manager()._write_archive,
        )


def create(*, settings=None, **kwargs):
    return LocalEvalProvider(settings=settings, **kwargs)
