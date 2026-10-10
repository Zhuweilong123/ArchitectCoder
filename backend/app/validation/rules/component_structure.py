from app.agent_base.host_api.validation import CheckResult, ValidationDiagnostic


class ComponentStructureRule:
    rule_id = "component.structure"
    diagram_types = ("component",)

    def check(self, diagram, path, context):
        diagnostics = []
        components = {item["id"]: item for item in diagram["components"]}
        ids = [item["id"] for item in diagram["components"] + diagram["comp_relations"]]
        if any(not value for value in ids) or len(set(ids)) != len(ids):
            diagnostics.append(ValidationDiagnostic("error", "COMPONENT_ID", path, "Component and relation IDs must be non-empty and unique within the diagram."))
        for item in components.values():
            seen, current = set(), item["id"]
            while current:
                if current in seen:
                    diagnostics.append(ValidationDiagnostic("error", "COMPONENT_PARENT_CYCLE", path, f"Containment cycle at {current}."))
                    break
                seen.add(current)
                if current not in components:
                    diagnostics.append(ValidationDiagnostic("error", "COMPONENT_PARENT", path, f"Unknown parent component: {current}."))
                    break
                current = components[current]["parent_id"]
        for i, relation in enumerate(diagram["comp_relations"]):
            if relation["source"] not in components or relation["target"] not in components:
                diagnostics.append(ValidationDiagnostic("error", "COMPONENT_ENDPOINT", f"{path}.comp_relations[{i}]", "Relation references an unknown component."))
        return CheckResult(self.rule_id, path, "checked", tuple(diagnostics))
