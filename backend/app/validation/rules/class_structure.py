from app.agent_base.host_api.validation import CheckResult, ValidationDiagnostic


class ClassStructureRule:
    rule_id = "class.structure"
    diagram_types = ("class",)

    def check(self, diagram, path, context):
        diagnostics = []
        ids = [item["id"] for item in diagram["classes"]]
        relation_ids = [item["id"] for item in diagram["relations"]]
        if any(not value for value in ids + relation_ids) or len(set(ids + relation_ids)) != len(ids + relation_ids):
            diagnostics.append(ValidationDiagnostic("error", "CLASS_ID", path, "Class and relation IDs must be non-empty and unique within the diagram."))
        for i, relation in enumerate(diagram["relations"]):
            if relation["source"] not in ids or relation["target"] not in ids:
                diagnostics.append(ValidationDiagnostic("error", "CLASS_ENDPOINT", f"{path}.relations[{i}]", "Relation references an unknown class."))
        return CheckResult(self.rule_id, path, "checked", tuple(diagnostics))
