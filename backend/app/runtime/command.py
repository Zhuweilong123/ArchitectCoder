"""Host command adapters for the Agent Runtime.

The Agent receives a runtime-generated environment description and invokes a
stable tool contract.  This module owns host-specific process and shell
launching; WSL is optional and never selected by ``auto`` on Windows.
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

from app.runtime.encoding import decode_process_output


_VERSION_RE = re.compile(r"\b\d+(?:\.\d+){0,3}(?:[-+._][A-Za-z0-9.-]+)?\b")


def _probe_version_process(start, terminate, timeout: float) -> dict[str, object]:
    """Collect a best-effort ``--version`` banner from an executor process."""
    process = start()
    try:
        stdout, stderr = process.communicate(timeout=max(0.1, float(timeout)))
    except subprocess.TimeoutExpired:
        terminate(process)
        return {"status": "timeout", "version": ""}
    output = (decode_process_output(stdout) + decode_process_output(stderr)).strip()
    if process.returncode != 0:
        return {
            "status": "unavailable",
            "version": "",
            "output": output[:200],
        }
    banner = next((line.strip() for line in output.splitlines() if line.strip()), "")
    match = _VERSION_RE.search(banner)
    return {
        "status": "available",
        "version": match.group(0) if match else banner[:120],
        "output": banner[:200],
    }


class ExecutionEnvironmentError(RuntimeError):
    """The configured command environment cannot safely execute a command."""


@dataclass(frozen=True)
class LinuxExecutionProfile:
    name: str
    cwd_note: str

    @property
    def tool_description(self) -> str:
        return (
            "Run one simple Linux/POSIX bash command as a last resort in a configured workspace directory. "
            f"{self.cwd_note} "
            "Use cwd='source', 'test', 'design', or 'workspace' instead of cd. "
            "Use POSIX commands only (for example ls, find, grep, pytest, npm, git); "
            "do not invoke cmd.exe, PowerShell, wsl.exe, or Windows-only commands. "
            "For file edits, prefer read_file/apply_changes; do not use shell, Python, sed, or package-manager commands to edit files. "
            "For verification, run the requested focused test directly; do not probe python/pip/which or install packages after a command error. "
            "Do not chain commands, use pipes/redirection, nested shells, or inline interpreter code. "
            "Choose the available filesystem tool that best fits the operation. "
            "Use run_task for project tasks and run_program for direct executable argv. "
            "High-risk commands are denied; sensitive commands require approval."
        )


class CommandExecutor(Protocol):
    """Host adapter for the Agent's shell command contract."""

    profile: LinuxExecutionProfile

    def preflight(self) -> None: ...

    def validate_command(self, command: str) -> str | None: ...

    def start(self, command: str, cwd: str | None) -> subprocess.Popen: ...

    def start_program(
        self, program: str, args: list[str], cwd: str | None,
    ) -> subprocess.Popen: ...

    def terminate(self, process: subprocess.Popen) -> None: ...


