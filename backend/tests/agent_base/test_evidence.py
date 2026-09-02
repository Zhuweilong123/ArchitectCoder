from app.agent_base.evidence import EvidenceLedger


def test_evidence_ledger_links_read_edit_and_verification():
    ledger = EvidenceLedger()
    read = ledger.record(
        call_id="read-1", tool_name="read_file",
        arguments={"path": "src/example.py", "offset": 20, "limit": 20},
        observation="old_name = 'Element'\n" * 100,
    )
    edit = ledger.record(
        call_id="edit-1", tool_name="edit_file",
        arguments={
            "path": "src/example.py", "old_text": "old_name = 'Element'",
            "new_text": "old_name = ''",
        },
        observation="Edited src/example.py (sha256=abc123def4567890)",
    )
    ledger.record(
        call_id="test-1", tool_name="bash",
        arguments={"command": "pytest tests/test_example.py", "cwd": "source"},
        observation="1 passed",
    )

    summary = ledger.summary_for(["read-1", "edit-1", "test-1"])

    assert "file=src/example.py" in summary["read-1"]
    assert f"used_by_edit={edit.id}" in summary["read-1"]
    assert "target_sha=" in summary["edit-1"]
    assert "verified" in summary["edit-1"]
    assert "command='pytest tests/test_example.py'" in summary["test-1"]
    assert read.id == "E1"


def test_evidence_ledger_normalizes_bash_failure():
    ledger = EvidenceLedger()
    record = ledger.record(
        call_id="bash-1", tool_name="bash",
        arguments={"command": "pytest tests/test_broken.py"},
        observation="Error: command exited with code 1: FAILED test_broken.py::test_case",
        status="error", error_code="TOOL_REPORTED_ERROR",
    )

    rendered = ledger.summary_for(["bash-1"])["bash-1"]
    assert "exit_code=1" in rendered
    assert "error_code=TOOL_REPORTED_ERROR" in rendered
    assert "FAILED test_broken.py" in rendered
    assert record.status == "error"


def test_evidence_ledger_extracts_project_and_comparison_facts_without_raw_json():
    ledger = EvidenceLedger(max_records=4)
    project = ledger.record(
        call_id="map", tool_name="get_project_map", arguments={},
        observation='{"project_id":"radar","stats":{"total_nodes":12,"total_edges":15},"diagrams":[{},{}],"files":{"source_count":4,"test_count":2}}',
    )
    comparison = ledger.record(
        call_id="compare", tool_name="compare_design_code", arguments={},
        observation='{"summary":{"coverage_rate":0.75,"mismatches":1,"missing_implementations":2,"extra_code":3}}',
    )

    rendered = ledger.summary_for(["map", "compare"])

    assert "project_id=radar" in rendered["map"]
    assert "total_nodes=12" in rendered["map"]
    assert "coverage_rate=0.75" in rendered["compare"]
    assert "missing_implementations=2" in rendered["compare"]
    assert "{" not in project.detail
    assert "{" not in comparison.detail
