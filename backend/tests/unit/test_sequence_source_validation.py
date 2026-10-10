import asyncio
import copy
import json

import pytest

from app.models.uml import UmlDiagram
from app.services.sequence_validation import validate_sequence_diagrams
from app.agent_base.tools.review import ReviewManager, SubmitUmlReviewTool


@pytest.fixture
def source_diagram(tmp_path):
    (tmp_path / "planner.py").write_text(
        "class Planner:\n"
        "    def plan(self, bad):\n"
        "        knots = self.build_knots()\n"
        "        if bad:\n"
        "            return []\n"
        "        result = self.solve(knots)\n"
        "        return result\n", encoding="utf-8")
    def message(id, order, label, source_line=None, kind="call", reply=""):
        value = {"id": id, "order": order, "y": 180 + order * 100,
                 "from_lifeline": "planner", "to_lifeline": "worker", "type": "sync", "label": label}
        if source_line:
            value["source_refs"] = [{"scope_id": "plan", "line": source_line, "kind": kind}]
        if reply:
            value.update(type="return", to_lifeline="caller", reply_to=reply)
        return value
    diagram = {
        "name": "Plan", "diagram_type": "sequence",
        "lifelines": [{"id": "caller", "x": 100}, {"id": "planner", "x": 350}, {"id": "worker", "x": 600}],
        "messages": [
            {"id": "entry", "from_lifeline": "caller", "to_lifeline": "planner", "type": "sync", "order": 1, "y": 280},
            message("build", 2, "build_knots()", 3),
            message("early", 3, "return []", 5, "return", "entry"),
            message("solve", 4, "solve(knots)", 6),
            message("done", 5, "return result", 7, "return", "entry"),
        ],
        "fragments": [{"id": "failure", "type": "break", "x": 80, "width": 640, "y_start": 450, "y_end": 550,
                       "lifeline_ids": ["caller", "planner", "worker"],
                       "operands": [{"id": "failed", "guard": "[bad]", "message_ids": ["early"], "y_start": 460, "y_end": 540,
                                     "source_guard": {"scope_id": "plan", "line": 4, "kind": "condition"}}]}],
        "source_scopes": [{"id": "plan", "path": "planner.py", "symbol": "Planner.plan", "lifeline_id": "planner",
                           "entry_message_id": "entry", "coverage": "returns"}],
    }
    return tmp_path, diagram


def codes(root, diagram):
    return {d.code for d in validate_sequence_diagrams([diagram], str(root)) if d.severity == "error"}


def test_contract_reuses_collected_facts_and_checks_design_only_changes(source_diagram):
    from extensions.design_contract.contract_harness import ContractHarness
    from extensions.design_contract.language_adapters import LanguageAdapterRegistry, PythonAstAdapter
    from extensions.design_contract.provider import DesignContractProvider
    root, diagram = source_diagram
    source_root = root / "src"
    source_root.mkdir()
    (root / "planner.py").rename(source_root / "planner.py")
    diagram["source_scopes"][0]["path"] = "src/planner.py"
    diagram["messages"][1]["order"], diagram["messages"][3]["order"] = 4, 2
    project = root / "design.umlproj"
    project.write_text(json.dumps({"diagrams": [diagram]}), encoding="utf-8")

    class CountingAdapter(PythonAstAdapter):
        count = 0
        def extract(self, *args, **kwargs):
            self.count += 1
            return super().extract(*args, **kwargs)

    adapter = CountingAdapter()
    provider = DesignContractProvider(language_adapters=LanguageAdapterRegistry((adapter,)))
    result = ContractHarness().check({
        "workspace_root": str(root), "source_root": str(source_root),
        "project_file": str(project),
    }, changed_paths=[str(project)], contract_provider=provider)
    assert result.status == "block"
    assert "SEQ_SOURCE_ORDER" in {v.code for v in result.violations}
    assert result.metadata["design_validation"][0]["status"] == "fail"
    assert adapter.count == 1


