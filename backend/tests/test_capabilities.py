import asyncio

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

    assert policy.check("bash", {"command": "git -C .. status"})
    assert policy.check("bash", {"command": f"git -C {tmp_path} status"}) is None
