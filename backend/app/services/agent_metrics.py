"""轻量级 Agent 指标收集器；不依赖外部监控服务，便于后续接 Prometheus。"""

from __future__ import annotations

import threading
import time
from collections import Counter


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

