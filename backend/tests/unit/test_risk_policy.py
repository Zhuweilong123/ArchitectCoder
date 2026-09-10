import asyncio
import json

from app.agent_base.tools.my_tools.foundation_tools import ShellTool
from app.agent_base.tools.review import ReviewManager
from app.core.risk_policy import RiskPolicy
from app.runtime.command import NativePowerShellExecutor


class _Progress:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


def test_risk_policy_classifies_and_scopes_approval():
    policy = RiskPolicy(deny_patterns=["wipe"], approval_patterns=["git reset"])

    assert policy.evaluate("shell", {"command": "echo ok"}).action == "allow"
    assert policy.evaluate("shell", {"command": "git reset --hard"}).level == "high"
    assert policy.evaluate("shell", {"command": "wipe disk"}).action == "deny"

    scope = policy.approval_scope("shell", {"command": "git reset --hard"})
    assert policy.approval_is_valid("shell", {"command": "git reset --hard"}, scope)
    assert not policy.approval_is_valid("shell", {"command": "git reset --soft"}, scope)


def test_default_deny_policy_does_not_confuse_format_parameter_with_disk_formatting(tmp_path):
    bash = ShellTool(str(tmp_path))

    assert bash._risk_policy.evaluate(
        "shell", {"command": 'Get-Date -Format "yyyy-MM-dd HH:mm:ss dddd"'},
    ).action == "allow"
    assert bash._risk_policy.evaluate(
        "shell", {"command": "format C:"},
    ).action == "deny"
    assert bash._risk_policy.evaluate(
        "shell", {"command": "Format-Volume -DriveLetter D -FileSystem NTFS"},
    ).action == "deny"


def test_powershell_allows_readonly_get_date_format():
    executor = NativePowerShellExecutor(executable="powershell.exe")

    assert executor.validate_command(
        'Get-Date -Format "yyyy-MM-dd HH:mm:ss dddd"',
    ) is None


def test_bash_review_contains_risk_and_scope_metadata(tmp_path):
    progress = _Progress()
    manager = ReviewManager()
    bash = ShellTool(str(tmp_path), review_manager=manager, progress=progress)

    async def scenario():
        task = asyncio.create_task(
            bash._execute({"command": 'echo "git reset --hard is risky"'})
        )
        await asyncio.sleep(0.05)
        manager.resolve(0, json.dumps({"decision": "accept", "feedback": ""}))
        return await task

    result = asyncio.run(scenario())
    review = next(event for event in progress.events if event["event"] == "review")

    assert "git reset --hard is risky" in result
    assert review["metadata"]["risk_level"] == "high"
    assert review["metadata"]["approval_scope"]["tool"] == "shell"
