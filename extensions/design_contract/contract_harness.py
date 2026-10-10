"""Deterministic design/source/test consistency rules owned by the plugin."""

from __future__ import annotations

from app.agent_base.host_api.services import get_host_services
import uuid
from pathlib import Path
from typing import Any
from .contract_pipeline import assemble_contract
from .language_adapters import LanguageAdapterRegistry, default_language_adapters

from app.agent_base.host_api.contract_checks import CheckStatus, ContractCheckResult, ContractViolation

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
        validation_requirements=(),
        requirements_only: bool = False,
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
        project_files = {self._normalize(path) for path in (
            normalized.get("project_files") or ()) if path}
        if normalized.get("project_file"):
            project_files.add(self._normalize(normalized["project_file"]))
        relevant_design_change = any(
            self._normalize(path) in project_files or (
                self._is_under(path, str(normalized.get("design_root", "") or ""))
                and Path(path).suffix.lower() in {".umlproj", ".uml"}
            ) for path in changed
        )
        check_implementation = (not changed or relevant_source_change) and not requirements_only
        if changed and not (relevant_source_change or relevant_design_change) and not validation_requirements:
            return self._result(
                "not_applicable", project_id, changed,
                message="no source or design artifact changed in this run",
            )

        if contract_provider is None and language_runner is not None:
            contract_provider = get_host_services().resolve_provider("design_contract",
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
                "inconclusive" if validation_requirements else "not_applicable", snapshot.project_id, changed,
                snapshot_status=snapshot.status,
                graph_status=assembly.graph_status,
                message="contract provider is disabled",
            )

        violations: list[ContractViolation] = []
        validation_reports = []
        design_records = snapshot.metadata.get("design_diagrams", [])
        from app.agent_base.host_api.validation import normalize_requirements
        requirements = normalize_requirements(validation_requirements)
        target_names = {record["diagram"].get("name") for record in design_records}
        for requirement in requirements:
            if requirement.diagram_name not in target_names:
                violations.append(ContractViolation(code="VALIDATION_REQUIREMENT_UNMET", severity="error",
                    message=f"Required {requirement.rule_id}: diagram {requirement.diagram_name!r} was not collected."))
        if design_records:
            from .provider import SnapshotSourceFacts
            source_view = SnapshotSourceFacts(snapshot.metadata.get("source_artifacts", {}), contract_provider)
            by_file = {}
            for record in design_records:
                if requirements_only and not any(r.diagram_name == record["diagram"].get("name") for r in requirements):
                    continue
                by_file.setdefault(record["path"], []).append(record["diagram"])
            for design_path, diagrams in by_file.items():
                report = get_host_services().validate_design(diagrams,
                    workspace_root=workspace_root, source_provider=source_view,
                    requirements=tuple(requirement for requirement in requirements
                                       if any(d.get("name") == requirement.diagram_name for d in diagrams)))
                validation_reports.append({"path": design_path, **report.to_dict()})
                for diagnostic in report.diagnostics:
                    if requirements_only and diagnostic.severity != "error":
                        continue
                    violations.append(ContractViolation(code=diagnostic.code, severity=diagnostic.severity,
                        message=diagnostic.message, path=design_path))
                # The contract scenario owns this decision: a requested check
                # without the required capability cannot authorize a commit.
                for check in report.checks:
                    if check.status in {"unavailable", "unsupported"}:
                        violations.append(ContractViolation(code="validation_unavailable", severity="error",
                            message=f"Required check {check.rule_id} unavailable: {check.reason}", path=design_path))
        if check_implementation:
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
            if requirements_only:
                continue
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
                "design_validation": validation_reports,
                "validation_requirements": [dict(rule_id=r.rule_id, diagram_name=r.diagram_name) for r in requirements],
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
