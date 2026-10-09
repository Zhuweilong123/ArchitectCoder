"""Regression coverage for cross-turn loss of cleanup/recovery evidence."""

from app.agent_base.execution_summary import build_task_execution_summary


def _file_call(operation, path, status="success"):
    return {
        "name": "apply_changes",
        "status": status,
        "arguments": {"changes": [{"op": operation, "path": path}]},
        "changes": [{"operation": operation, "path": path}],
        "observation": f"Applied changes: {operation}",
    }


def test_late_cleanup_and_recovery_survive_cross_turn_summary():
    """Reproduce the Oct 8 trace: exploration hides cleanup after a failure."""
    calls = [
        {"name": "read_file", "status": "success", "arguments": {"path": f"source_{i}.py"}}
        for i in range(20)
    ]
    calls += [
        _file_call("create", "_probe_noscipy.py"),
        _file_call("create", ".pytest_report.xml"),
        _file_call("delete", "_probe_noscipy.py"),
        _file_call("delete", ".pytest_report.xml"),
        {
            "name": "apply_changes", "status": "error",
            "error_code": "CHANGE_COMMIT_FAILED",
            "arguments": {"changes": [{"op": "delete", "path": ".pytest_basetemp"}]},
            "observation": "changes rolled back: directory is not empty",
        },
        _file_call("create", "_cleanup_tmp.py"),
        {
            "name": "run_program", "status": "success",
            "arguments": {"program": "python", "args": ["_cleanup_tmp.py"], "cwd": "workspace"},
            "observation": "cleaned .pytest_basetemp",
        },
        _file_call("delete", "_cleanup_tmp.py"),
        {
            "name": "shell", "status": "success",
            "arguments": {"command": "Test-Path .pytest_basetemp", "cwd": "workspace"},
            "observation": "False",
        },
        {
            "name": "shell", "status": "success",
            "arguments": {"command": "git status --short", "cwd": "workspace"},
            "observation": " M design/uml/project.umlproj\n M tests/test_speed_ros.py",
        },
    ]
    summary = build_task_execution_summary(calls, {
        "changed_files": ["_probe_noscipy.py", ".pytest_report.xml", "_cleanup_tmp.py"],
        "stop_reason": "verification_failed",
    }, "partial")

    assert summary.index("CHANGE_COMMIT_FAILED") < summary.index("cleaned .pytest_basetemp")
    assert "python _cleanup_tmp.py" in summary
    assert "Test-Path .pytest_basetemp" in summary and "False" in summary
    assert "git status --short" in summary and "tests/test_speed_ros.py" in summary
    assert "not current existence or remaining changes" in summary
    final_operations = summary.split("Last successful file-tool operations", 1)[1]
    for path in ("_probe_noscipy.py", ".pytest_report.xml", "_cleanup_tmp.py"):
        assert f"{path}: delete" in final_operations
        assert f"{path}: create" not in final_operations
    assert len([line for line in summary.splitlines() if line.startswith("- [")]) == len(calls)
    assert "Additional successful calls" not in summary


def test_repeated_calls_keep_changed_results_and_all_failures_in_order():
    calls = [
        {"name": "shell", "status": "success", "arguments": {"command": "Test-Path temp.py"}, "observation": "True"},
        *[
            {"name": "shell", "status": "error", "error_code": "RETRY_FAILED", "observation": f"failure {i}"}
            for i in range(20)
        ],
        {"name": "shell", "status": "success", "arguments": {"command": "Test-Path temp.py"}, "observation": "False"},
    ]
    summary = build_task_execution_summary(calls, {}, "completed")
    assert summary.index("True") < summary.index("failure 19") < summary.index("False")
    assert summary.count("RETRY_FAILED") == 20
    assert summary.count("Test-Path temp.py") == 2


def test_failed_effects_do_not_override_last_successful_file_operation():
    failed = _file_call("delete", "kept.py", status="error")
    failed["observation"] = "changes rolled back"
    # Also cover the trace evidence representation of effects.
    created = _file_call("create", "kept.py")
    created["evidence"] = {"effects": {"changes": created.pop("changes")}}
    summary = build_task_execution_summary([created, failed], {}, "partial")
    final_operations = summary.split("Last successful file-tool operations", 1)[1]
    assert "kept.py: create" in final_operations
    assert "kept.py: delete" not in final_operations
