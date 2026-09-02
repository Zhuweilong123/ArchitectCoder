"""One Linux command contract, with WSL as the Windows host adapter.

The agent never has to infer a host shell.  It always writes POSIX commands;
this module owns the host-specific launch and workspace-path translation.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass
from typing import Protocol


class ExecutionEnvironmentError(RuntimeError):
    """The configured command environment cannot safely execute a command."""


@dataclass(frozen=True)
class LinuxExecutionProfile:
    name: str
    cwd_note: str

    @property
    def tool_description(self) -> str:
        return (
            "Run one Linux/POSIX bash command in a configured workspace directory. "
            f"{self.cwd_note} "
            "Use cwd='source', 'test', 'design', or 'workspace' instead of cd. "
            "Use POSIX commands only (for example ls, find, grep, pytest, npm, git); "
            "do not invoke cmd.exe, PowerShell, wsl.exe, or Windows-only commands. "
            "Do not chain commands, use pipes/redirection, nested shells, or inline interpreter code. "
            "Choose the available filesystem tool that best fits the operation. "
            "High-risk commands are denied; sensitive commands require approval."
        )


class CommandExecutor(Protocol):
    """Host adapter for the agent's single Linux command contract."""

    profile: LinuxExecutionProfile

    def preflight(self) -> None: ...

    def validate_command(self, command: str) -> str | None: ...

    def start(self, command: str, cwd: str | None) -> subprocess.Popen: ...

    def terminate(self, process: subprocess.Popen) -> None: ...


class HostShellExecutor:
    """Compatibility adapter for direct tool reuse outside production assembly.

    Production DevAgent construction always injects a Linux executor.  Keeping
    this adapter separate prevents standalone tests and integrations from
    silently acquiring the production WSL prerequisite.
    """

    profile = LinuxExecutionProfile(
        name="host-compatibility",
        cwd_note="The host adapter owns working-directory handling.",
    )

    def validate_command(self, command: str) -> str | None:
        return None

    def preflight(self) -> None:
        return None

    def start(self, command: str, cwd: str | None) -> subprocess.Popen:
        kwargs = {
            "shell": True,
            "cwd": cwd,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        return subprocess.Popen(command, **kwargs)

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


class NativeLinuxBashExecutor:
    """Run the Linux command contract directly on a Linux host."""

    profile = LinuxExecutionProfile(
        name="linux-native",
        cwd_note="The supplied working directory is a native Linux path.",
    )

    def validate_command(self, command: str) -> str | None:
        return None

    def preflight(self) -> None:
        if not shutil.which("bash"):
            raise ExecutionEnvironmentError("bash is not available on the Linux host")

    def start(self, command: str, cwd: str | None) -> subprocess.Popen:
        self.preflight()
        bash = shutil.which("bash")
        return subprocess.Popen(
            [bash, "-lc", command],
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )

    def terminate(self, process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except OSError:
            process.kill()


_WINDOWS_PATH = re.compile(r"^([A-Za-z]):[\\/](.*)$")


def windows_path_to_wsl(path: str) -> str:
    """Map a Windows workspace path to its deterministic /mnt mount path."""
    normalized = str(path or "").strip()
    if normalized.startswith("/mnt/"):
        return normalized
    match = _WINDOWS_PATH.match(normalized)
    if not match:
        raise ExecutionEnvironmentError(
            f"WSL requires an absolute drive path for cwd, got: {path!r}"
        )
    drive, remainder = match.groups()
    return f"/mnt/{drive.lower()}/{remainder.replace(chr(92), '/') }"


def _decode_wsl_output(data: bytes) -> str:
    """Decode WSL diagnostics from either UTF-8 or Windows UTF-16LE."""
    if b"\x00" in data:
        return data.decode("utf-16le", errors="replace")
    return data.decode("utf-8", errors="replace")


class WslBashExecutor:
    """Launch POSIX commands in one configured WSL distribution."""

    profile = LinuxExecutionProfile(
        name="linux-wsl",
        cwd_note=(
            "This is a WSL Linux environment. The host working directory is mapped "
            "for you; never write /mnt paths or Windows drive paths in the command."
        ),
    )

    def __init__(
        self,
        *,
        distribution: str = "",
        executable: str = "wsl.exe",
        preflight_timeout_seconds: float = 20.0,
    ) -> None:
        self.distribution = distribution.strip()
        self.executable = executable.strip() or "wsl.exe"
        self.preflight_timeout_seconds = max(0.1, float(preflight_timeout_seconds))
        self._preflight_done = False

    def validate_command(self, command: str) -> str | None:
        if _WINDOWS_PATH.search(command):
            return (
                "Windows paths are not valid inside WSL commands; use the cwd parameter "
                "or a workspace-relative POSIX path"
            )
        return None

    def _prefix(self, *, cwd: str | None = None) -> list[str]:
        command = [self.executable]
        if self.distribution:
            command.extend(["--distribution", self.distribution])
        if cwd:
            command.extend(["--cd", windows_path_to_wsl(cwd)])
        return command

    def _ensure_available(self) -> None:
        if self._preflight_done:
            return
        if not shutil.which(self.executable):
            raise ExecutionEnvironmentError(f"WSL launcher is unavailable: {self.executable}")
        try:
            result = subprocess.run(
                [*self._prefix(), "--exec", "bash", "-lc", "printf agent_wsl_ready"],
                capture_output=True,
                timeout=self.preflight_timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            # Do not permanently poison this executor after a transient WSL
            # cold-start timeout.  The next tool call must be able to retry.
            raise ExecutionEnvironmentError(
                f"WSL preflight failed: {type(exc).__name__}: {exc}"
            ) from exc
        if result.returncode != 0 or b"agent_wsl_ready" not in result.stdout:
            detail = _decode_wsl_output(result.stderr or result.stdout).strip()
            summary = detail or f"exit code {result.returncode}"
            raise ExecutionEnvironmentError(f"WSL preflight failed: {summary}")
        self._preflight_done = True

    def preflight(self) -> None:
        self._ensure_available()

    def start(self, command: str, cwd: str | None) -> subprocess.Popen:
        self.preflight()
        if not cwd:
            raise ExecutionEnvironmentError("WSL command execution requires a workspace cwd")
        return subprocess.Popen(
            [*self._prefix(cwd=cwd), "--exec", "bash", "-lc", command],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
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
                process.terminate()
        except (OSError, subprocess.SubprocessError):
            process.kill()


def resolve_linux_command_environment(
    mode: str, *, host_os: str | None = None, platform_name: str | None = None,
) -> str:
    """Resolve an explicit or host-derived Linux command environment."""
    host_os = host_os or os.name
    platform_name = platform_name or sys.platform
    if mode == "auto":
        if host_os == "nt":
            return "wsl"
        if platform_name.startswith("linux"):
            return "native_linux"
        raise ExecutionEnvironmentError(
            "automatic Linux command execution supports Windows (WSL) and Linux hosts only"
        )
    return mode


def build_linux_command_executor(settings) -> CommandExecutor:
    """Build the configured production command host without task classification."""
    mode = resolve_linux_command_environment(settings.agent_command_environment)
    if mode == "wsl":
        return WslBashExecutor(
            distribution=settings.agent_wsl_distribution,
            executable=settings.agent_wsl_executable,
            preflight_timeout_seconds=settings.agent_wsl_preflight_timeout_seconds,
        )
    if mode == "native_linux":
        return NativeLinuxBashExecutor()
    raise ExecutionEnvironmentError(f"unsupported command environment: {mode}")
