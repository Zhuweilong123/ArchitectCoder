"""Default evaluation provider backed by the local Eval MVP implementation."""

from __future__ import annotations

import json

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

    def list_batches(self, limit: int = 20):
        return get_batch_manager().list_batches(limit)

    def get_batch(self, batch_id: str):
        return get_batch_manager().get(batch_id)

    def trends(self, limit: int = 20):
        return get_batch_manager().trends(limit)

    def archive(self, request):
        return get_batch_manager().archive(request)

    def archive_baseline(self, snapshot, note: str = ""):
        return get_batch_manager().archive_baseline(snapshot, note)

    def list_archives(self, limit: int = 20):
        return get_batch_manager().list_archives(limit)


def create(*, settings=None, **kwargs):
    return LocalEvalProvider(settings=settings, **kwargs)
