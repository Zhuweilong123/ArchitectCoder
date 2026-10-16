"""Compatibility entry point; parsing belongs to the injected facts provider."""
from app.services.design_validation import source_facts_provider
from app.validation.rules.sequence_source import validate_sequence_sources as _check


def validate_sequence_sources(diagram, location, workspace_root, source_provider=None):
    return _check(diagram, location, workspace_root, source_provider or source_facts_provider())
