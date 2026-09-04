import subprocess

import pytest

from app.agent_base.execution.linux import (
    ExecutionEnvironmentError,
    WslBashExecutor,
    _decode_wsl_output,
    build_linux_command_executor,
    resolve_linux_command_environment,
    windows_path_to_wsl,
)
from app.agent_base.tools.my_tools.file_system_tools import BashTool
from backend.config import Settings


def test_windows_workspace_path_maps_to_wsl_mount():
    assert windows_path_to_wsl(r"D:\AI_tools\uml_designer\backend") == "/mnt/d/AI_tools/uml_designer/backend"
    assert windows_path_to_wsl("/mnt/d/AI_tools/uml_designer") == "/mnt/d/AI_tools/uml_designer"


def test_wsl_path_mapping_rejects_ambiguous_host_path():
    with pytest.raises(ExecutionEnvironmentError, match="absolute drive path"):
        windows_path_to_wsl("relative/path")


def test_wsl_diagnostic_decodes_windows_utf16le():
    assert _decode_wsl_output("E_ACCESSDENIED".encode("utf-16le")) == "E_ACCESSDENIED"


def test_wsl_executor_preflights_then_uses_linux_bash(monkeypatch):
    executor = WslBashExecutor(distribution="Ubuntu", executable="wsl.exe")
    monkeypatch.setattr("app.agent_base.execution.linux.shutil.which", lambda _: "C:/Windows/System32/wsl.exe")

    preflight = []
    launched = []

    class _Result:
        returncode = 0
        stdout = b"agent_wsl_ready"
        stderr = b""

    def fake_run(args, **kwargs):
        preflight.append((args, kwargs))
        return _Result()

    def fake_popen(args, **kwargs):
        launched.append((args, kwargs))
        return object()

    monkeypatch.setattr("app.agent_base.execution.linux.subprocess.run", fake_run)
    monkeypatch.setattr("app.agent_base.execution.linux.subprocess.Popen", fake_popen)

    executor.start("pytest -q", r"D:\repo\tests")

    assert preflight[0][0] == [
        "wsl.exe", "--distribution", "Ubuntu", "--exec", "bash", "-lc", "printf agent_wsl_ready",
    ]
    assert launched[0][0] == [
        "wsl.exe", "--distribution", "Ubuntu", "--cd", "/mnt/d/repo/tests",
        "--exec", "bash", "-lc", "pytest -q",
    ]
    assert launched[0][1]["stdout"] is subprocess.PIPE


def test_wsl_executor_retries_preflight_after_transient_failure(monkeypatch):
    executor = WslBashExecutor(executable="wsl.exe")
    monkeypatch.setattr("app.agent_base.execution.linux.shutil.which", lambda _: "C:/Windows/System32/wsl.exe")

    class _Result:
        returncode = 0
        stdout = b"agent_wsl_ready"
        stderr = b""

    calls = 0

    def fake_run(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise subprocess.TimeoutExpired("wsl.exe", 20)
        return _Result()

    monkeypatch.setattr("app.agent_base.execution.linux.subprocess.run", fake_run)

    with pytest.raises(ExecutionEnvironmentError, match="TimeoutExpired"):
        executor.preflight()
    executor.preflight()

    assert calls == 2


def test_explicit_wsl_settings_build_wsl_executor():
    settings = Settings(_env_file=None, deepseek_api_key="test-key")
    settings.agent_command_environment = "wsl"

    executor = build_linux_command_executor(settings)

    assert isinstance(executor, WslBashExecutor)


def test_auto_environment_uses_wsl_on_windows():
    assert resolve_linux_command_environment("auto", host_os="nt", platform_name="win32") == "wsl"


def test_auto_environment_uses_native_bash_on_linux():
    assert resolve_linux_command_environment("auto", host_os="posix", platform_name="linux") == "native_linux"


def test_bash_schema_advertises_linux_contract_and_rejects_windows_command(tmp_path):
    bash = BashTool(str(tmp_path), command_executor=WslBashExecutor())

    assert "Linux/POSIX bash" in bash.to_openai_schema()["function"]["description"]
    result = __import__("asyncio").run(bash._execute({"command": "dir"}))
    assert "executable 'dir' is not allowed" in result


def test_bash_allows_linux_environment_diagnostics():
    for command in ("printf agent_wsl_ready", "uname -s", "pwd"):
        assert BashTool._validate_shell_command(command) is None
