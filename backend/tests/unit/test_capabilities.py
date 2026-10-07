import asyncio

import pytest

from app.agent_base.tools.base import Tool, ToolParameter
from app.agent_base.tools.registry import ToolRegistry
from app.core.capabilities import CapabilityPolicy


class _EchoTool(Tool):
    def __init__(self):
        super().__init__("echo_tool", "echo")

    def get_parameters(self):
        return [ToolParameter(name="value", type="string", description="value")]

    def run(self, parameters):
        return parameters.get("value", "")


def test_registry_enforces_run_tool_allowlist(tmp_path):
    registry = ToolRegistry(
        policy=CapabilityPolicy(workspace_roots=[str(tmp_path)], allowed_tools=[])
    )
    registry.register_tool(_EchoTool())

    result = asyncio.run(registry.aexecute_tool_result_with_params("echo_tool", {"value": "x"}))

    assert result.status == "blocked"
    assert result.error_code == "POLICY_BLOCKED"


def test_registry_blocks_protected_and_outside_paths(tmp_path):
    policy = CapabilityPolicy(workspace_roots=[str(tmp_path)])

    protected = policy.check("read_file", {"path": ".env"})
    outside = policy.check("read_file", {"path": str(tmp_path.parent / "outside.txt")})

    assert protected and "protected path" in protected
    assert outside and "outside configured workspace" in outside


def test_shell_policy_blocks_traversal_and_allows_workspace_absolute_path(tmp_path):
    policy = CapabilityPolicy(workspace_roots=[str(tmp_path)])

    assert policy.check("shell", {"command": "git -C .. status"})
    assert policy.check("shell", {"command": f"git -C {tmp_path} status"}) is None


@pytest.mark.parametrize("value", ["source/.env", "workspace/.git/config", "nested/.SSH/key", ".env.local"])
def test_protected_paths_remain_protected_when_qualified_or_absolute(tmp_path, value):
    policy = CapabilityPolicy(workspace_roots=[str(tmp_path)])
    assert "protected path" in policy.check("read_file", {"path": value})
    assert "protected path" in policy.check("read_file", {"path": str(tmp_path / value)})


def test_literal_program_path_with_spaces_uses_full_argument(tmp_path):
    root = tmp_path / "workspace with spaces"
    root.mkdir()
    policy = CapabilityPolicy(workspace_roots=[str(root)])
    assert policy.check("run_program", {"program": "python", "args": [str(root / "main.py")]}) is None
    assert policy.check("run_program", {"program": "python", "args": [str(tmp_path / "outside.py")]})
