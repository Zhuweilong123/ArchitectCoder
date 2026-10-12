"""Host-side contracts shared by the execution lifecycle and plugins.

This module contains values and callbacks supplied by the host. Domain models
owned by a plugin stay in that plugin's ``plugin_api.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable, Protocol
from .data import JsonObject, WorkspaceSnapshot, ChangedPath
from .configuration import PluginConfiguration

from .contract_checks import ContractCheckResult


@dataclass(frozen=True)
class AnalysisRequest:
    prompt: str
    evidence: str = ""
    run_id: str = ""
    allowed_tools: tuple[str, ...] | None = None
    metadata: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class ReviewPrompt:
    review_type: str
    title: str
    content: str
    question: str
    timeout_seconds: float
    metadata: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class SkillContext:
    workspace_root: str = ""


@dataclass(frozen=True)
class ContractFailureAnalysisContext:
    result: ContractCheckResult
    invoke: Callable[[AnalysisRequest], Awaitable[str]]
    message: str = ""
    run_id: str = ""
    rollback_completed: bool | None = True
    allowed_tools: tuple[str, ...] | None = None


class ContractFailureAnalyzerPort(Protocol):
    async def analyze(self, context: ContractFailureAnalysisContext) -> str: ...


@dataclass(frozen=True)
class ContractGateContext:
    workspace_manifest: JsonObject
    changed_paths: tuple[ChangedPath | str, ...]
    emit: Callable[[JsonObject], Awaitable[bool]]
    request_review: Callable[[ReviewPrompt], Awaitable[str]] | None = None
    run_id: str = ""
    settings: PluginConfiguration | None = None
    contract_enabled: bool = True
    validation_requirements: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True)
class ContractGateDecision:
    allowed: bool
    result: ContractCheckResult | None = None
    message: str = ""


class ContractGatePort(Protocol):
    async def evaluate(self, context: ContractGateContext) -> ContractGateDecision: ...

    async def finalize(
        self,
        context: ContractGateContext,
        prior_result: ContractCheckResult | None = None,
    ) -> ContractCheckResult | None: ...
