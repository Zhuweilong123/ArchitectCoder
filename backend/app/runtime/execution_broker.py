"""Execution broker boundary for language-neutral project tasks.

The broker consumes a resolved :class:`TaskSpec` and returns structured
evidence.  It deliberately does not contain language names or a task map.  A
restricted local implementation is provided for the migration period; WSL
and container workers can implement the same protocol later.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import replace
from pathlib import Path
from typing import Callable, Protocol

from app.runtime.command import CommandExecutor, ExecutionEnvironmentError, WslBashExecutor
from app.runtime.encoding import decode_process_output
from app.runtime.task_contracts import (
    ApprovalClass,
    ExecutionEvidence,
    ExecutionPolicy,
    NetworkPolicy,
    TaskSpec,
)
from app.runtime.sandbox_worker import ContainerWorker, SandboxWorker, WslWorker


class ExecutionBroker(Protocol):
    """Run a resolved task inside a selected execution boundary."""

    async def execute(
        self,
        task: TaskSpec,
        cwd: str,
        *,
        policy: ExecutionPolicy | None = None,
    ) -> ExecutionEvidence: ...


class LocalExecutionBroker:
    """Execute tasks through an injected executor and bounded workspace roots.

    This is intentionally a migration backend, not a claim of OS-level
    isolation.  It enforces the task contract, delegates host-specific command
    validation to ``CommandExecutor``, and exposes the seam used by a future
    container/WSL worker.
    """

    def __init__(
        self,
        executor: CommandExecutor,
        roots: list[str] | tuple[str, ...],
        *,
        output_cap: int = 1_000_000,
        stop_check: Callable[[], bool] | None = None,
    ) -> None:
        self.executor = executor
        self.sandbox_name = "workspace"
        self.roots = tuple(Path(root).resolve() for root in roots if root)
        self.output_cap = max(1024, min(int(output_cap), 1_000_000))
        self.stop_check = stop_check or (lambda: False)

    async def execute(
        self,
        task: TaskSpec,
        cwd: str,
        *,
        policy: ExecutionPolicy | None = None,
    ) -> ExecutionEvidence:
        effective = policy or ExecutionPolicy(
            network=task.network,
            approval=task.approval,
        )
        base = dict(
            task_id=task.task_id,
            toolchain_id=task.toolchain_id,
            command=task.argv,
            cwd=cwd,
            sandbox=effective.sandbox,
            network=effective.network,
        )
        if effective.approval is ApprovalClass.DENY or task.approval is ApprovalClass.DENY:
            return ExecutionEvidence(
                status="blocked", output="task denied by approval policy",
                diagnostics={"category": "approval_denied"}, **base,
            )
        if effective.approval is ApprovalClass.USER_APPROVAL:
            return ExecutionEvidence(
                status="blocked", output="task requires user approval",
                diagnostics={"category": "approval_required"}, **base,
            )
        if effective.network is not NetworkPolicy.DENY:
            return ExecutionEvidence(
                status="blocked", output="local broker does not grant network access",
                diagnostics={"category": "network_policy", "requested": effective.network.value}, **base,
            )
        resolved_cwd = self._resolve_cwd(cwd)
        if resolved_cwd is None:
            return ExecutionEvidence(
                status="blocked", output="cwd is outside the broker workspace roots",
                diagnostics={"category": "path_policy"}, **base,
            )
        validation_error = self._validate_program(task)
        if validation_error:
            category = (
                "toolchain_unavailable"
                if str(validation_error).startswith("toolchain executable ")
                else "executor_policy"
            )
            return ExecutionEvidence(
                status="blocked", output=validation_error,
                diagnostics={"category": category},
                cwd=resolved_cwd, **{key: value for key, value in base.items() if key != "cwd"},
            )
        try:
            self.executor.preflight()
            limited_launcher = getattr(self.executor, "start_program_with_limits", None)
            if callable(limited_launcher):
                proc = await asyncio.to_thread(
                    limited_launcher,
                    task.argv[0], list(task.argv[1:]), resolved_cwd,
                    task.resources,
                )
            else:
                proc = await asyncio.to_thread(
                    self.executor.start_program,
                    task.argv[0], list(task.argv[1:]), resolved_cwd,
                )
        except (OSError, ExecutionEnvironmentError) as exc:
            category = "toolchain_unavailable" if isinstance(exc, FileNotFoundError) else "start_failure"
            return ExecutionEvidence(
                status="failed", output=f"{type(exc).__name__}: {exc}",
                diagnostics={"category": category}, cwd=resolved_cwd,
                **{key: value for key, value in base.items() if key != "cwd"},
            )

        started = time.monotonic()
        communication = asyncio.create_task(asyncio.to_thread(proc.communicate))
        timeout = task.resources.timeout_seconds
        try:
            while not communication.done():
                if self.stop_check():
                    self.executor.terminate(proc)
                    await asyncio.shield(communication)
                    return ExecutionEvidence(
                        status="canceled", output="task canceled",
                        duration_ms=(time.monotonic() - started) * 1000,
                        diagnostics={"category": "canceled"}, cwd=resolved_cwd,
                        **{key: value for key, value in base.items() if key != "cwd"},
                    )
                if time.monotonic() - started >= timeout:
                    self.executor.terminate(proc)
                    await asyncio.shield(communication)
                    return ExecutionEvidence(
                        status="timeout", output=f"task timed out after {timeout:g}s",
                        duration_ms=(time.monotonic() - started) * 1000,
                        timeout_reason="process_timeout", diagnostics={"category": "timeout"},
                        cwd=resolved_cwd,
                        **{key: value for key, value in base.items() if key != "cwd"},
                    )
                await asyncio.sleep(0.05)
            stdout, stderr = await communication
        except asyncio.CancelledError:
            self.executor.terminate(proc)
            await asyncio.shield(communication)
            raise
        output = (decode_process_output(stdout) + decode_process_output(stderr)).strip()
        if len(output) > self.output_cap:
            output = output[: self.output_cap]
        exit_code = proc.returncode
        return ExecutionEvidence(
            status="success" if exit_code == 0 else "failed",
            output=output or "(no output)", exit_code=exit_code,
            duration_ms=(time.monotonic() - started) * 1000,
            diagnostics={"category": "process_exit"}, cwd=resolved_cwd,
            **{key: value for key, value in base.items() if key != "cwd"},
        )

    def _resolve_cwd(self, cwd: str) -> str | None:
        candidate = Path(cwd).expanduser().resolve()
        if not candidate.is_dir():
            return None
        try:
            if any(os.path.commonpath((str(candidate), str(root))) == str(root) for root in self.roots):
                return str(candidate)
        except ValueError:
            return None
        return None

    def _validate_program(self, task: TaskSpec) -> str | None:
        # Resolved project tasks are intentionally not checked against the
        # interactive ``run_program`` executable allowlist.  The selected
        # executor/worker owns PATH resolution and can therefore support a
        # toolchain that was not known when the host application was built.
        # Keep the old validator as a compatibility fallback for executors
        # that have not implemented the resolver-task contract yet.
        resolved_validator = getattr(self.executor, "validate_resolved_program", None)
        if callable(resolved_validator):
            return resolved_validator(task.argv[0], list(task.argv[1:]))
        validator = getattr(self.executor, "validate_program", None)
        if not callable(validator):
            return None
        return validator(task.argv[0], list(task.argv[1:]))


class WorkerExecutionBroker:
    """Run tasks only when a worker proves it satisfies the requested policy.

    Worker policy checks and local process orchestration are separate
    responsibilities.  Composition keeps the worker boundary independent of
    the migration-era local broker while preserving the same execution
    protocol and public diagnostics.
    """

    def __init__(
        self,
        executor: CommandExecutor,
        roots: list[str] | tuple[str, ...],
        *,
        worker: SandboxWorker,
        output_cap: int = 1_000_000,
        stop_check: Callable[[], bool] | None = None,
    ) -> None:
        self.worker = worker
        # The worker owns the process boundary.  A worker that exposes a
        # dedicated executor must never be checked for capabilities and then
        # silently fall back to the host executor.
        worker_executor = getattr(worker, "executor", None) or executor
        self.executor = worker_executor
        self.sandbox_name = worker.capabilities.worker_id
        self._local_broker = LocalExecutionBroker(
            worker_executor,
            roots,
            output_cap=output_cap,
            stop_check=stop_check,
        )

    async def execute(
        self,
        task: TaskSpec,
        cwd: str,
        *,
        policy: ExecutionPolicy | None = None,
    ) -> ExecutionEvidence:
        effective = policy or ExecutionPolicy(
            network=task.network,
            approval=task.approval,
        )
        status = self.worker.preflight()
        base = dict(
            task_id=task.task_id,
            toolchain_id=task.toolchain_id,
            command=task.argv,
            cwd=cwd,
            sandbox=effective.sandbox,
            network=effective.network,
        )
        if not status.ready:
            return ExecutionEvidence(
                status="blocked", output=status.reason or "sandbox worker unavailable",
                diagnostics={"category": "sandbox_unavailable", "worker": status.to_dict()},
                **base,
            )
        if not self.worker.supports(effective):
            return ExecutionEvidence(
                status="blocked", output="sandbox worker lacks requested capabilities",
                diagnostics={"category": "sandbox_capability_missing", "worker": status.to_dict()},
                **base,
            )
        limits = task.resources
        requested_limits = {
            "cpu_seconds": limits.cpu_seconds,
            "memory_mb": limits.memory_mb,
            "disk_mb": limits.disk_mb,
            "max_processes": limits.max_processes,
        }
        supported_kinds = set(status.capabilities.resource_limit_kinds)
        unsupported_limits = {
            name: value for name, value in requested_limits.items()
            if value is not None and (
                (supported_kinds and name not in supported_kinds)
                or (not supported_kinds and not status.capabilities.resource_limits)
            )
        }
        if unsupported_limits:
            return ExecutionEvidence(
                status="blocked", output="sandbox worker cannot enforce requested resource limits",
                diagnostics={
                    "category": "resource_policy",
                    "worker": status.to_dict(),
                    "requested": requested_limits,
                    "unsupported": unsupported_limits,
                },
                **base,
            )
        evidence = await self._local_broker.execute(task, cwd, policy=effective)
        return replace(
            evidence,
            diagnostics={
                **dict(evidence.diagnostics),
                "worker": status.to_dict(),
            },
        )


def build_execution_broker(
    settings,
    executor: CommandExecutor,
    roots: list[str] | tuple[str, ...],
    *,
    stop_check: Callable[[], bool] | None = None,
) -> ExecutionBroker:
    """Build the configured broker without silently weakening isolation.

    ``local`` preserves the migration behavior.  ``container`` owns its
    Docker executor and therefore cannot accidentally execute on the host.
    ``wsl`` is exposed for capability probing, but the default WSL worker does
    not claim isolation and will fail closed until deployment supplies those
    guarantees.
    """
    mode = str(getattr(settings, "agent_execution_worker", "local") or "local").lower()
    if mode == "container":
        if not roots:
            raise ValueError("container worker requires a workspace root")
        worker = ContainerWorker(
            roots[0],
            image=getattr(settings, "agent_container_image", "ubuntu:24.04"),
            executable=getattr(settings, "agent_container_executable", "docker"),
            preflight_timeout_seconds=getattr(
                settings, "agent_container_preflight_timeout_seconds", 10.0,
            ),
            require_digest=getattr(settings, "agent_container_require_digest", False),
        )
        return WorkerExecutionBroker(
            worker.executor, roots, worker=worker, stop_check=stop_check,
        )
    if mode == "wsl":
        worker = WslWorker(
            WslBashExecutor(
                distribution=getattr(settings, "agent_wsl_distribution", ""),
                executable=getattr(settings, "agent_wsl_executable", "wsl.exe"),
                preflight_timeout_seconds=getattr(
                    settings, "agent_wsl_preflight_timeout_seconds", 20.0,
                ),
            ),
        )
        return WorkerExecutionBroker(
            worker.executor, roots, worker=worker, stop_check=stop_check,
        )
    if mode != "local":
        raise ValueError(f"unsupported execution worker: {mode}")
    return LocalExecutionBroker(executor, roots, stop_check=stop_check)


__all__ = [
    "ExecutionBroker", "LocalExecutionBroker", "WorkerExecutionBroker",
    "build_execution_broker",
]