def test_contract_design_only_does_not_require_new_class_implementation(tmp_path):
    from extensions.design_contract.contract_harness import ContractHarness
    from extensions.design_contract.provider import DesignContractProvider
    source_root = tmp_path / "src"
    source_root.mkdir()
    (source_root / "existing.py").write_text("class Existing: pass\n", encoding="utf-8")
    project = tmp_path / "design.umlproj"
    project.write_text(json.dumps({"diagrams": [{"diagram_type": "class", "classes": [
        {"id": "new", "name": "FutureClass"}]}]}), encoding="utf-8")
    manifest = {"workspace_root": str(tmp_path), "source_root": str(source_root), "project_file": str(project)}
    provider = DesignContractProvider()
    result = ContractHarness().check(manifest, changed_paths=[str(project)], contract_provider=provider)
    assert result.status == "pass"
    result = ContractHarness().check(manifest, changed_paths=[str(source_root / "existing.py")], contract_provider=provider)
    assert result.status == "block"
    assert "missing_implementation" in {v.code for v in result.violations}


def test_valid_source_evidence_and_persistence(source_diagram):
    root, diagram = source_diagram
    loaded = UmlDiagram.model_validate(diagram).model_dump()
    assert not codes(root, loaded)
    assert loaded["source_scopes"][0]["symbol"] == "Planner.plan"
    assert loaded["messages"][2]["reply_to"] == "entry"
    assert loaded["fragments"][0]["operands"][0]["source_guard"]["line"] == 4


def test_bounded_source_check_satisfies_declared_scope_but_missing_exits_do_not(source_diagram):
    from app.services.design_validation import validate_project_diagrams
    root, diagram = source_diagram
    requirements = [{"rule_id": "sequence.source", "diagram_name": "Plan"}]
    report = validate_project_diagrams([diagram], str(root), requirements=requirements)
    assert not report.has_errors
    source = next(c for c in report.checks if c.rule_id == "sequence.source")
    assert source.status == "partial" and source.coverage_met
    diagram["source_scopes"][0]["coverage"] = "partial"
    report = validate_project_diagrams([diagram], str(root), requirements=requirements)
    assert "VALIDATION_REQUIREMENT_UNMET" in {d.code for d in report.diagnostics}


def test_reversed_build_and_solve_is_blocked(source_diagram):
    root, diagram = source_diagram
    diagram["messages"][1]["order"], diagram["messages"][3]["order"] = 4, 2
    assert "SEQ_SOURCE_ORDER" in codes(root, diagram)


def test_conditional_exit_as_unguarded_text_is_blocked(source_diagram):
    root, diagram = source_diagram
    diagram["fragments"] = []
    diagram["messages"][2]["label"] = "if bad -> return []"
    assert "SEQ_EARLY_EXIT_GUARD" in codes(root, diagram)


def test_precondition_exit_cannot_be_deferred_until_after_solver(source_diagram):
    root, diagram = source_diagram
    diagram["messages"][2]["order"] = 5
    assert "SEQ_EARLY_EXIT_ORDER" in codes(root, diagram)


def test_visual_order_cannot_reverse_declared_call_order(source_diagram):
    root, diagram = source_diagram
    diagram["messages"][1]["y"], diagram["messages"][3]["y"] = 580, 380
    assert "SEQ_SOURCE_ORDER" in codes(root, diagram)


def test_missing_parent_exit_cannot_be_replaced_by_child_reply(source_diagram):
    root, diagram = source_diagram
    diagram["messages"][2].pop("source_refs")
    assert "SEQ_SOURCE_RETURN_MISSING" in codes(root, diagram)


@pytest.mark.parametrize("mutation", ["self_return", "child_reply", "future_reply"])
def test_bad_return_relationships_are_blocked(source_diagram, mutation):
    root, diagram = source_diagram
    early = diagram["messages"][2]
    if mutation == "self_return":
        early["to_lifeline"] = "planner"
    elif mutation == "child_reply":
        early.update(from_lifeline="worker", to_lifeline="planner", reply_to="build")
    else:
        early["reply_to"] = "solve"
    assert codes(root, diagram) & {"SEQ_REPLY_MISMATCH", "SEQ_SOURCE_RETURN"}


