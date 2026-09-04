"""Runtime lifecycle, environment, and host execution abstractions."""

from app.runtime.command import (
    CommandExecutor,
    ExecutionEnvironmentError,
    HostShellExecutor,
    NativeLinuxBashExecutor,
    NativePowerShellExecutor,
    PowerShellExecutionProfile,
    WslBashExecutor,
    build_command_executor,
    build_linux_command_executor,
    resolve_command_environment,
)
from app.runtime.environment import EnvironmentContext, build_environment_context
from app.runtime.filesystem import FileSystemOperationError, NativeFileSystem

from app.runtime.agent_runtime import (
    AgentRuntime,
    AgentSession,
    SessionBusyError,
    active_count,
    cleanup_expired,
    finalize,
    get,
    get_or_create,
    release_run,
    runtime,
    try_claim_run,
)

__all__ = [
    "AgentRuntime",
    "AgentSession",
    "SessionBusyError",
    "active_count",
    "cleanup_expired",
    "finalize",
    "get",
    "get_or_create",
    "release_run",
    "runtime",
    "try_claim_run",
    "EnvironmentContext",
    "build_environment_context",
    "FileSystemOperationError",
    "NativeFileSystem",
    "ExecutionEnvironmentError",
    "CommandExecutor",
    "HostShellExecutor",
    "NativeLinuxBashExecutor",
    "NativePowerShellExecutor",
    "PowerShellExecutionProfile",
    "WslBashExecutor",
    "build_command_executor",
    "build_linux_command_executor",
    "resolve_command_environment",
]
