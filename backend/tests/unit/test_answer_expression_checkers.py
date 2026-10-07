"""Expression coverage does not replace artifact or execution evidence."""

import asyncio
import json
from pathlib import Path

import pytest

from extensions.evals.checkers import AnswerContainsAllChecker, build_checkers
from extensions.evals.models import EvalCase


CONFIG = {
    "type": "answer_contains_groups", "texts": ["removeTarget"],
    "groups": [
        {"id": "source", "any_of": ["源码", "源代码", "source"]},
        {"id": "verification", "any_of": ["测试", "test", "tests", "pytest"]},
    ],
}


@pytest.mark.parametrize("answer,passed,missing", [
    ("removeTarget 源代码修改完成，测试通过", True, []),
    ("removeTarget Source & TESTS: 39 passed", True, []),
    ("removeTarget resource contest", False, ["source", "verification"]),
    ("removeTarget source updated", False, ["verification"]),
    ("removeTarget tests passed", False, ["source"]),
    ("removetarget source tests", False, []),
])
def test_groups_require_every_topic_and_preserve_exact_identifiers(tmp_path, answer, passed, missing):
    result = asyncio.run(build_checkers([CONFIG], answer=answer)[0].check(tmp_path))
    assert result.passed is passed
    assert result.details["missing_groups"] == missing
    assert result.details["scope"] == "text_coverage"
    if "removetarget" in answer:
        assert result.details["missing_texts"] == ["removeTarget"]


def test_groups_normalize_full_width_case_and_whitespace(tmp_path):
    config = {"type": "answer_contains_groups", "groups": [
        {"id": "source", "any_of": ["source code"]},
    ]}
    result = asyncio.run(build_checkers([config], answer="ＳＯＵＲＣＥ\n  ＣＯＤＥ")[0].check(tmp_path))
    assert result.passed
    assert result.details["matched_groups"] == {"source": ["source code"]}


@pytest.mark.parametrize("groups", [
    [], {}, ["source"], [{"id": "x", "any_of": []}],
    [{"id": "x", "any_of": [""]}], [{"id": "x", "any_of": [1]}],
    [{"id": "", "any_of": ["test"]}],
    [{"id": "x", "any_of": ["a"]}, {"id": "x", "any_of": ["b"]}],
])
def test_invalid_groups_are_rejected_before_running(groups):
    config = {"type": "answer_contains_groups", "groups": groups}
    with pytest.raises(ValueError):
        EvalCase(id="invalid", prompt="inspect", checkers=[config])
    with pytest.raises(ValueError):
        build_checkers([config])


def test_mentions_of_tests_do_not_prove_they_were_executed(tmp_path):
    trace = tmp_path / "empty.jsonl"
    trace.write_text("", encoding="utf-8")
    configs = [CONFIG, {"type": "trace_policy", "required_tools": ["run_task"]}]
    checkers = build_checkers(configs, answer="removeTarget 源码完成，未执行测试", trace_path=str(trace))
    results = [asyncio.run(checker.check(tmp_path)) for checker in checkers]
    assert results[0].passed  # Mentions the topic; makes no execution claim.
    assert not results[1].passed


def test_failed_program_verification_does_not_satisfy_execution_policy(tmp_path):
    trace = tmp_path / "failed.jsonl"
    trace.write_text("\n".join(json.dumps(row) for row in [
        {"event_type": "tool_call", "tool_name": "run_program"},
        {"event_type": "tool_result", "tool_name": "run_program", "evidence": {
            "effects": {"verification": {"kind": "test", "passed": False, "exit_code": 1}},
        }},
    ]), encoding="utf-8")
    checker = build_checkers([{"type": "trace_policy", "required_tools": ["run_task"]}],
                             trace_path=str(trace))[0]
    assert not asyncio.run(checker.check(tmp_path)).passed


def test_historical_keyword_false_negatives_pass_expression_coverage(tmp_path):
    root = Path(__file__).resolve().parents[3]
    samples = json.loads((root / "backend/tests/fixtures/evals/answer_keyword_regressions.json").read_text(encoding="utf-8"))
    for sample in samples:
        case = json.loads((root / "backend/evals/cases" / sample["case_file"]).read_text(encoding="utf-8"))
        EvalCase.model_validate(case)
        old = asyncio.run(AnswerContainsAllChecker(sample["old_texts"], sample["answer"]).check(tmp_path))
        new = asyncio.run(build_checkers(case["checkers"], answer=sample["answer"])[0].check(tmp_path))
        assert not old.passed, sample["case_file"]
        assert new.passed, (sample["case_file"], new.details)
        assert len(case["checkers"]) == 1  # Keep the existing score weight.
