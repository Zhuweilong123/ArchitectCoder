"""轻量级 Agent 指标收集器；不依赖外部监控服务，便于后续接 Prometheus。"""

from __future__ import annotations

import threading
import time
from collections import Counter
import re


class AgentMetrics:
    def __init__(self):
        self._lock = threading.Lock()
        self._counters = Counter()
        self._latency_total_ms = 0.0

    def record_tool(self, tool_name: str, status: str, duration_ms: float = 0.0) -> None:
        with self._lock:
            self._counters["tool_calls_total"] += 1
            self._counters[f"tool_calls_{status}"] += 1
            self._counters[f"tool_{tool_name}_total"] += 1
            self._latency_total_ms += max(duration_ms, 0.0)

    def record_run(self, status: str) -> None:
        with self._lock:
            self._counters["runs_total"] += 1
            self._counters[f"runs_{status}"] += 1

    def record_prompt(
        self,
        prompt_tokens: int,
        *,
        prompt_version: str = "",
        compacted_tokens: int = 0,
    ) -> None:
        """Record prompt sizing without retaining prompt content.

        The counters are intentionally aggregate-only: this makes prompt
        A/B comparisons observable while avoiding another copy of potentially
        sensitive context in process memory or metrics payloads.
        """
        with self._lock:
            self._counters["prompt_builds_total"] += 1
            self._counters["prompt_tokens_total"] += max(int(prompt_tokens or 0), 0)
            self._counters["prompt_compacted_tokens_total"] += max(int(compacted_tokens or 0), 0)
            if prompt_version:
                key = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(prompt_version))[:64]
                self._counters[f"prompt_version_{key}_total"] += 1

    def snapshot(self) -> dict:
        with self._lock:
            result = dict(self._counters)
            result["tool_latency_total_ms"] = round(self._latency_total_ms, 2)
            return result

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._latency_total_ms = 0.0


_METRICS = AgentMetrics()


def get_agent_metrics() -> AgentMetrics:
    return _METRICS
