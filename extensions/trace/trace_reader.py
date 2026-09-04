"""TraceReader — 读取并解析 temp/chat_log/trace_*.jsonl。

只读、无副作用。供 TraceViewer（浏览 / 读取）与后续回放驱动复用，
避免各消费方各自重写 JSONL 解析逻辑。

产物约定（与 chat_trace.py 对齐）：
  - 文件名: trace_{session_id}.jsonl
  - 每行一个 JSON 事件，字段含 event_type / span_id / ts_ms / monotonic_ns 等。
"""

from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime

from extensions.trace.chat_trace import (
    EVT_CONTEXT_COMPACTED,
    EVT_TASK_SUMMARY,
    _chat_log_dir,
)


def _trace_dir() -> str:
    return _chat_log_dir()


def _sanitize_session_id(session_id: str) -> str:
    """仅保留安全字符，防止路径穿越。session_id 通常为时间戳 / uuid / 十六进制。"""
    return "".join(c for c in session_id if c.isalnum() or c in "-_.")


def _ts_of(line: str):
    """从一行 JSON 提取 ts_ms；解析失败返回 None（不抛）。"""
    try:
        return json.loads(line).get("ts_ms")
    except Exception:
        return None


def _peek(path: str) -> dict:
    """轻量读取文件元数据：事件数 + 首/尾事件时间戳 + 会话主题（首条 user 消息）。"""
    events = 0
    first_ts = None
    last_ts = None
    title = ""
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            events += 1
            try:
                obj = json.loads(line)
            except Exception:
                continue
            ts = obj.get("ts_ms")
            if first_ts is None:
                first_ts = ts
            last_ts = ts
            if not title and obj.get("event_type") == "user_message":
                title = (obj.get("message") or "")[:40]
    return {
        "events": events,
        "first_ts_ms": first_ts,
        "last_ts_ms": last_ts,
        "title": title,
    }


def list_traces() -> list[dict]:
    """列出所有 trace 文件，按修改时间倒序。"""
    log_dir = _trace_dir()
    if not os.path.isdir(log_dir):
        return []

    traces = []
    for name in os.listdir(log_dir):
        if not name.startswith("trace_") or not name.endswith(".jsonl"):
            continue
        path = os.path.join(log_dir, name)
        try:
            st = os.stat(path)
        except OSError:
            continue

        session_id = name[len("trace_"):-len(".jsonl")]
        traces.append({
            "session_id": session_id,
            "filename": name,
            "size": st.st_size,
            "modified": datetime.fromtimestamp(st.st_mtime).isoformat(),
            **_peek(path),
        })

    traces.sort(key=lambda t: t["modified"], reverse=True)
    return traces


def read_trace(session_id: str) -> dict | None:
    """读取单个 session 的完整事件流（保持文件顺序）。"""
    safe_id = _sanitize_session_id(session_id)
    path = os.path.join(_trace_dir(), f"trace_{safe_id}.jsonl")
    if not os.path.isfile(path):
        return None

    events = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                # 单行损坏不影响整体（尾行可能因写入中断而截断）
                continue

    return {"session_id": safe_id, "events": events}