@pytest.mark.parametrize("path", ["../escape.py", "missing.py"])
def test_source_boundary_and_missing_source(source_diagram, path):
    root, diagram = source_diagram
    diagram["source_scopes"][0]["path"] = path
    assert "SEQ_SOURCE_REFERENCE" in codes(root, diagram)


def test_forged_line_and_wrong_call_name_are_rejected(source_diagram):
    root, diagram = source_diagram
    diagram["messages"][1]["source_refs"][0]["line"] = 6
    assert "SEQ_SOURCE_CALL_LABEL" in codes(root, diagram)
    diagram["messages"][1]["source_refs"][0]["line"] = 2
    assert "SEQ_SOURCE_REFERENCE" in codes(root, diagram)


def test_legacy_and_partial_source_are_explicitly_unverified(source_diagram):
    root, diagram = source_diagram
    partial = copy.deepcopy(diagram)
    partial["source_scopes"][0]["coverage"] = "partial"
    assert "SEQ_SOURCE_PARTIAL" in {d.code for d in validate_sequence_diagrams([partial], str(root))}
    diagram.pop("source_scopes")
    assert "SEQ_SOURCE_UNVERIFIED" in {d.code for d in validate_sequence_diagrams([diagram], str(root))}


def test_mutually_exclusive_calls_are_not_forced_into_source_order(source_diagram):
    root, diagram = source_diagram
    (root / "planner.py").write_text("class Planner:\n    def plan(self, bad):\n        if bad:\n            self.build_knots()\n        else:\n            self.solve()\n", encoding="utf-8")
    diagram["source_scopes"][0]["coverage"] = "partial"
    diagram["messages"] = [diagram["messages"][0], diagram["messages"][1], diagram["messages"][3]]
    diagram["messages"][1]["source_refs"][0]["line"] = 4
    diagram["messages"][1]["order"], diagram["messages"][2]["order"] = 4, 2
    diagram["fragments"] = []
    assert "SEQ_SOURCE_ORDER" not in codes(root, diagram)


def test_review_blocks_semantic_error_before_request(source_diagram):
    root, diagram = source_diagram
    diagram["messages"][1]["order"], diagram["messages"][3]["order"] = 4, 2
    manager = ReviewManager()
    tool = SubmitUmlReviewTool(manager, workspace_root=str(root))
    result = asyncio.run(tool._execute({"diagrams_json": json.dumps([diagram])}))
    assert "SEQ_SOURCE_ORDER" in result
    assert not manager.approval_events


def test_multiline_nested_call_uses_evaluation_order(source_diagram):
    root, diagram = source_diagram
    (root / "planner.py").write_text("class Planner:\n    def plan(self, bad):\n        return self.solve(\n            self.build_knots()\n        )\n", encoding="utf-8")
    diagram["messages"] = [diagram["messages"][0], diagram["messages"][1], diagram["messages"][3]]
    diagram["messages"][1]["source_refs"][0]["line"] = 4
    diagram["messages"][2]["source_refs"][0]["line"] = 3
    diagram["fragments"] = []
    diagram["source_scopes"][0]["coverage"] = "partial"
    assert not codes(root, diagram)


def test_run_task_blocks_order_error(source_diagram):
    from app.agent_base.tools.my_tools.foundation_tools import create_foundation_tools
    root, diagram = source_diagram
    diagram["messages"][1]["order"], diagram["messages"][3]["order"] = 4, 2
    (root / "model.umlproj").write_text(json.dumps({"diagrams": [diagram]}), encoding="utf-8")
    tool = next(t for t in create_foundation_tools("", "", "", workspace_root=str(root)) if t.name == "run_task")
    result = asyncio.run(tool._execute({"task": "validate", "target": "model.umlproj", "cwd": "workspace"}))
    assert "SEQ_SOURCE_ORDER" in result


