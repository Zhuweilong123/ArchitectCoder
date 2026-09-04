"""Backward-compatible imports for the host Runtime command adapters.

New application code should import from :mod:`app.runtime.command`.  This
module preserves the historical low-level import path used by extensions and
tests.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

from app.runtime.command import (
    CommandExecutor,
    ExecutionEnvironmentError,
    HostShellExecutor,
    NativeLinuxBashExecutor,
    NativePowerShellExecutor,
    PowerShellExecutionProfile,
    WslBashExecutor,
    _decode_wsl_output,
    build_command_executor,
    resolve_command_environment,
    windows_path_to_wsl,
)


def resolve_linux_command_environment(
    mode: str, *, host_os: str | None = None, platform_name: str | None = None,
) -> str:
    """Preserve the historical Linux-only resolver for old callers."""
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
    """Preserve the historical WSL-first builder for compatibility callers."""
    mode = resolve_linux_command_environment(settings.agent_command_environment)
    if mode == "wsl":
        return WslBashExecutor(
            distribution=settings.agent_wsl_distribution,
            executable=settings.agent_wsl_executable,
            preflight_timeout_seconds=settings.agent_wsl_preflight_timeout_seconds,
        )
    if mode in {"native_linux", "native_posix"}:
        return NativeLinuxBashExecutor()
    if mode == "native_windows":
        return NativePowerShellExecutor()
    raise ExecutionEnvironmentError(f"unsupported command environment: {mode}")


__all__ = [
    "CommandExecutor",
    "ExecutionEnvironmentError",
    "HostShellExecutor",
    "NativeLinuxBashExecutor",
    "NativePowerShellExecutor",
    "PowerShellExecutionProfile",
    "WslBashExecutor",
    "_decode_wsl_output",
    "build_command_executor",
    "build_linux_command_executor",
    "resolve_command_environment",
    "resolve_linux_command_environment",
    "windows_path_to_wsl",
]