def summarize_trace(session_id: str) -> dict | None:
    """Return privacy-preserving counters for Prompt and runtime comparison.

    The summary intentionally excludes messages, tool arguments, observations,
    and answers.  It is suitable for release gates and prompt-size dashboards
    without duplicating trace payloads in an API response.
    """
    data = read_trace(session_id)
    if data is None:
        return None

    events = data["events"]
    event_counts = Counter(str(event.get("event_type", "unknown")) for event in events)
    usage_totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    runtime_totals = {
        "llm_duration_ms": 0.0,
        "tool_duration_ms": 0.0,
        "max_tool_duration_ms": 0.0,
    }
    prompt_versions: Counter[str] = Counter()
    prompt_builds = 0
    prompt_chars = 0
    prompt_tokens = 0

    for event in events:
        if event.get("event_type") == "llm_response":
            usage = event.get("usage") or {}
            for key in usage_totals:
                fallback = "input_tokens" if key == "prompt_tokens" else key
                usage_totals[key] += int(usage.get(key, usage.get(fallback, 0)) or 0)
            runtime_totals["llm_duration_ms"] += float(event.get("duration_ms", 0) or 0)
        if event.get("event_type") == "tool_result":
            duration_ms = float(event.get("duration_ms", 0) or 0)
            runtime_totals["tool_duration_ms"] += duration_ms
            runtime_totals["max_tool_duration_ms"] = max(
                runtime_totals["max_tool_duration_ms"], duration_ms,
            )
        if event.get("event_type") != "prompt_context":
            continue
        prompt_builds += 1
        version = str(event.get("prompt_version") or "unknown")
        prompt_versions[version] += 1
        static_report = event.get("static_prompt") or {}
        if isinstance(static_report, dict):
            prompt_chars += int(static_report.get("chars", 0) or 0)
            prompt_tokens += int(static_report.get("estimated_tokens", 0) or 0)
        prompt_chars += int(event.get("total_chars", 0) or 0)
        prompt_tokens += int(event.get("estimated_tokens", 0) or 0)

    return {
        "session_id": data["session_id"],
        "events": len(events),
        "event_counts": dict(event_counts),
        "turns": event_counts.get("user_message", 0),
        "llm_requests": event_counts.get("llm_request", 0),
        "llm_responses": event_counts.get("llm_response", 0),
        "llm_usage": usage_totals,
        "runtime": {
            key: round(value, 1) for key, value in runtime_totals.items()
        },
        "tool_calls": event_counts.get("tool_call", 0),
        "tool_results": event_counts.get("tool_result", 0),
        "tool_errors": sum(
            bool(event.get("error"))
            for event in events if event.get("event_type") == "tool_result"
        ),
        "context_compactions": event_counts.get("context_compacted", 0),
        "prompt": {
            "builds": prompt_builds,
            "versions": dict(prompt_versions),
            "chars": prompt_chars,
            "estimated_tokens": prompt_tokens,
        },
    }


def reconstruct_history(session_id: str) -> list[dict] | None:
    """从 trace 重建会话历史（结论级：user_message + done 交错）。

    返回 ``[{role: 'user'|'assistant', content: str}, ...]``，按对话顺序排列；
    该 session 无 trace 时返回 None。

    说明：agent 的 ``_history`` 只存 user/assistant 摘要（不含逐步工具步骤），
    故此处仅重建这两类。无 done 的轮次（错误/超步）不会补 assistant 兜底回复。
    """
    data = read_trace(session_id)
    if data is None:
        return None

    history: list[dict] = []
    pending_user: str | None = None
    pending_task_summaries: list[dict] = []
    checkpoint: str = ""
    for e in data["events"]:
        et = e.get("event_type")
        if et == "user_message":
            if pending_user is not None:
                history.append({"role": "user", "content": pending_user})
                history.extend(pending_task_summaries)
            pending_task_summaries = []
            pending_user = e.get("message", "")
        elif et == "done":
            if pending_user is not None:
                history.append({"role": "user", "content": pending_user})
                pending_user = None
            history.append({"role": "assistant", "content": e.get("answer", "")})
            history.extend(pending_task_summaries)
            pending_task_summaries = []
        elif et == EVT_CONTEXT_COMPACTED:
            checkpoint = str(e.get("summary") or "")
        elif et == EVT_TASK_SUMMARY:
            summary = str(e.get("summary") or "").strip()
            if summary:
                pending_task_summaries.append({
                    "role": "summary",
                    "content": summary,
                    "metadata": {
                        "kind": "task_execution",
                        "status": str(e.get("status") or ""),
                        "tool_call_count": e.get("tool_call_count", 0),
                    },
                })
    if pending_user is not None:
        history.append({"role": "user", "content": pending_user})
        history.extend(pending_task_summaries)
    if checkpoint:
        history.insert(0, {
            "role": "summary",
            "content": checkpoint,
        })
    return history
