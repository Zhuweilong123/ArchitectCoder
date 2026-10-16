"""Legacy sequence-only API; tools use the generic project validation service."""
from pydantic import ValidationError
from app.models.uml import UmlDiagram
from app.agent_base.host_api.validation import ValidationDiagnostic as SequenceDiagnostic
from app.validation.rules.sequence_structure import _validate


def validate_sequence_diagrams(diagrams, workspace_root=None, source_provider=None):
    diagnostics = []
    for index, diagram in enumerate(diagrams):
        if not isinstance(diagram, dict) or diagram.get("diagram_type") != "sequence":
            continue
        try:
            normalized = UmlDiagram.model_validate(diagram).model_dump()
        except ValidationError as exc:
            diagnostics.append(SequenceDiagnostic("error", "SEQ_SCHEMA", f"diagrams[{index}]", str(exc)))
            continue
        diagnostics.extend(_validate(normalized, f"diagrams[{index}]"))
        if workspace_root is not None or normalized.get("source_scopes"):
            from app.services.sequence_source_validation import validate_sequence_sources
            diagnostics.extend(validate_sequence_sources(normalized, f"diagrams[{index}]", workspace_root, source_provider))
    return diagnostics


def format_sequence_diagnostics(diagnostics):
    return "\n".join(f"{d.severity.upper()} {d.code} {d.path}: {d.message}" for d in diagnostics)
