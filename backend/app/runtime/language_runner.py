"""Adapt the host execution broker to a synchronous parser runner."""
from __future__ import annotations
import asyncio
import subprocess
import threading
from typing import Any, Callable
from app.runtime.task_contracts import ExecutionPolicy, NetworkPolicy, ResourceLimits, TaskKind, TaskSpec

def broker_command_runner(broker: Any) -> Callable[..., Any]:
    """Adapt an async execution Broker to a synchronous command runner.

    The returned callable never invokes ``subprocess`` itself.  It submits a
    ``TaskSpec`` to the broker and converts the structured evidence into a
    ``CompletedProcess``-compatible object for the AST adapter.  If called
    from an active event loop, the coroutine is isolated in a short-lived
    helper thread so synchronous contract collectors remain safe.
    """

    def run(argv: list[str] | tuple[str, ...], *, cwd: str, capture_output=True,
            timeout: float = 60.0, check: bool = False, **_: Any) -> Any:
        task = TaskSpec(
            task_id="parser.extract",
            kind=TaskKind.CUSTOM,
            argv=tuple(argv),
            network=NetworkPolicy.DENY,
            resources=ResourceLimits(timeout_seconds=float(timeout)),
        )
        policy = ExecutionPolicy(
            sandbox=str(getattr(broker, "sandbox_name", "workspace")),
            network=NetworkPolicy.DENY,
        )

        async def execute() -> Any:
            return await broker.execute(task, cwd, policy=policy)

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            evidence = asyncio.run(execute())
        else:
            result_box: list[Any] = []
            error_box: list[BaseException] = []

            def worker() -> None:
                try:
                    result_box.append(asyncio.run(execute()))
                except BaseException as exc:  # propagate to the caller thread
                    error_box.append(exc)

            thread = threading.Thread(target=worker, name="parser-broker-runner")
            thread.start()
            thread.join(max(float(timeout) + 5.0, 5.0))
            if thread.is_alive():
                raise TimeoutError("Broker-backed parser runner did not return before its deadline")
            if error_box:
                raise error_box[0]
            evidence = result_box[0]
        output = str(getattr(evidence, "output", "") or "")
        success = getattr(evidence, "status", "") == "success"
        return subprocess.CompletedProcess(
            list(argv),
            0 if success else int(getattr(evidence, "exit_code", None) or 1),
            stdout=output.encode("utf-8") if success else b"",
            stderr=b"" if success else output.encode("utf-8"),
        )

    return run