class HostShellExecutor:
    """Compatibility adapter for direct tool reuse outside production assembly.

    Production assembly injects an explicit host adapter.  This compatibility
    adapter remains useful for isolated low-level tool tests.
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
            # Windows command resolution may pick an inaccessible Python
            # installation earlier on PATH (for example a stale Anaconda
            # entry).  Prefer the interpreter running the backend so commands
            # such as ``python -m pytest`` remain executable and reproducible.
            interpreter_dir = os.path.dirname(sys.executable)
            inherited_path = os.environ.get("PATH", "")
            if interpreter_dir:
                kwargs["env"] = {
                    **os.environ,
                    "PATH": os.pathsep.join(
                        part for part in (interpreter_dir, inherited_path) if part
                    ),
                }
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

    def start_program(self, program: str, args: list[str], cwd: str | None) -> subprocess.Popen:
        return subprocess.Popen(
            [program, *args], cwd=cwd, shell=False,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )


class NativeLinuxBashExecutor:
    """Run the Linux command contract directly on a Linux host."""

    profile = LinuxExecutionProfile(
        name="linux-native",
        cwd_note="The supplied working directory is a native Linux path.",
    )

    def validate_command(self, command: str) -> str | None:
        return None

    def validate_resolved_program(self, program: str, args: list[str]) -> str | None:
        return _validate_resolved_program(program, args, resolve_on_host=True)

    def probe_toolchain(self, program: str, cwd: str, *, timeout: float = 10.0):
        return _probe_version_process(
            lambda: self.start_program(program, ["--version"], cwd),
            self.terminate,
            timeout,
        )

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

    def start_program(self, program: str, args: list[str], cwd: str | None) -> subprocess.Popen:
        self.preflight()
        return subprocess.Popen(
            [program, *args], cwd=cwd, shell=False,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True,
        )


@dataclass(frozen=True)
class PowerShellExecutionProfile:
    name: str = "windows-powershell"
    cwd_note: str = "The supplied working directory is a native Windows path."

    @property
    def tool_description(self) -> str:
        return (
            "Run one simple native PowerShell command as a last resort in the configured workspace. "
            f"{self.cwd_note} Use cwd='source', 'test', 'design', or 'workspace'. "
            "Use PowerShell syntax, including safe read-only expressions such as "
            "(Get-Content file).Count; do not invoke cmd.exe, bash, sh, wsl.exe, or nested shells. "
            "For file edits, prefer the structured file tools. Do not chain commands, use pipes, "
            "redirection, command substitution, or inline interpreter code. High-risk commands are "
            "denied; sensitive commands require approval. Use run_task for project tasks and "
            "run_program for direct executable argv."
        )


class NativePowerShellExecutor:
    """Execute commands directly on Windows without requiring WSL."""

    profile = PowerShellExecutionProfile()

    def __init__(self, executable: str = ""):
        self.executable = executable.strip() or (
            "pwsh.exe" if shutil.which("pwsh.exe") else "powershell.exe"
        )

    def preflight(self) -> None:
        if not shutil.which(self.executable):
            raise ExecutionEnvironmentError(
                f"PowerShell is unavailable: {self.executable}. "
                "Install Windows PowerShell or PowerShell 7."
            )

    def validate_command(self, command: str) -> str | None:
        if len(command) > 4000:
            return "command is too long (maximum 4000 characters)"
        if any(token in command for token in ("\n", "\r", ";", "&&", "||", "|", ">", "<", "`", "$" + "(")):
            return (
                "shell accepts one simple command only; chaining, pipes, redirection, "
                "command substitution, and inline scripts are not allowed. "
                "Use run_task for project tasks or run_program with literal argv."
            )
        lowered = command.lower()
        if any(token in lowered for token in (
            "cmd.exe", "powershell -command", "pwsh -command", "wsl.exe", "bash -c", "sh -c",
        )):
            return "nested shell invocation is not allowed"
        if " -command " in lowered or lowered.startswith(("-command ", "-c ")):
            return "nested PowerShell invocation is not allowed"
        executable_source = command
        expression = _SAFE_READONLY_EXPRESSION.match(command)
        if expression:
            # Permit common, read-only PowerShell aggregation over file content
            # without opening the door to arbitrary parenthesized expressions.
            executable_source = expression.group(1)
        executable = executable_source.strip().split(None, 1)[0].strip('"').lower()
        if executable == "&":
            remainder = command.strip()[1:].lstrip()
            executable = remainder.split(None, 1)[0].strip("'\"").lower()
        allowed = {
            "get-childitem", "gci", "dir", "get-content", "gc", "select-string", "sls",
            "test-path", "get-date", "pwd", "write-output", "echo", "python", "python.exe", "py",
            "pytest", "git", "node", "node.exe", "npm", "npx", "pnpm", "yarn", "ruff",
            "mypy", "cargo", "go", "dotnet", "java", "mvn", "gradle",
        }
        if executable not in allowed:
            return f"executable '{executable}' is not allowed"
        return None

    validate_shell_command = validate_command

    def validate_program(self, program: str, args: list[str]) -> str | None:
        if not program or any(any(char in value for char in ("\n", "\r", ";", "|", ">", "<")) for value in [program, *args]):
            return (
                "program and args must be literal values without shell control characters "
                "(; | > < or newlines); do not pass a command string"
            )
        executable = os.path.basename(program).lower()
        if executable.endswith((".exe", ".cmd", ".bat")):
            executable = executable.rsplit(".", 1)[0]
        allowed = {
            "python", "py", "pytest", "git", "node", "npm", "npx", "pnpm", "yarn",
            "ruff", "mypy", "cargo", "go", "dotnet", "java", "mvn", "gradle",
        }
        if executable not in allowed:
            if executable in {"powershell", "pwsh", "cmd", "bash", "sh", "wsl"}:
                return (
                    f"executable '{executable}' is a shell interpreter and cannot be used by "
                    "run_program; use shell for one simple command"
                )
            return f"executable '{executable}' is not allowed"
        return None

    def validate_resolved_program(self, program: str, args: list[str]) -> str | None:
        """Validate a resolver-owned task without a host executable allowlist.

        ``run_program`` is intentionally a tightly restricted compatibility
        escape hatch and continues to use :meth:`validate_program`.  A
        ``run_task`` command, however, has already been resolved from a
        project manifest and is executed by the broker.  For that path we
        only require literal argv, a command available on the selected
        worker's PATH, and no nested shell interpreter.  This keeps support
        open to new languages/toolchains without adding one executable at a
        time to the global allowlist.
        """
        return _validate_resolved_program(program, args, resolve_on_host=True)

    def probe_toolchain(self, program: str, cwd: str, *, timeout: float = 10.0):
        return _probe_version_process(
            lambda: self.start_program(program, ["--version"], cwd),
            self.terminate,
            timeout,
        )

    def start(self, command: str, cwd: str | None) -> subprocess.Popen:
        self.preflight()
        return subprocess.Popen(
            [self.executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )

    def start_program(self, program: str, args: list[str], cwd: str | None) -> subprocess.Popen:
        self.preflight()
        executable = shutil.which(program) or program
        suffix = os.path.splitext(executable)[1].lower()
        if suffix in {".cmd", ".bat"}:
            command = ["cmd.exe", "/d", "/s", "/c", subprocess.list2cmdline([executable, *args])]
        else:
            command = [executable, *args]
        return subprocess.Popen(
            command, cwd=cwd, shell=False,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
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
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except (OSError, subprocess.SubprocessError):
            process.kill()


_WINDOWS_PATH = re.compile(r"^([A-Za-z]):[\\/](.*)$")
_SAFE_READONLY_EXPRESSION = re.compile(
    r"^\s*\(\s*(Get-Content|gc)\s+.+?\s*\)\s*\.\s*(Count|Length)\s*$",
    re.IGNORECASE,
)


def _validate_resolved_program(
    program: str,
    args: list[str],
    *,
    resolve_on_host: bool,
) -> str | None:
    """Validate resolver-owned argv independently of any language/tool list."""
    if not program or any(
        any(char in value for char in ("\n", "\r", ";", "|", ">", "<"))
        for value in [program, *args]
    ):
        return "program and args must be literal values without shell control characters"
    executable = os.path.basename(program).lower()
    if executable.endswith((".exe", ".cmd", ".bat")):
        executable = executable.rsplit(".", 1)[0]
    if executable in {"powershell", "pwsh", "cmd", "bash", "sh", "wsl"}:
        return (
            f"resolver task cannot invoke shell interpreter '{executable}'; "
            "declare the underlying tool as a literal argv"
        )
    if os.path.isabs(program) or any(separator in program for separator in ("/", "\\")):
        # Project wrappers are intentionally relative to the already bounded
        # task cwd (for example ``./gradlew``).  Absolute paths and parent
        # traversal remain forbidden; ordinary tool names still resolve on the
        # worker PATH.
        normalized = program.replace("\\", "/")
        parts = tuple(part for part in normalized.split("/") if part)
        if not normalized.startswith("./") or ".." in parts:
            return "resolver task executable must be a command name or a safe relative wrapper"
    # Native workers can attest PATH availability before starting.  Isolated
    # workers perform the equivalent lookup inside their own boundary.
    if resolve_on_host and shutil.which(program) is None:
        return f"toolchain executable '{program}' is unavailable on the worker"
    return None


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
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    if b"\x00" in data:
        return data.decode("utf-16le", errors="replace")
    return decode_process_output(data)


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

    def validate_resolved_program(self, program: str, args: list[str]) -> str | None:
        # The command is resolved inside WSL, never against the Windows PATH.
        return _validate_resolved_program(program, args, resolve_on_host=False)

    def normalize_resolved_program(
        self, program: str, args: list[str], cwd: str,
    ) -> tuple[str, list[str]]:
        """Translate a Windows Gradle wrapper selected by host discovery."""
        if program.lower() == "gradlew.bat" and (Path(cwd) / "gradlew").is_file():
            return "./gradlew", args
        return program, args

    def probe_toolchain(self, program: str, cwd: str, *, timeout: float = 10.0):
        return _probe_version_process(
            lambda: self.start_program(program, ["--version"], cwd),
            self.terminate,
            timeout,
        )

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

    def start_program(self, program: str, args: list[str], cwd: str | None) -> subprocess.Popen:
        self.preflight()
        if not cwd:
            raise ExecutionEnvironmentError("WSL program execution requires a workspace cwd")
        return subprocess.Popen(
            [*self._prefix(cwd=cwd), "--exec", program, *args],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )


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


def resolve_command_environment(
    mode: str, *, host_os: str | None = None, platform_name: str | None = None,
) -> str:
    """Resolve a native or optional compatibility execution target."""
    host_os = host_os or os.name
    platform_name = platform_name or sys.platform
    if mode == "auto":
        if host_os == "nt":
            return "native_windows"
        if platform_name.startswith(("linux", "darwin")):
            return "native_posix"
        raise ExecutionEnvironmentError(f"unsupported host platform: {platform_name}")
    if mode == "native_linux":
        return "native_posix"
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


def build_command_executor(settings) -> CommandExecutor:
    """Build the production executor selected by the host Runtime."""
    mode = resolve_command_environment(settings.agent_command_environment)
    if mode == "native_windows":
        return NativePowerShellExecutor()
    if mode == "native_posix":
        return NativeLinuxBashExecutor()
    if mode == "wsl":
        return WslBashExecutor(
            distribution=settings.agent_wsl_distribution,
            executable=settings.agent_wsl_executable,
            preflight_timeout_seconds=settings.agent_wsl_preflight_timeout_seconds,
        )
    raise ExecutionEnvironmentError(f"unsupported command environment: {mode}")
