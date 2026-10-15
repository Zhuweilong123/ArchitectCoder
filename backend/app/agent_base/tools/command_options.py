"""Construction options shared by shell, program and semantic task tools."""
from typing import TypedDict

from ..host_api.agents import ProgressPort, ReviewManagerPort
from app.core.risk_policy import RiskPolicy
from app.runtime.command import CommandExecutor


class CommandToolOptions(TypedDict, total=False):
    review_manager: ReviewManagerPort | None
    progress: ProgressPort | None
    review_timeout: float
    timeout: float
    output_cap: int
    risk_policy: RiskPolicy | None
    command_executor: CommandExecutor | None
    workspace_root: str
