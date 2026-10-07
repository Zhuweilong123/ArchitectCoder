"""Public data contract for the evaluation extension.

Evaluation catalogs, runners, checkers and batch persistence are extension
concerns. The host adapter uses this contract to validate provider calls.
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field


class EvalBatchRequest(BaseModel):
    """Provider-neutral request for starting an evaluation batch."""

    suite: str = ""
    case_ids: list[str] = Field(default_factory=list, max_length=200)
    version: str = Field(default="working-tree", min_length=1, max_length=100)
    label: str = Field(default="", max_length=200)


class EvalBatchMergeRequest(BaseModel):
    """Request for converting complete baseline batches into a performance result."""

    batch_ids: list[str] = Field(min_length=1, max_length=20)
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

    def merge_batches(self, request: Any) -> Any: ...

    def list_batches(self, limit: int = 20) -> list[dict[str, Any]]: ...

    def get_batch(self, batch_id: str) -> Any | None: ...

    def delete_batch(self, batch_id: str) -> dict[str, Any]: ...

    def trends(self, limit: int = 20) -> list[dict[str, Any]]: ...

    def archive(self, request: Any) -> dict[str, Any]: ...

    def archive_baseline(self, snapshot: dict[str, Any], note: str = "") -> dict[str, Any]: ...

    def list_archives(self, limit: int = 20) -> list[dict[str, Any]]: ...

    def list_performance_results(self, limit: int = 20) -> list[dict[str, Any]]: ...

    def get_performance_result(self, result_id: str) -> dict[str, Any] | None: ...

    def delete_performance_result(self, result_id: str) -> dict[str, Any]: ...

    def archive_performance_result(self, request: Any) -> dict[str, Any]: ...
