"""Bounded, concurrent capture of a subprocess's stdout and stderr pipes."""

from __future__ import annotations

import asyncio
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable


DEFAULT_OUTPUT_LIMIT_BYTES = 10 * 1024 * 1024
MAX_OUTPUT_LIMIT_BYTES = 100 * 1024 * 1024
_READ_CHUNK_BYTES = 64 * 1024


def normalize_output_limit(value: int) -> int:
    return max(1024, min(int(value), MAX_OUTPUT_LIMIT_BYTES))


@dataclass(frozen=True)
class ProcessOutput:
    stdout: bytes
    stderr: bytes
    reason: str  # completed | canceled | timeout | output_limit
    exit_code: int | None
    collected_bytes: int
    limit_bytes: int


async def collect_process_output(
    process: subprocess.Popen,
    *,
    terminate: Callable[[subprocess.Popen], None],
    timeout: float,
    stop_check: Callable[[], bool],
    output_limit: int = DEFAULT_OUTPUT_LIMIT_BYTES,
) -> ProcessOutput:
    """Drain both pipes without retaining more than ``output_limit`` bytes.

    The first byte beyond the limit stops collection and terminates the
    process. The returned output is explicitly incomplete in that case.
    """
    if process.stdout is None or process.stderr is None:
        raise ValueError("process stdout and stderr must be pipes")
    limit = normalize_output_limit(output_limit)
    lock = threading.Lock()
    exceeded = threading.Event()
    buffers = (bytearray(), bytearray())
    collected = 0

    def read_pipe(pipe, buffer: bytearray) -> None:
        nonlocal collected
        while not exceeded.is_set():
            chunk = pipe.read(_READ_CHUNK_BYTES)
            if not chunk:
                break
            with lock:
                available = limit - collected
                if available > 0:
                    kept = chunk[:available]
                    buffer.extend(kept)
                    collected += len(kept)
                if len(chunk) > available:
                    exceeded.set()

    jobs = [
        asyncio.create_task(asyncio.to_thread(read_pipe, process.stdout, buffers[0])),
        asyncio.create_task(asyncio.to_thread(read_pipe, process.stderr, buffers[1])),
        asyncio.create_task(asyncio.to_thread(process.wait)),
    ]
    joined = asyncio.gather(*jobs)
    deadline = time.monotonic() + timeout
    reason = "completed"
    try:
        while not joined.done():
            if stop_check():
                reason = "canceled"
            elif exceeded.is_set():
                reason = "output_limit"
            elif time.monotonic() >= deadline:
                reason = "timeout"
            if reason != "completed":
                terminate(process)
                break
            await asyncio.sleep(0.05)
        await asyncio.shield(joined)
        if reason == "completed" and exceeded.is_set():
            reason = "output_limit"
    except asyncio.CancelledError:
        terminate(process)
        await asyncio.shield(joined)
        raise
    except BaseException:
        terminate(process)
        await asyncio.shield(joined)
        raise
    return ProcessOutput(
        stdout=bytes(buffers[0]), stderr=bytes(buffers[1]),
        reason=reason, exit_code=process.returncode,
        collected_bytes=collected, limit_bytes=limit,
    )
