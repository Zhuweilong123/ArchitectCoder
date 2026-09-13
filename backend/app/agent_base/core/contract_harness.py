"""Model-independent design-contract checks for workspace changes.

The harness deliberately sits above the collector and graph adapters.  It
evaluates a candidate workspace snapshot and returns a structured decision;
the Agent may consume that decision, but it is not part of the check itself.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from .contract_pipeline import assemble_contract
from .contracts import load_contracts
from .language_adapters import LanguageAdapterRegistry, default_language_adapters


CheckStatus = Literal["pass", "warn", "block", "inconclusive", "not_applicable"]


@dataclass(frozen=True)
class ContractViolation:
    code: str
    severity: Literal["warning", "error"]
    message: str
    path: str = ""
    design_entity_id: str = ""
    source_entity_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ContractCheckResult:
    check_id: str
    status: CheckStatus
    project_id: str
    changed_paths: tuple[str, ...] = ()
    violations: tuple[ContractViolation, ...] = ()
    snapshot_status: str = ""
    graph_status: str = "not_requested"
    message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def requires_confirmation(self) -> bool:
        return self.status == "warn"

    @property
    def can_commit(self) -> bool:
        return self.status in {"pass", "warn"}

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["violations"] = [item.to_dict() for item in self.violations]
        value["requires_confirmation"] = self.requires_confirmation
        value["can_commit"] = self.can_commit
        return value


class ContractHarness:
    """Run deterministic contract checks independently of the model."""

    def check(
        self,
        manifest: Any,
        *,
        project_id: str = "",
        changed_paths: list[str] | tuple[str, ...] = (),
        settings: Any = None,
        index_graph: bool = False,
        contract_provider: Any = None,
        knowledge_graph_provider: Any = None,
        language_runner: Any = None,
        language_adapters: LanguageAdapterRegistry | None = None,
    ) -> ContractCheckResult:
        normalized = manifest.to_dict() if hasattr(manifest, "to_dict") else dict(manifest or {})
        changed = tuple(
            str(item.get("path", "")) if isinstance(item, dict) else str(item)
            for item in changed_paths
        )
        changed = tuple(path for path in changed if path)
        source_root = str(normalized.get("source_root", "") or "")
        workspace_root = str(normalized.get("workspace_root", "") or "")
        adapters = language_adapters or default_language_adapters()
        relevant_source_change = any(
            self._is_under(path, source_root)
            and adapters.adapter_for(path) is not None
            for path in changed
        )
        if changed and not relevant_source_change:
            return self._result(
                "not_applicable", project_id, changed,
                message="no source artifact changed in this run",
            )

        if contract_provider is None and language_runner is not None:
            contract_provider = load_contracts(
                settings=settings,
                language_runner=language_runner,
                language_adapters=adapters,
            )
        try:
            assembly = assemble_contract(
                normalized,
                project_id=project_id,
                settings=settings,
                contract_provider=contract_provider,
                knowledge_graph_provider=knowledge_graph_provider,
                index_graph=index_graph,
            )
        except Exception as exc:
            return self._result(
                "inconclusive", project_id, changed,
                message=f"contract collection failed: {exc}",
                metadata={"error": str(exc)},
            )

        snapshot = assembly.snapshot
        if snapshot.metadata.get("reason") == "contract provider is disabled":
            return self._result(
                "not_applicable", snapshot.project_id, changed,
                snapshot_status=snapshot.status,
                graph_status=assembly.graph_status,
                message="contract provider is disabled",
            )

        violations: list[ContractViolation] = []
        entities = list(snapshot.entities)
        mappings = list(snapshot.mappings)
        designs = [item for item in entities if item.entity_type == "class"]
        source_classes = [item for item in entities if item.entity_type == "source_class"]
        mapped_design_ids = {item.design_entity_id for item in mappings if item.source_entity_id}

        if designs and source_root and source_classes:
            for design in designs:
                if design.entity_id not in mapped_design_ids:
                    violations.append(ContractViolation(
                        code="missing_implementation",
                        severity="error",
                        message=f"设计类 {design.name} 没有对应的源码实现",
                        path=design.path,
                        design_entity_id=design.entity_id,
                    ))

        source_by_id = {item.entity_id: item for item in source_classes}
        design_by_id = {item.entity_id: item for item in designs}
        for mapping in mappings:
            design = design_by_id.get(mapping.design_entity_id)
            source = source_by_id.get(mapping.source_entity_id)
            if design is None or source is None:
                continue
            design_methods = {
                item.name for item in entities
                if item.entity_type == "method" and item.parent_id == design.entity_id
            }
            source_methods = {
                item.name.rsplit(".", 1)[-1] for item in entities
                if item.entity_type == "source_method" and item.parent_id == source.entity_id
            }
            for method_name in sorted(design_methods - source_methods):
                violations.append(ContractViolation(
                    code="missing_method",
                    severity="error",
                    message=f"源码类 {source.name} 缺少设计方法 {method_name}",
                    path=source.path,
                    design_entity_id=design.entity_id,
                    source_entity_id=source.entity_id,
                ))
            if not mapping.test_entity_ids:
                violations.append(ContractViolation(
                    code="no_test_coverage",
                    severity="warning",
                    message=f"源码类 {source.name} 尚未发现对应测试用例",
                    path=source.path,
                    design_entity_id=design.entity_id,
                    source_entity_id=source.entity_id,
                ))

        changed_set = {self._normalize(path) for path in changed}
        for diagnostic in snapshot.metadata.get("errors", ()):
            if not isinstance(diagnostic, dict):
                continue
            path = str(diagnostic.get("path", ""))
            diagnostic_path = path
            if workspace_root and path and not Path(path).is_absolute():
                diagnostic_path = str(Path(workspace_root) / path)
            severity = "error" if self._normalize(diagnostic_path) in changed_set else "warning"
            violations.append(ContractViolation(
                code="artifact_parse_error",
                severity=severity,
                message=str(diagnostic.get("error", "artifact parse failed")),
                path=path,
            ))

        errors = tuple(item for item in violations if item.severity == "error")
        status: CheckStatus = "block" if errors else ("warn" if violations else "pass")
        message = {
            "pass": "设计契约校验通过",
            "warn": "设计契约校验发现警告",
            "block": "设计契约校验阻止提交",
        }[status]
        return ContractCheckResult(
            check_id=uuid.uuid4().hex,
            status=status,
            project_id=snapshot.project_id,
            changed_paths=changed,
            violations=tuple(violations),
            snapshot_status=snapshot.status,
            graph_status=assembly.graph_status,
            message=message,
            metadata={
                "entity_counts": snapshot.metadata.get("entity_counts", {}),
                "parser_version": snapshot.metadata.get("parser_version", ""),
            },
        )

    @staticmethod
    def _result(
        status: CheckStatus,
        project_id: str,
        changed_paths: tuple[str, ...],
        *,
        snapshot_status: str = "",
        graph_status: str = "not_requested",
        message: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> ContractCheckResult:
        return ContractCheckResult(
            check_id=uuid.uuid4().hex,
            status=status,
            project_id=project_id,
            changed_paths=changed_paths,
            snapshot_status=snapshot_status,
            graph_status=graph_status,
            message=message,
            metadata=metadata or {},
        )

    @staticmethod
    def _normalize(path: str) -> str:
        return str(Path(path).resolve()).replace("\\", "/").lower()

    @classmethod
    def _is_under(cls, path: str, root: str) -> bool:
        if not path or not root:
            return False
        try:
            return Path(cls._normalize(path)).is_relative_to(Path(cls._normalize(root)))
        except (OSError, ValueError):
            return False


__all__ = ["ContractHarness", "ContractCheckResult", "ContractViolation"]
