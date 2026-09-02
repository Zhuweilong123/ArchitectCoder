# Linux command execution contract

DevAgent exposes one command dialect: Linux/POSIX `bash`.

On a Windows host, `bash` runs through WSL. The Agent must use POSIX commands
and the `cwd` aliases in the tool schema; it must not invoke `wsl.exe`,
PowerShell, or `cmd.exe`, and must not convert host paths itself.

## Deployment configuration

```env
# Optional override. The default is auto:
# Windows host -> WSL; Linux host -> native bash.
AGENT_COMMAND_ENVIRONMENT=wsl
AGENT_WSL_DISTRIBUTION=Ubuntu
AGENT_WSL_EXECUTABLE=wsl.exe
AGENT_WSL_PREFLIGHT_TIMEOUT_SECONDS=5

# Native Linux host needs no command-environment configuration.
# Optional explicit override: AGENT_COMMAND_ENVIRONMENT=native_linux
```

The configured distribution must have `bash` and all project command-line
dependencies (Python, Node, Git, package managers, and test dependencies).
Windows workspace paths are mapped to the corresponding `/mnt/<drive>/...`
directory by the execution adapter. File, graph, and capability policies retain
their host-path checks before command execution.

With `STRICT_PRODUCTION=true`, application startup performs the WSL/Linux
preflight and refuses to start if the configured command environment is not
ready. There is intentionally no automatic fallback to Windows `cmd.exe`.
