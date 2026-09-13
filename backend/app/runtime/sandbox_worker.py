"""Capability discovery for isolated task workers.

Workers are intentionally separate from language adapters and task resolvers.
An available WSL launcher is not automatically an isolated sandbox: its
network and filesystem capabilities must be reported explicitly.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

from app.runtime.command import (
    ExecutionEnvironmentError,
    LinuxExecutionProfile,
    WslBashExecutor,
    _probe_version_process,
)
from app.runtime.task_contracts import ExecutionPolicy, NetworkPolicy


_SAFE_IMAGE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9./:_@-]*$")
_DIGEST_IMAGE_REF = re.compile(r"@sha256:[0-9a-f]{64}$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class WorkerCapabilities:
    worker_id: str
    filesystem_isolated: bool
    network_isolated: bool
    resource_limits: bool
    process_tree_termination: bool
    resource_limit_kinds: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["resource_limit_kinds"] = list(self.resource_limit_kinds)
        return value


@dataclass(frozen=True, slots=True)
class WorkerStatus:
    worker_id: str
    status: str
    capabilities: WorkerCapabilities
    reason: str = ""
    details: dict[str, object] = field(default_factory=dict)

    @property
    def ready(self) -> bool:
        return self.status == "ready"

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["capabilities"] = self.capabilities.to_dict()
        return value


class SandboxWorker(Protocol):
    """Preflight and capability contract for a task execution worker."""

    capabilities: WorkerCapabilities

    def preflight(self) -> WorkerStatus: ...

    def supports(self, policy: ExecutionPolicy) -> bool: ...


class WslWorker:
    """Capability adapter for WSL, conservative by default.

    WSL provides a separate Linux process environment, but this adapter does
    not claim filesystem or network isolation unless the deployment explicitly
    configures those guarantees.  The actual process execution remains owned
    by ``WslBashExecutor``/``ExecutionBroker``.
    """

    def __init__(
        self,
        executor: WslBashExecutor | None = None,
        *,
        filesystem_isolated: bool = False,
        network_isolated: bool = False,
        resource_limits: bool = False,
    ) -> None:
        self.executor = executor or WslBashExecutor()
        self.capabilities = WorkerCapabilities(
            worker_id="wsl",
            filesystem_isolated=filesystem_isolated,
            network_isolated=network_isolated,
            resource_limits=resource_limits,
            process_tree_termination=True,
        )

    def preflight(self) -> WorkerStatus:
        try:
            self.executor.preflight()
        except (OSError, ExecutionEnvironmentError, RuntimeError) as exc:
            return WorkerStatus(
                worker_id=self.capabilities.worker_id,
                status="unavailable",
                capabilities=self.capabilities,
                reason=f"{type(exc).__name__}: {exc}",
            )
        return WorkerStatus(
            worker_id=self.capabilities.worker_id,
            status="ready",
            capabilities=self.capabilities,
        )

    def supports(self, policy: ExecutionPolicy) -> bool:
        if policy.sandbox not in {"wsl", "linux-wsl"}:
            return False
        if policy.network is NetworkPolicy.DENY and not self.capabilities.network_isolated:
            return False
        if policy.writable_roots and not self.capabilities.filesystem_isolated:
            return False
        return True


class DockerCommandExecutor:
    """Run literal argv inside a disposable Docker workspace container.

    The host path is mounted at ``/workspace`` and the container always uses
    ``--network none``.  Images must be pulled by deployment ahead of time;
    this executor never performs an implicit network operation on behalf of a
    task.
    """

    profile = LinuxExecutionProfile(
        name="container",
        cwd_note="Commands execute in a disposable Linux container with /workspace mounted.",
    )

    def __init__(
        self,
        workspace_root: str,
        *,
        image: str = "ubuntu:24.04",
        executable: str = "docker",
        preflight_timeout_seconds: float = 10.0,
        require_digest: bool = False,
    ) -> None:
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.image = str(image).strip()
        self.executable = str(executable).strip() or "docker"
        self.preflight_timeout_seconds = max(0.1, float(preflight_timeout_seconds))
        self.require_digest = bool(require_digest)
        self.image_id = ""
        self._preflight_done = False

    def preflight(self) -> None:
        if self._preflight_done:
            return
        if not self.workspace_root.is_dir():
            raise ExecutionEnvironmentError(
                f"container workspace does not exist: {self.workspace_root}"
            )
        if not self.image:
            raise ExecutionEnvironmentError("container image must not be empty")
        if not _SAFE_IMAGE_REF.fullmatch(self.image):
            raise ExecutionEnvironmentError("container image reference contains unsupported characters")
        if self.require_digest and not _DIGEST_IMAGE_REF.search(self.image):
            raise ExecutionEnvironmentError(
                "container image must be pinned by a sha256 digest"
            )
        if not shutil.which(self.executable):
            raise ExecutionEnvironmentError(f"container launcher is unavailable: {self.executable}")
        try:
            result = subprocess.run(
                [self.executable, "info"],
                capture_output=True,
                timeout=self.preflight_timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ExecutionEnvironmentError(
                f"container preflight failed: {type(exc).__name__}: {exc}"
            ) from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or b"").decode(
                "utf-8", errors="replace"
            ).strip()
            raise ExecutionEnvironmentError(
                f"container preflight failed: {detail or f'exit code {result.returncode}'}"
            )
        try:
            image_result = subprocess.run(
                [self.executable, "image", "inspect", self.image, "--format", "{{.Id}}"],
                capture_output=True,
                timeout=self.preflight_timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ExecutionEnvironmentError(
                f"container image inspection failed: {type(exc).__name__}: {exc}"
            ) from exc
        image_id = image_result.stdout or b""
        if isinstance(image_id, bytes):
            image_id = image_id.decode("utf-8", errors="replace")
        self.image_id = str(image_id).strip()
        if image_result.returncode != 0 or not self.image_id:
            detail = (image_result.stderr or image_result.stdout or b"")
            if isinstance(detail, bytes):
                detail = detail.decode("utf-8", errors="replace")
            raise ExecutionEnvironmentError(
                f"container image is not available locally: {str(detail).strip() or self.image}"
            )
        self._preflight_done = True

    def validate_command(self, command: str) -> str | None:
        if any(token in command for token in ("\n", "\r")):
            return "container shell command must be a single line"
        return None

    def normalize_resolved_program(
        self, program: str, args: list[str], cwd: str,
    ) -> tuple[str, list[str]]:
        """Translate host-selected Windows wrappers to the Linux container."""
        if program.lower() == "gradlew.bat":
            candidate = Path(cwd) / "gradlew"
            if candidate.is_file():
                return "./gradlew", args
        return program, args

    def probe_toolchain(self, program: str, cwd: str, *, timeout: float = 10.0):
        return _probe_version_process(
            lambda: self.start_program(program, ["--version"], cwd),
            self.terminate,
            timeout,
        )

    def _container_cwd(self, cwd: str | None) -> str:
        candidate = Path(cwd or self.workspace_root).expanduser().resolve()
        try:
            relative = candidate.relative_to(self.workspace_root)
        except ValueError as exc:
            raise ExecutionEnvironmentError(
                "container cwd is outside the mounted workspace"
            ) from exc
        return "/workspace" if not relative.parts else "/workspace/" + "/".join(relative.parts)

    def _base(self, cwd: str | None, *, resources=None) -> list[str]:
        command = [
            self.executable, "run", "--rm", "--init", "--pull", "never",
            "--network", "none",
            "--read-only",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=256m",
            "--volume", f"{self.workspace_root}:/workspace:rw",
            "--workdir", self._container_cwd(cwd),
        ]
        if resources is not None:
            if resources.cpu_seconds is not None:
                command.extend(["--ulimit", f"cpu={max(1, int(resources.cpu_seconds))}"])
            if resources.memory_mb is not None:
                command.extend(["--memory", f"{int(resources.memory_mb)}m"])
            if resources.max_processes is not None:
                command.extend(["--pids-limit", str(int(resources.max_processes))])
        command.append(self.image)
        return command

    def start_program(self, program: str, args: list[str], cwd: str | None) -> subprocess.Popen:
        self.preflight()
        return subprocess.Popen(
            [*self._base(cwd), program, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )

    def start_program_with_limits(self, program: str, args: list[str], cwd: str | None, resources) -> subprocess.Popen:
        self.preflight()
        return subprocess.Popen(
            [*self._base(cwd, resources=resources), program, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )

    def start(self, command: str, cwd: str | None) -> subprocess.Popen:
        self.preflight()
        return subprocess.Popen(
            [*self._base(cwd), "bash", "-lc", command],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )

    def terminate(self, process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True, timeout=5, check=False,
                )
            else:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except (OSError, subprocess.SubprocessError):
            process.kill()


class ContainerWorker:
    """Docker worker with explicit workspace and network isolation claims."""

    def __init__(
        self,
        workspace_root: str,
        *,
        image: str = "ubuntu:24.04",
        executable: str = "docker",
        preflight_timeout_seconds: float = 10.0,
        require_digest: bool = False,
    ) -> None:
        self.executor = DockerCommandExecutor(
            workspace_root,
            image=image,
            executable=executable,
            preflight_timeout_seconds=preflight_timeout_seconds,
            require_digest=require_digest,
        )
        self.capabilities = WorkerCapabilities(
            worker_id="container",
            filesystem_isolated=True,
            network_isolated=True,
            resource_limits=False,
            process_tree_termination=True,
            resource_limit_kinds=("cpu_seconds", "memory_mb", "max_processes"),
        )

    def preflight(self) -> WorkerStatus:
        try:
            self.executor.preflight()
        except (OSError, ExecutionEnvironmentError, RuntimeError) as exc:
            return WorkerStatus(
                worker_id=self.capabilities.worker_id,
                status="unavailable",
                capabilities=self.capabilities,
                reason=f"{type(exc).__name__}: {exc}",
                details={"image": self.executor.image, "image_id": self.executor.image_id},
            )
        return WorkerStatus(
            worker_id=self.capabilities.worker_id,
            status="ready",
            capabilities=self.capabilities,
            details={"image": self.executor.image, "image_id": self.executor.image_id},
        )

    def supports(self, policy: ExecutionPolicy) -> bool:
        if policy.sandbox not in {"container", "docker"}:
            return False
        if policy.network is NetworkPolicy.DENY and not self.capabilities.network_isolated:
            return False
        if policy.writable_roots and not self.capabilities.filesystem_isolated:
            return False
        return True


# Public name follows the worker terminology; keep the Docker-prefixed alias
# for callers that want to make the concrete runtime dependency explicit.
ContainerCommandExecutor = DockerCommandExecutor


__all__ = [
    "ContainerCommandExecutor", "ContainerWorker", "DockerCommandExecutor", "SandboxWorker",
    "WorkerCapabilities", "WorkerStatus", "WslWorker",
]
