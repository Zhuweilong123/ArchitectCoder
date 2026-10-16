import ast
from pathlib import Path
from app.agent_base.host_api.validation import CheckResult, SourceArtifact, ValidationContext
from app.validation.engine import ProjectValidator, ValidationRegistry
from app.services.design_validation import validate_project_diagrams


def test_all_builtin_diagram_types_have_independent_structure_rules():
    report = validate_project_diagrams([
        {"name": "Classes", "diagram_type": "class", "classes": [{"id": "a"}],
         "relations": [{"id": "r", "source": "a", "target": "missing"}]},
        {"name": "Components", "diagram_type": "component", "components": [{"id": "c", "parent_id": "c"}]},
        {"name": "Sequence", "diagram_type": "sequence"},
    ])
    assert report.has_errors
    assert {d.code for d in report.diagnostics} >= {"CLASS_ENDPOINT", "COMPONENT_PARENT_CYCLE"}
    checks = {c.rule_id: c.status for c in report.checks}
    assert checks["class.structure"] == checks["component.structure"] == checks["sequence.structure"] == "checked"
    assert checks["sequence.source"] == "not_applicable"


def test_registry_extension_requires_no_engine_or_tool_changes():
    class NewRule:
        rule_id = "custom.structure"
        diagram_types = ("custom",)
        def check(self, diagram, path, context):
            assert diagram["custom_field"] == "value"
            return CheckResult(self.rule_id, path, "checked")
    registry = ValidationRegistry([NewRule()])
    report = ProjectValidator(registry).validate([{"diagram_type": "custom", "custom_field": "value"}])
    assert report.status == "pass"
    assert report.checks[-1].rule_id == "custom.structure"


def test_selected_diagrams_keep_full_project_context():
    class Rule:
        rule_id = "context"
        diagram_types = ("class",)
        def check(self, diagram, path, context):
            assert len(context.diagrams) == 2
            return CheckResult(self.rule_id, path, "checked")
    diagrams = [{"name": "A"}, {"name": "B"}]
    report = ProjectValidator(ValidationRegistry([Rule()])).validate(diagrams, selected_keys={("class", "B")})
    assert all(c.path == "diagrams[1]" for c in report.checks)


def test_schema_errors_are_not_silently_normalized_away():
    report = validate_project_diagrams([{"name": "bad", "classes": [{"name": "missing id"}]}])
    assert report.has_errors
    assert report.diagnostics[0].code == "DESIGN_SCHEMA"


def test_unsupported_artifact_is_reported_not_passed():
    report = ProjectValidator(ValidationRegistry()).validate([{"diagram_type": "future"}])
    assert report.status == "partial"
    assert report.checks[0].status == "unsupported"


def test_failed_rule_does_not_disable_independent_checks():
    class BrokenRule:
        rule_id = "broken"
        diagram_types = ("class",)
        def check(self, diagram, path, context):
            raise RuntimeError("fact provider unavailable")
    class WorkingRule:
        rule_id = "working"
        diagram_types = ("class",)
        def check(self, diagram, path, context):
            return CheckResult(self.rule_id, path, "checked")
    report = ProjectValidator(ValidationRegistry([BrokenRule(), WorkingRule()])).validate([{}])
    assert report.status == "partial"
    assert {c.rule_id: c.status for c in report.checks} == {
        "schema": "checked", "broken": "unavailable", "working": "checked"}


def test_engine_and_rules_have_no_parser_or_plugin_dependencies():
    root = Path(__file__).resolve().parents[2] / "app" / "validation"
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                imports = [node.module or ""]
            else:
                continue
            assert not any(name.startswith(("extensions.", "subprocess", "app.services", "app.runtime")) or name == "ast" for name in imports), path
