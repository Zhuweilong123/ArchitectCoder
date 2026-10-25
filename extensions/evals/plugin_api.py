"""Public data contract for the evaluation extension.

Evaluation catalogs, runners, checkers and batch persistence are extension
concerns. The host adapter uses this contract to validate provider calls.
"""

from __future__ import annotations

from typing import Annotated, NotRequired, Protocol, TypedDict

from pydantic import BaseModel, Field

from app.agent_base.host_api.data import JsonObject

from .models import EvalCase as EvalCase, EvalResult as EvalResult
from .batch_models import EvalBatch as EvalBatch


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


_Count = Annotated[int, Field(ge=0)]
_Rate = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class EvalBaselineGroup(TypedDict, total=False):
    name: str
    total: _Count
    passed: _Count
    failed: _Count
    timeout: _Count
    errors: _Count
    pass_rate: _Rate
    average_score: _Rate


class EvalBaseline(TypedDict, total=False):
    agent: str
    label: str
    version: str
    captured_at: str
    source_result: str
    case_ids: list[str]
    case_count: _Count
    passed: _Count
    failed: _Count
    timeout: _Count
    errors: _Count
    budget_finalized: _Count
    pass_rate: _Rate
    average_score: _Rate
    total_duration_ms: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    total_tokens: _Count
    total_tool_calls: _Count
    failure_categories: dict[str, _Count]
    eval_contract: dict[str, str]
    groups: list[EvalBaselineGroup]


class EvalBatchRecord(TypedDict, total=False):
    batch_id: str
    agent: str
    suite: str
    version: str
    label: str
    case_ids: list[str]
    status: str
    started_at: str
    finished_at: str
    current_case_id: str
    results: list[JsonObject]
    summary: JsonObject
    error: str
    performance_result_id: str
    source_batch_ids: list[str]
    source_result_id: str
    created_at: str


class EvalArchiveReceipt(TypedDict):
    archive_id: str
    created_at: str
    path: str
    batch_id: str


class EvalArchiveRecord(TypedDict):
    archive_id: str
    created_at: str
    note: str
    batch_id: str
    agent: str
    version: str
    suite: str
    started_at: str
    finished_at: str
    summary: JsonObject


class EvalPerformanceRecord(TypedDict):
    result_id: str
    file_name: str
    source_path: str
    version: str
    suite: str
    started_at: str
    finished_at: str
    result_count: int
    summary: JsonObject
    archived: bool
    results: NotRequired[list[JsonObject]]


class EvalBatchDeleted(TypedDict):
    batch_id: str


class EvalPerformanceDeleted(TypedDict):
    result_id: str
    path: str


class EvalProvider(Protocol):
    """Provider contract for case execution and result management."""

    def list_cases(self) -> list[EvalCase]: ...

    def get_case(self, case_id: str) -> EvalCase | None: ...

    def get_baseline(self) -> EvalBaseline: ...

    async def run_case(self, case: EvalCase) -> EvalResult: ...

    def list_results(self, limit: int = 100) -> list[JsonObject]: ...

    async def start_batch(self, request: EvalBatchRequest) -> EvalBatch: ...

    def merge_batches(self, request: EvalBatchMergeRequest) -> EvalBatch: ...

    def list_batches(self, limit: int = 20) -> list[EvalBatchRecord]: ...

    def get_batch(self, batch_id: str) -> EvalBatch | None: ...

    def delete_batch(self, batch_id: str) -> EvalBatchDeleted: ...

    def trends(self, limit: int = 20) -> list[EvalBatchRecord]: ...

    def archive(self, request: EvalArchiveRequest) -> EvalArchiveReceipt: ...

    def archive_baseline(self, snapshot: EvalBaseline, note: str = "") -> EvalArchiveReceipt: ...

    def list_archives(self, limit: int = 20) -> list[EvalArchiveRecord]: ...

    def list_performance_results(self, limit: int = 20) -> list[EvalPerformanceRecord]: ...

    def get_performance_result(self, result_id: str) -> EvalPerformanceRecord | None: ...

    def delete_performance_result(self, result_id: str) -> EvalPerformanceDeleted: ...

    def archive_performance_result(self, request: EvalPerformanceArchiveRequest) -> EvalArchiveReceipt: ...