def test_loop_order_is_explicitly_unverified(source_diagram):
    root, diagram = source_diagram
    (root / "planner.py").write_text("class Planner:\n    def plan(self, bad):\n        for i in range(2):\n            self.build_knots()\n            self.solve()\n", encoding="utf-8")
    diagram["messages"] = [diagram["messages"][0], diagram["messages"][1], diagram["messages"][3]]
    diagram["messages"][1]["source_refs"][0]["line"] = 4
    diagram["messages"][2]["source_refs"][0]["line"] = 5
    diagram["fragments"] = []
    diagram["source_scopes"][0]["coverage"] = "partial"
    assert "SEQ_SOURCE_ORDER_UNVERIFIED" in {d.code for d in validate_sequence_diagrams([diagram], str(root))}


def test_reply_reference_is_checked_without_source_scopes(source_diagram):
    _, diagram = source_diagram
    diagram.pop("source_scopes")
    diagram["messages"][2]["to_lifeline"] = "planner"
    assert "SEQ_REPLY_MISMATCH" in {d.code for d in validate_sequence_diagrams([diagram])}


def test_cpp_flow_uses_same_rules_through_registered_language_adapter(source_diagram):
    from types import SimpleNamespace
    from extensions.design_contract.provider import DesignContractProvider
    from extensions.design_contract.language_adapters import ClangAstAdapter, LanguageAdapterRegistry
    from app.services.design_validation import validate_project_diagrams
    root, diagram = source_diagram
    source = root / "planner.cpp"
    source.write_text("struct Planner {\nint plan(bool bad) {\nbuild_knots();\nif(bad)\nreturn 0;\nsolve();\nreturn 1;\n}\n};", encoding="utf-8")
    (root / "compile_commands.json").write_text(json.dumps([
        {"directory": str(root), "file": "planner.cpp", "arguments": ["clang++", "-std=c++17", "-c", "planner.cpp"]}
    ]), encoding="utf-8")
    def node(kind, line, **kwargs):
        return {"kind": kind, "range": {"begin": {"line": line}}, **kwargs}
    def call(name, line):
        return node("CXXMemberCallExpr", line, inner=[{"kind": "MemberExpr", "name": name}])
    document = {"kind": "TranslationUnitDecl", "inner": [{"kind": "CXXRecordDecl", "name": "Planner", "inner": [
        {"kind": "CXXMethodDecl", "name": "plan", "inner": [{"kind": "CompoundStmt", "inner": [
            call("build_knots", 3), node("IfStmt", 4, inner=[node("DeclRefExpr", 4), node("ReturnStmt", 5)]),
            call("solve", 6), node("ReturnStmt", 7),
        ]}]},
    ]}]}
    executions = []
    def runner(argv, **kwargs):
        executions.append(argv)
        return SimpleNamespace(returncode=0, stdout=json.dumps(document), stderr="")
    provider = DesignContractProvider(language_adapters=LanguageAdapterRegistry([ClangAstAdapter(runner=runner)]))
    diagram["source_scopes"][0].update(path="planner.cpp", symbol="Planner::plan")
    report = validate_project_diagrams([diagram], str(root), source_provider=provider)
    assert not report.has_errors
    assert len(executions) == 1
    diagram["messages"][1]["order"], diagram["messages"][3]["order"] = 4, 2
    report = validate_project_diagrams([diagram], str(root), source_provider=provider)
    assert "SEQ_SOURCE_ORDER" in {d.code for d in report.diagnostics}


def test_unavailable_parser_is_explicit_coverage_not_fake_success(source_diagram):
    from app.agent_base.host_api.validation import SourceArtifact
    from app.services.design_validation import validate_project_diagrams
    root, diagram = source_diagram
    class Unavailable:
        def source_facts(self, path, *, workspace_root):
            return SourceArtifact("failed", reason="Compiler runner not configured")
    report = validate_project_diagrams([diagram], str(root), source_provider=Unavailable())
    assert report.status == "partial"
    assert not report.has_errors
    assert next(c for c in report.checks if c.rule_id == "sequence.source").status == "unavailable"
