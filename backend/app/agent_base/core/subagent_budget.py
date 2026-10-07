"""Cumulative request admission, independent of context compression and plugins."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RequestAllowance:
    input_tokens: int
    output_tokens: int
    finalize: bool = False


@dataclass
class CumulativeTokenBudget:
    limit: int
    summary_output_tokens: int
    finalization_reserve_tokens: int = 12000
    input_estimate_ratio: float = 1.25

    def remaining(self, used: int) -> int:
        return max(0, self.limit - used)

    def allowance(self, used: int, estimated_input: int, *, finalizing: bool) -> RequestAllowance:
        remaining = self.remaining(used)
        input_tokens = int(estimated_input * self.input_estimate_ratio) + 256
        target = self.summary_output_tokens if finalizing else min(4096, max(256, remaining // 4))
        # A summary needs another input request as well as output tokens.
        summary_reserve = (
            int(min(estimated_input, 6000) * self.input_estimate_ratio)
            + 256 + self.summary_output_tokens
        )
        summary_reserve = max(summary_reserve, min(self.finalization_reserve_tokens, remaining // 2))
        if not finalizing and input_tokens + target + summary_reserve >= remaining:
            return RequestAllowance(input_tokens, 0, finalize=True)
        return RequestAllowance(input_tokens, max(0, min(target, remaining - input_tokens)))

    def observe_input(self, actual: int, estimated: int) -> None:
        if actual > 0:
            self.input_estimate_ratio = max(
                self.input_estimate_ratio, actual / max(1, estimated) * 1.1,
            )
