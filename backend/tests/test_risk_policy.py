import asyncio
import json

from app.agent_base.tools.my_tools.file_system_tools import BashTool
from app.agent_base.tools.review import ReviewManager
from app.core.risk_policy import RiskPolicy


class _Progress:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


def test_risk_policy_classifies_and_scopes_approval():
    policy = RiskPolicy(deny_patterns=["wipe"], approval_patterns=["git reset"])

    assert policy.evaluate("bash", {"command": "echo ok"}).action == "allow"
    assert policy.evaluate("bash", {"command": "git reset --hard"}).level == "high"
    assert policy.evaluate("bash", {"command": "wipe disk"}).action == "deny"

    scope = policy.approval_scope("bash", {"command": "git reset --hard"})
    assert policy.approval_is_valid("bash", {"command": "git reset --hard"}, scope)
    assert not policy.approval_is_valid("bash", {"command": "git reset --soft"}, scope)


def test_bash_review_contains_risk_and_scope_metadata(tmp_path):
    progress = _Progress()
    manager = ReviewManager()
    bash = BashTool(str(tmp_path), review_manager=manager, progress=progress)

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
    assert review["metadata"]["approval_scope"]["tool"] == "bash"
