"""Evaluation batch values, independent of job scheduling and persistence."""
from pydantic import BaseModel, Field
from .models import EvalResult
from .summary import EvalSummary


class EvalBatch(BaseModel):
    batch_id: str
    agent: str = "devagent"
    suite: str = ""
    version: str
    label: str = ""
    case_ids: list[str]
    status: str = "queued"
    started_at: str = ""
    finished_at: str = ""
    current_case_id: str = ""
    results: list[EvalResult] = Field(default_factory=list)
    summary: EvalSummary = Field(default_factory=EvalSummary)
    error: str = ""
    performance_result_id: str = ""
    source_batch_ids: list[str] = Field(default_factory=list)
