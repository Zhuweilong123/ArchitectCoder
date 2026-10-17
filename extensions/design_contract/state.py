"""Contract gate output retained through rejection or commit callbacks."""
from dataclasses import dataclass

from app.agent_base.host_api.contract_checks import ContractCheckResult


@dataclass
class ContractExecutionState:
    result: ContractCheckResult | None = None
    message: str = ""
