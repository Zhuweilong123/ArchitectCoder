"""Rule dispatch only: no language parsers, UI, execution or commit policy."""
from app.agent_base.host_api.validation import CheckResult, ValidationContext, ValidationDiagnostic, ValidationReport
from app.models.uml import UmlDiagram
from pydantic import ValidationError


class ValidationRegistry:
    def __init__(self, rules=()):
        self._rules = {}
        for rule in rules:
            self.register(rule)

    def register(self, rule):
        if rule.rule_id in self._rules:
            raise ValueError(f"Duplicate validation rule: {rule.rule_id}")
        self._rules[rule.rule_id] = rule

    def rules_for(self, diagram_type):
        return [rule for rule in self._rules.values() if diagram_type in rule.diagram_types]


class ProjectValidator:
    def __init__(self, registry):
        self.registry = registry

    def validate(self, diagrams, context=None, selected_keys=None):
        context = context or ValidationContext()
        context.diagrams = tuple(diagrams)
        checks = []
        for index, raw in enumerate(diagrams):
            path = f"diagrams[{index}]"
            key = (raw.get("diagram_type", "class"), raw.get("name", "")) if isinstance(raw, dict) else None
            if selected_keys is not None and key not in selected_keys:
                continue
            dtype = raw.get("diagram_type", "class") if isinstance(raw, dict) else ""
            if not self.registry.rules_for(dtype):
                checks.append(CheckResult("schema", path, "unsupported", reason=f"Unsupported diagram type: {dtype}"))
                continue
            try:
                diagram = {**raw, **UmlDiagram.model_validate(raw).model_dump()}
            except ValidationError as exc:
                checks.append(CheckResult("schema", path, "checked", (
                    ValidationDiagnostic("error", "DESIGN_SCHEMA", path, str(exc)),)))
                continue
            checks.append(CheckResult("schema", path, "checked"))
            rules = self.registry.rules_for(dtype)
            if not rules:
                checks.append(CheckResult("rules", path, "unsupported", reason=f"No registered rules for {dtype}"))
            for rule in rules:
                try:
                    checks.append(rule.check(diagram, path, context))
                except Exception as exc:
                    checks.append(CheckResult(rule.rule_id, path, "unavailable", (
                        ValidationDiagnostic("warning", "VALIDATION_RULE_UNAVAILABLE", path,
                            f"Rule could not complete: {exc}"),), reason=str(exc)))
        return ValidationReport(tuple(checks))
