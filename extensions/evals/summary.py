"""Evaluation-result aggregation shared by batch and performance views."""

from __future__ import annotations

from collections import Counter

from pydantic import BaseModel, Field

from .models import EvalResult


class EvalSummary(BaseModel):
    total: int = 0
    completed: int = 0
    passed: int = 0
    failed: int = 0
    timeout: int = 0
    budget_exceeded: int = 0
    budget_finalized: int = 0
    errors: int = 0
    pass_rate: float = 0.0
    average_score: float = 0.0
    average_duration_ms: float = 0.0
    total_tokens: int = 0
    total_tool_calls: int = 0
    prompt_tokens: int = 0
    cached_prompt_tokens: int = 0
    prompt_cache_requests: int = 0
    prompt_cache_hit_rate: float | None = None
    prompt_prefix_chars: int = 0
    reused_prompt_prefix_chars: int = 0
    prompt_prefix_requests: int = 0
    prompt_prefix_reuse_rate: float | None = None
    failure_categories: dict[str, int] = Field(default_factory=dict)


def summarize(results: list[EvalResult], total: int | None = None) -> EvalSummary:
    """Build the aggregate used by both live batches and persisted results."""
    completed = len(results)
    passed = sum(
        bool(getattr(item, "passed", item.status == "passed"))
        for item in results
    )
    failed = sum(
        item.status == "failed"
        or (item.status == "budget_finalized" and not bool(getattr(item, "passed", False)))
        for item in results
    )
    timeout = sum(item.status == "timeout" for item in results)
    budget_exceeded = sum(item.status == "budget_exceeded" for item in results)
    budget_finalized = sum(item.status == "budget_finalized" for item in results)
    errors = sum(item.status == "error" for item in results)
    failure_categories = Counter(
        str(getattr(item, "failure_category", "none") or "none")
        for item in results
        if str(getattr(item, "failure_category", "none") or "none") != "none"
    )
    prompt_tokens = sum(max(0, int(getattr(item, "prompt_tokens", 0) or 0)) for item in results)
    cached_prompt_tokens = sum(max(0, int(getattr(item, "cached_prompt_tokens", 0) or 0)) for item in results)
    prompt_cache_requests = sum(max(0, int(getattr(item, "prompt_cache_requests", 0) or 0)) for item in results)
    prompt_prefix_chars = sum(max(0, int(getattr(item, "prompt_prefix_chars", 0) or 0)) for item in results)
    reused_prompt_prefix_chars = sum(max(0, int(getattr(item, "reused_prompt_prefix_chars", 0) or 0)) for item in results)
    prompt_prefix_requests = sum(max(0, int(getattr(item, "prompt_prefix_requests", 0) or 0)) for item in results)
    return EvalSummary(
        total=total if total is not None else completed,
        completed=completed,
        passed=passed,
        failed=failed,
        timeout=timeout,
        budget_exceeded=budget_exceeded,
        budget_finalized=budget_finalized,
        errors=errors,
        pass_rate=round(passed / completed, 4) if completed else 0.0,
        average_score=round(sum(item.score for item in results) / completed, 4) if completed else 0.0,
        average_duration_ms=round(sum(item.duration_ms for item in results) / completed, 1) if completed else 0.0,
        total_tokens=sum(item.total_tokens for item in results),
        total_tool_calls=sum(item.tool_calls for item in results),
        prompt_tokens=prompt_tokens,
        cached_prompt_tokens=cached_prompt_tokens,
        prompt_cache_requests=prompt_cache_requests,
        prompt_cache_hit_rate=(
            round(cached_prompt_tokens / prompt_tokens, 4)
            if prompt_cache_requests > 0 and prompt_tokens > 0 else None
        ),
        prompt_prefix_chars=prompt_prefix_chars,
        reused_prompt_prefix_chars=reused_prompt_prefix_chars,
        prompt_prefix_requests=prompt_prefix_requests,
        prompt_prefix_reuse_rate=(
            round(reused_prompt_prefix_chars / prompt_prefix_chars, 4)
            if prompt_prefix_requests > 0 and prompt_prefix_chars > 0 else None
        ),
        failure_categories=dict(sorted(failure_categories.items())),
    )
