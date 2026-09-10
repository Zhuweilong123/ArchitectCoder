"""Machine-readable execution outcomes, independent of response wording."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class RunOutcome:
    status: str
    stop_reason: str
    final_answer: str
    total_tokens: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_stop(cls, reason: str, answer: str, *, total_tokens: int = 0,
                  plan_complete: bool = True, verification_failed: bool = False):
        status = {
            "time_limit": "timed_out",
            "llm_timeout": "timed_out",
            "final_summary_timeout": "timed_out",
            "hard_limit_before_next_llm": "budget_exceeded",
            "hard_limit_after_current_tools": "budget_exceeded",
            "emergency_token_limit": "budget_exceeded",
            "emergency_token_limit_after_current_tools": "budget_exceeded",
            "context_hard_limit": "budget_exceeded",
            "reserve_finalization": "budget_exceeded",
            "reserve_finalization_empty_response": "budget_exceeded",
            "tool_call_limit": "partial",
            "incomplete_plan": "partial",
        }.get(reason, "completed" if reason == "model_answer" else "partial")
        if status == "completed" and (not plan_complete or verification_failed):
            status = "partial"
            reason = "incomplete_plan" if not plan_complete else "verification_failed"
        return cls(status, reason, answer, total_tokens)
