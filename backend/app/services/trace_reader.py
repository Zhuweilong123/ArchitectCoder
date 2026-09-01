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
from datetime import datetime

from app.services.chat_trace import EVT_CONTEXT_COMPACTED, _chat_log_dir


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
    checkpoint: str = ""
    for e in data["events"]:
        et = e.get("event_type")
        if et == "user_message":
            if pending_user is not None:
                history.append({"role": "user", "content": pending_user})
            pending_user = e.get("message", "")
        elif et == "done":
            if pending_user is not None:
                history.append({"role": "user", "content": pending_user})
                pending_user = None
            history.append({"role": "assistant", "content": e.get("answer", "")})
        elif et == EVT_CONTEXT_COMPACTED:
            checkpoint = str(e.get("summary") or "")
    if pending_user is not None:
        history.append({"role": "user", "content": pending_user})
    if checkpoint:
        history.insert(0, {"role": "summary", "content": checkpoint})
    return history
