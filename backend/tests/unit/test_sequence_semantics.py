"""Regression tests for sequence control-flow representation and agent gates."""
import asyncio
import json
from pathlib import Path

import pytest

from app.models.uml import Project, UmlDiagram
from app.services.project_repository import ProjectRepository
from app.services.sequence_validation import validate_sequence_diagrams
from app.agent_base.tools.my_tools.foundation_tools import create_foundation_tools
from app.agent_base.tools.review import ReviewManager, SubmitUmlReviewTool


def sequence():
    return {
        "name": "Choice", "diagram_type": "sequence",
        "lifelines": [{"id": "caller", "x": 100}, {"id": "service", "x": 400}],
        "messages": [
            {"id": "ok", "from_lifeline": "service", "to_lifeline": "caller", "type": "return", "y": 280, "order": 1},
            {"id": "fail", "from_lifeline": "service", "to_lifeline": "caller", "type": "return", "y": 400, "order": 2},
        ],
        "fragments": [{
            "id": "choice", "type": "alt", "x": 80, "width": 540, "y_start": 200, "y_end": 450,
            "lifeline_ids": ["caller", "service"],
            "operands": [
                {"id": "success", "guard": "[valid]", "message_ids": ["ok"], "y_start": 240, "y_end": 320},
                {"id": "failure", "guard": "[else]", "message_ids": ["fail"], "y_start": 340, "y_end": 440},
            ],
        }],
    }


def codes(diagram):
    return {d.code for d in validate_sequence_diagrams([diagram]) if d.severity == "error"}


def test_explicit_alt_roundtrips_without_losing_operands(tmp_path):
    diagram = sequence()
    assert validate_sequence_diagrams([diagram]) == []
    repository = ProjectRepository()
    path = tmp_path / "model.umlproj"
    repository.save(Project(diagrams=[UmlDiagram.model_validate(diagram)]), path)
    restored = repository.load(path).diagrams[0]
    assert restored.fragments[0].operands[1].guard == "[else]"
    assert restored.fragments[0].operands[0].message_ids == ["ok"]
    assert restored.fragments[0].lifeline_ids == ["caller", "service"]


@pytest.mark.parametrize("mutation, expected", [
    (lambda d: d["fragments"][0]["operands"].pop(), "SEQ_OPERAND_COUNT"),
    (lambda d: d["fragments"][0]["operands"][0].update(guard=""), "SEQ_GUARD"),
    (lambda d: d["fragments"][0]["operands"][0].update(guard="[else]"), "SEQ_ELSE_GUARD"),
    (lambda d: d["fragments"][0]["operands"][1].update(message_ids=["missing"]), "SEQ_MESSAGE_REFERENCE"),
    (lambda d: d["fragments"][0]["operands"][1].update(message_ids=["ok"]), "SEQ_MESSAGE_OWNERSHIP"),
    (lambda d: d["fragments"][0]["operands"][1].update(y_start=300), "SEQ_OPERAND_OVERLAP"),
    (lambda d: d["fragments"][0]["operands"][1].update(y_end=500), "SEQ_OPERAND_RANGE"),
    (lambda d: d["messages"][0].update(y=100), "SEQ_MESSAGE_RANGE"),
    (lambda d: d["messages"][0].update(type="self"), "SEQ_SELF_ENDPOINT"),
    (lambda d: d["messages"][0].update(to_lifeline="missing"), "SEQ_MESSAGE_ENDPOINT"),
    (lambda d: d["fragments"][0].update(lifeline_ids=[]), "SEQ_LIFELINE_COVERAGE"),
    (lambda d: d["fragments"][0].update(width=100), "SEQ_FRAME_COVERAGE"),
    (lambda d: d["fragments"][0]["operands"][1].update(message_ids=[]), "SEQ_UNASSIGNED_MESSAGE"),
])
def test_invalid_branch_structures_have_actionable_codes(mutation, expected):
    diagram = sequence()
    mutation(diagram)
    assert expected in codes(diagram)


def test_legacy_alt_warns_without_inventing_a_success_operand():
    diagram = sequence()
    del diagram["fragments"][0]["operands"]
    diagnostics = validate_sequence_diagrams([diagram])
    assert not codes(diagram)
    assert any(d.code == "SEQ_LEGACY_OPERANDS" and d.severity == "warning" for d in diagnostics)
    assert UmlDiagram.model_validate(diagram).fragments[0].operands == []


