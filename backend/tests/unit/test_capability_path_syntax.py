"""Path syntax decisions must not depend on the test runner's operating system."""
from pathlib import PurePosixPath, PureWindowsPath

import pytest

from app.core.capabilities import CapabilityPolicy


@pytest.mark.parametrize("arguments", [["/list", "/v"], ["/opt/ros/humble/setup.bash"]])
def test_windows_options_and_guest_paths_use_command_checks(tmp_path, monkeypatch, arguments):
    from app.core import capabilities
    monkeypatch.setattr(capabilities, "_HOST_IS_WINDOWS", True)
    policy = CapabilityPolicy(workspace_roots=[str(tmp_path)])
    assert policy.check("run_program", {"program": "tool", "args": arguments}) is None
    assert policy.check("run_program", {"program": "tool", "args": ["../secret"]})


@pytest.mark.parametrize("native_path", [PurePosixPath, PureWindowsPath])
@pytest.mark.parametrize("windows, argument, absolute", [
    (True, "/list", False),
    (True, "/v", False),
    (True, "/opt/ros/humble/setup.bash", False),
    (True, "C:/workspace with spaces/main.py", True),
    (True, r"C:\workspace with spaces\main.py", True),
    (True, r"\\server\share\main.py", True),
    (False, "/list", True),
    (False, "/workspace with spaces/main.py", True),
    (False, "relative/main.py", False),
])
def test_program_argument_classification_uses_policy_platform(
    monkeypatch, native_path, windows, argument, absolute,
):
    from app.core import capabilities

    policy = CapabilityPolicy()
    monkeypatch.setattr(capabilities, "_HOST_IS_WINDOWS", windows)
    # Simulate either interpreter path flavor without touching the filesystem.
    monkeypatch.setattr(capabilities, "Path", native_path)
    checked_paths = []
    monkeypatch.setattr(policy, "_check_path", lambda value: checked_paths.append(value) or "blocked path")
    monkeypatch.setattr(policy, "_check_command", lambda value: None)

    result = policy.check("run_program", {"program": "tool", "args": [argument]})
    assert checked_paths == ([argument] if absolute else [])
    assert result == ("blocked path" if absolute else None)
