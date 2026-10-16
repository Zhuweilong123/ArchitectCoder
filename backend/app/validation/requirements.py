"""Task acceptance over coverage reports. No diagram or language rules."""
from app.agent_base.host_api.validation import CheckResult, ValidationDiagnostic, ValidationReport, normalize_requirements


def enforce_requirements(report, diagrams, requirements):
    diagnostics = []
    for i, requirement in enumerate(normalize_requirements(requirements)):
        paths = [f"diagrams[{n}]" for n, diagram in enumerate(diagrams)
                 if isinstance(diagram, dict) and diagram.get("name") == requirement.diagram_name]
        checks = [check for check in report.checks
                  if check.rule_id == requirement.rule_id and check.path in paths]
        if not paths or len(checks) != len(paths) or not all(check.satisfies_requirement for check in checks):
            observed = ", ".join(check.status for check in checks) or "not evaluated / target not found"
            diagnostics.append(ValidationDiagnostic("error", "VALIDATION_REQUIREMENT_UNMET",
                f"validation_requirements[{i}]",
                f"Required {requirement.rule_id} for diagram {requirement.diagram_name!r} is not satisfied "
                f"({observed}). Supply evidence and complete the requested check before review or completion."))
    if not requirements:
        return report
    return ValidationReport((*report.checks, CheckResult("task.validation", "validation_requirements",
                                                       "checked", tuple(diagnostics))))