def test_break_covers_idle_enclosing_lifelines():
    diagram = sequence()
    fragment = diagram["fragments"][0]
    fragment.update(type="break", operands=[fragment["operands"][1]], lifeline_ids=["caller", "service"])
    fragment["operands"][0]["guard"] = "[failed]"
    diagram["lifelines"].append({"id": "idle", "x": 600})
    assert "SEQ_BREAK_COVERAGE" in codes(diagram)
    fragment.update(lifeline_ids=["caller", "service", "idle"], width=700)
    assert not codes(diagram)


def test_nesting_checks_parent_operand_and_cycles():
    diagram = sequence()
    child = {
        "id": "child", "type": "opt", "x": 80, "width": 540, "y_start": 245, "y_end": 315,
        "lifeline_ids": ["caller", "service"], "parent_fragment_id": "choice", "parent_operand_id": "success",
        "operands": [{"id": "child_op", "guard": "[enabled]", "message_ids": ["ok"], "y_start": 260, "y_end": 310}],
    }
    diagram["fragments"][0]["operands"][0]["message_ids"] = []
    diagram["fragments"].append(child)
    assert not codes(diagram)
    child["parent_operand_id"] = "missing"
    assert "SEQ_PARENT_REFERENCE" in codes(diagram)
    child["parent_operand_id"] = "success"
    diagram["fragments"][0].update(parent_fragment_id="child", parent_operand_id="child_op")
    assert "SEQ_PARENT_CYCLE" in codes(diagram)


def test_schema_errors_are_diagnostics_not_validator_crashes():
    diagram = sequence()
    diagram["fragments"][0]["operands"] = {"bad": "shape"}
    assert "SEQ_SCHEMA" in codes(diagram)


def test_agent_validate_rejects_invalid_branches_and_reports_legacy_warnings(tmp_path):
    path = tmp_path / "model.umlproj"
    tool = next(t for t in create_foundation_tools("", "", "", workspace_root=str(tmp_path)) if t.name == "run_task")
    diagram = sequence()
    diagram["fragments"][0]["operands"].pop()
    path.write_text(json.dumps({"diagrams": [diagram]}), encoding="utf-8")
    result = asyncio.run(tool._execute({"task": "validate", "target": "model.umlproj", "cwd": "workspace"}))
    assert "SEQ_OPERAND_COUNT" in result and "Error:" in result
    del diagram["fragments"][0]["operands"]
    path.write_text(json.dumps({"diagrams": [diagram]}), encoding="utf-8")
    result = asyncio.run(tool._execute({"task": "validate", "target": "model.umlproj", "cwd": "workspace"}))
    assert "SEQ_LEGACY_OPERANDS" in result and "semantic warnings remain" in result


def test_review_rejects_invalid_branch_before_auto_approval():
    diagram = sequence()
    diagram["fragments"][0]["operands"].pop()
    manager = ReviewManager(auto_approve_reviews=True)
    result = asyncio.run(SubmitUmlReviewTool(manager)._execute({"diagrams_json": [diagram]}))
    assert "SEQ_OPERAND_COUNT" in result
    assert manager.approval_events == []


def test_reference_example_has_valid_optional_branch():
    root = Path(__file__).resolve().parents[3]
    content = (root / "skills/uml-design-guide/sequence_diagram_example.md").read_text(encoding="utf-8")
    for block in content.split("```json")[1:]:
        diagram = json.loads(block.split("```", 1)[0])
        assert validate_sequence_diagrams([diagram]) == []


def test_knowledge_graph_retains_branch_semantics(tmp_path):
    from extensions.knowledge_graph.builder import GraphBuilder
    from extensions.knowledge_graph.database import KnowledgeGraphDB
    path = str(tmp_path / "kg.db")
    builder = GraphBuilder(db_path=path)
    builder.build_from_project(Project(diagrams=[UmlDiagram.model_validate(sequence())]), "p")
    builder.close()
    db = KnowledgeGraphDB(path)
    try:
        node = db.find_nodes("p", node_type="fragment", source="design")[0]
        assert node.properties["operands"][0]["message_ids"] == ["ok"]
        assert node.properties["lifeline_ids"] == ["caller", "service"]
    finally:
        db.close()
