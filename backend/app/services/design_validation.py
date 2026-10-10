"""Composition root; rule implementations depend only on public ports."""
from app.agent_base.host_api.validation import ValidationContext
from app.agent_base.host_api.services import get_host_services
from app.validation.engine import ProjectValidator, ValidationRegistry
from app.validation.rules.class_structure import ClassStructureRule
from app.validation.rules.component_structure import ComponentStructureRule
from app.validation.rules.sequence_structure import SequenceStructureRule
from app.validation.rules.sequence_source import SequenceSourceRule


def default_validation_registry():
    return ValidationRegistry((ClassStructureRule(), ComponentStructureRule(),
        SequenceStructureRule(), SequenceSourceRule()))


def source_facts_provider():
    host = get_host_services()
    session = host.extension_context()
    binding = session.providers.get("design_contract") if session else None
    provider = getattr(binding, "gate", None)
    if provider is not None:
        return provider if callable(getattr(provider, "source_facts", None)) else None
    provider = host.resolve_provider("design_contract")
    return provider if callable(getattr(provider, "source_facts", None)) else None


def validate_project_diagrams(diagrams, workspace_root="", *, selected_keys=None,
                              source_provider=None, registry=None, requirements=()):
    if source_provider is None and any(d.get("source_scopes") for d in diagrams if isinstance(d, dict)):
        source_provider = source_facts_provider()
    context = ValidationContext(str(workspace_root or ""), source_provider=source_provider)
    report = ProjectValidator(registry or default_validation_registry()).validate(
        diagrams, context, selected_keys=selected_keys)
    from app.validation.requirements import enforce_requirements
    return enforce_requirements(report, diagrams, requirements)


def format_validation_report(report):
    lines = [f"Validation status: {report.status}. Only listed checks were evaluated."]
    for check in report.checks:
        lines.append(f"CHECK {check.rule_id} {check.path}: {check.status}" + (f" — {check.reason}" if check.reason else ""))
    for diagnostic in report.diagnostics:
        lines.append(f"{diagnostic.severity.upper()} {diagnostic.code} {diagnostic.path}: {diagnostic.message}")
    return "\n".join(lines)
