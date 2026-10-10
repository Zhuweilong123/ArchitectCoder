"""Session-owned execution state and reconnectable event delivery."""
from __future__ import annotations

import asyncio
import copy
import json
import uuid
from collections import deque
from typing import Any


class ChatConnection:
    """Keep execution alive when its current subscriber disconnects.

    Event cursors belong to an epoch, so a restarted server never mistakes an
    old cursor for an acknowledgement of new events. Delivery and replay share
    a lock to preserve ordering across reconnects.
    """

    def __init__(self) -> None:
        self.owner = uuid.uuid4().hex
        self.epoch = uuid.uuid4().hex
        self.task: asyncio.Task | None = None
        self.preparing = False
        self.stop_requested = False
        self.fallback_review_runs: dict[int, str] = {}
        self.paths = ("", "", "", "", "")
        self._socket: Any = None
        self._lock = asyncio.Lock()
        self.command_lock = asyncio.Lock()
        self._events: deque[dict] = deque(maxlen=2000)
        self._sequence = 0
        self._event_bytes = 0
        self._progress: dict | None = None
        self._terminal: dict | None = None
        self._reviews: dict[int, dict] = {}

    @property
    def running(self) -> bool:
        return self.preparing or (self.task is not None and not self.task.done())

    async def send_json(self, payload: dict) -> None:
        async with self._lock:
            self._sequence += 1
            event = {**copy.deepcopy(payload), "event_seq": self._sequence, "event_epoch": self.epoch}
            kind = event.get("event")
            if kind == "run_started":
                self._progress = self._terminal = None
            elif kind == "progress":
                self._progress = event
            elif kind in {"uml_review", "request_review"}:
                self._reviews[event["review_id"]] = event
            elif kind in {"review_timeout", "review_expired"}:
                self._reviews.pop(event["review_id"], None)
            elif kind in {"done", "stopped", "error"}:
                self._terminal = event
                if kind == "stopped":
                    self._reviews.clear()
            if len(self._events) == self._events.maxlen:
                self._event_bytes -= len(json.dumps(self._events[0], ensure_ascii=False).encode())
            self._events.append(event)
            self._event_bytes += len(json.dumps(event, ensure_ascii=False).encode())
            while self._event_bytes > 16 * 1024 * 1024 and len(self._events) > 1:
                self._event_bytes -= len(json.dumps(self._events.popleft(), ensure_ascii=False).encode())
            if self._socket is not None:
                try:
                    await asyncio.wait_for(self._socket.send_json(event), timeout=5)
                except Exception:
                    self._socket = None

    async def attach(self, socket: Any, cursor: int = 0, epoch: str = "") -> None:
        async with self._lock:
            self._socket = socket
            known_cursor = epoch == self.epoch
            # A fresh page needs current state, not historical design mutations.
            events = list(self._events) if known_cursor else sorted(
                [event for event in [self._progress, *self._reviews.values(),
                                     self._terminal if not self.running else None] if event],
                key=lambda event: event["event_seq"],
            )
            if not known_cursor:
                cursor = 0
            for event in events:
                if event["event_seq"] > cursor:
                    await socket.send_json(event)
            await socket.send_json({
                "event": "session_sync", "running": self.running,
                "stopping": self.stop_requested and self.running,
                "event_epoch": self.epoch,
                "pending_review_ids": list(self._reviews),
                "replay_truncated": bool(known_cursor and self._events and cursor < self._events[0]["event_seq"] - 1),
            })

    def detach(self, socket: Any) -> None:
        if self._socket is socket:
            self._socket = None

    def is_current(self, socket: Any) -> bool:
        return self._socket is socket

    def resolve_review(self, review_id: int) -> None:
        self._reviews.pop(review_id, None)
