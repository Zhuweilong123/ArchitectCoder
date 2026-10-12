import asyncio

import pytest

from app.agent_base.tools.my_tools.foundation_tools import ShellTool
from app.runtime.command import NativePowerShellExecutor


@pytest.mark.parametrize("command", [
    "wsl -l -v",
    "wsl.exe --list --verbose",
    "Get-Command wsl.exe -ErrorAction SilentlyContinue",
    "wsl.exe -d Ubuntu -- bash -lc 'source /opt/ros/humble/setup.bash && python3 -m pytest --collect-only -q tests'",
    'wsl.exe -- bash -lc "source /opt/ros/humble/setup.bash; python3 -m pytest -q"',
])
def test_windows_shell_accepts_explicit_wsl_commands(tmp_path, monkeypatch, command):
    tool = ShellTool(str(tmp_path), command_executor=NativePowerShellExecutor())
    calls = []

    async def run(command, cwd):
        calls.append((command, cwd))
        return "launched"

    monkeypatch.setattr(tool, "_run_command", run)
    assert asyncio.run(tool._execute({"command": command})) == "launched"
    assert calls == [(command, str(tmp_path))]


@pytest.mark.parametrize("command", [
    "wsl.exe -l; git status",
    "wsl.exe -l && git status",
    "wsl.exe -l | Get-Content .env",
    "wsl.exe -l & git status",
    "wsl.exe -- bash -lc 'echo ok' > output.txt",
    "wsl.exe -- bash -lc 'echo ok",
    'wsl.exe -- bash -lc "echo $(Get-Content .env)"',
])
def test_windows_shell_still_rejects_host_operators(command):
    assert NativePowerShellExecutor().validate_command(command)


def test_wsl_command_launch_uses_native_powershell_without_mapping(tmp_path, monkeypatch):
    executor = NativePowerShellExecutor("powershell.exe")
    calls = []
    monkeypatch.setattr("app.runtime.command.shutil.which", lambda _: "powershell.exe")
    monkeypatch.setattr("app.runtime.command.subprocess.Popen", lambda argv, **kwargs: calls.append((argv, kwargs)))
    command = "wsl.exe -d Ubuntu -- bash -lc 'pwd'"
    executor.start(command, str(tmp_path))
    assert calls[0][0][-2:] == ["-Command", command]
    assert calls[0][1]["cwd"] == str(tmp_path)


def test_windows_policy_does_not_treat_guest_paths_or_slash_options_as_host_paths(tmp_path, monkeypatch):
    from app.core import capabilities
    monkeypatch.setattr(capabilities, "_HOST_IS_WINDOWS", True)
    policy = capabilities.CapabilityPolicy(workspace_roots=[str(tmp_path)])
    assert policy.check("shell", {
        "command": "wsl.exe -- bash -lc 'source /opt/ros/humble/setup.bash && python3 -m pytest tests'",
    }) is None
    assert policy.check("run_program", {"program": "tool", "args": ["/list", "/v"]}) is None


def test_wsl_environment_configuration_and_adapters_are_removed():
    from backend.config.settings import Settings
    from app.runtime import command, sandbox_worker
    assert not any(name.startswith("agent_wsl_") for name in Settings.model_fields)
    assert not hasattr(command, "WslBashExecutor")
    assert not hasattr(command, "build_linux_command_executor")
    assert not hasattr(sandbox_worker, "WslWorker")
