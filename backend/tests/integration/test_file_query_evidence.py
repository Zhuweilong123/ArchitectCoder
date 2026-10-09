"""Queries and partial results remain explicit across the model boundary."""

import asyncio
import json
import re
import os
from datetime import datetime, timezone

import pytest

from app.agent_base.assembly import enabled_tools_context
from app.agent_base.execution_summary import build_task_execution_summary
from app.agent_base.agents.react_runtime.tool_round_executor import ToolRoundExecutor
from app.agent_base.tools.my_tools.foundation_tools import ListFilesTool
from app.agent_base.tools.registry import ToolRegistry
from app.agent_base.tools.tool_output import ReadToolOutputTool


def query(tool, **params):
    return asyncio.run(tool.run_result(params))


def metadata(text):
    header = text.splitlines()[0]
    return json.loads(header[len("[file query "):-1])


def test_multi_pattern_union_finds_artifacts_without_duplicate_entries(tmp_path):
    for name in ("draft.tmp", "backup.bak", "run.log", "cache.pyc", "source.py"):
        (tmp_path / name).write_text("data", encoding="utf-8")
    tool = ListFilesTool(workspace_root=str(tmp_path))
    result = query(tool, patterns=["**/*.tmp", "**/*.bak", "**/*.log", "**/*.pyc", "*.log"], details=False)
    assert result.status == "success"
    info = metadata(result.text)
    assert info["matched"] == info["shown"] == 4
    assert info["entry_limit_truncated"] is False
    entries = result.text.splitlines()[1:]
    assert len(entries) == len(set(entries)) == 4
    assert str(tmp_path / "run.log") in entries
    assert str(tmp_path / "source.py") not in entries


def test_empty_result_keeps_literal_query_scope_and_later_matches(tmp_path):
    tool = ListFilesTool(workspace_root=str(tmp_path))
    # Braces are legal filename characters: no guessing/rejection of literals.
    empty = query(tool, pattern="**/*.{tmp,log}", details=False)
    assert set(metadata(empty.text)["patterns"]) == {"**/*.{tmp,log}", "**/*.tmp", "**/*.log"}
    assert "matched entries only" in empty.text
    assert "(no matches)" in empty.text
    (tmp_path / "run.log").write_text("log", encoding="utf-8")
    assert metadata(query(tool, pattern="**/*.{tmp,log}").text)["matched"] == 1
    found = query(tool, patterns=["**/*.tmp", "**/*.log"], details=False)
    assert metadata(found.text)["matched"] == 1
    assert str(tmp_path / "run.log") in found.text
    summary = build_task_execution_summary([
        {"name": "list_files", "status": "success", "observation": empty.text},
        {"name": "list_files", "status": "success", "observation": found.text},
    ], {}, "completed")
    assert summary.index('"matched": 0') < summary.index('"matched": 1')
    (tmp_path / "literal.{tmp,log}").write_text("literal", encoding="utf-8")
    assert metadata(query(tool, pattern="**/*.{tmp,log}").text)["matched"] == 2


@pytest.mark.parametrize("params", [
    {"patterns": []}, {"patterns": "*.py"}, {"patterns": ["*.py", 1]},
    {"patterns": ["../*.py"]}, {"patterns": [""]},
    {"pattern": "*.py", "patterns": ["*.log"]}, {"pattern": False},
])
def test_invalid_multi_pattern_queries_are_errors_not_empty_success(tmp_path, params):
    result = query(ListFilesTool(workspace_root=str(tmp_path)), **params)
    assert result.error_code == "INVALID_ARGUMENT"
    assert result.retryable


def test_entry_limit_is_visible_before_partial_listing(tmp_path):
    for name in ("run_20260928.log", "run_20261008.log"):
        (tmp_path / name).write_text("log", encoding="utf-8")
    result = query(ListFilesTool(workspace_root=str(tmp_path)), pattern="*.log", limit=1, details=False)
    info = metadata(result.text)
    assert info["matched"] == 2 and info["shown"] == 1
    assert info["entry_limit_truncated"] is True
    assert "entry limit" in result.text


def test_newer_logs_beyond_first_page_are_recoverable(tmp_path):
    # Explicit query ordering makes the newer log follow a long older prefix.
    for i in range(80):
        (tmp_path / f"run_20260928_{i:03d}.log").write_text("old", encoding="utf-8")
    (tmp_path / "run_20261008.log").write_text("new", encoding="utf-8")
    registry = ToolRegistry()
    registry.register_tool(ListFilesTool(workspace_root=str(tmp_path)))
    registry.register_tool(ReadToolOutputTool())
    executor = ToolRoundExecutor(registry, agent_name="Test")
    params = {"patterns": ["run_20260928*.log", "run_20261008.log"], "details": False}
    result = asyncio.run(executor.execute([{
        "id": "logs", "type": "function", "function": {
            "name": "list_files", "arguments": json.dumps(params),
        },
    }], step=1))
    first = result.tool_results[0]["content"]
    assert metadata(first)["matched"] == 81
    assert "20261008" not in first.split("\n", 1)[1]
    assert result.details[0]["fed_truncated"]
    output_id, offset = re.search(r"output_id=([0-9a-f]{16}); next_offset=(\d+)", first).groups()
    original = registry.output_store.read(output_id)
    assert "run_20261008.log" in original
    reader = registry.get_tool("read_tool_output")
    recovered = ""
    while int(offset) < len(original):
        page = reader.run({"output_id": output_id, "offset": int(offset)})
        recovered += page
        following = re.search(r"\[next_offset=(\d+|none)\]", page).group(1)
        if following == "none":
            break
        offset = following
    assert "run_20261008.log" in recovered


def test_query_statistics_replace_the_added_prompt_rules(tmp_path):
    assert "Evidence rules:" not in enabled_tools_context()
    (tmp_path / "run_20260928.log").write_text("old", encoding="utf-8")
    (tmp_path / "run_20261008.log").write_text("new", encoding="utf-8")
    (tmp_path / ".cache.pyc").write_text("cache", encoding="utf-8")
    (tmp_path / "draft.tmp").write_text("draft", encoding="utf-8")
    oldest = datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp()
    newest = datetime(2026, 10, 9, tzinfo=timezone.utc).timestamp()
    os.utime(tmp_path / "run_20260928.log", (oldest, oldest))
    os.utime(tmp_path / "run_20261008.log", (newest, newest))
    tool = ListFilesTool(workspace_root=str(tmp_path))
    result = query(tool, extensions=["LOG", ".log"], summary=True, limit=1)
    info = result.execution_evidence["file_query"]
    assert info == metadata(result.text)
    assert info["matched"] == info["files"] == 2
    assert info["directories"] == info["shown"] == 0
    assert info["extension_counts"] == {".log": 2}
    assert not info["entry_limit_truncated"] and info["scan_complete"]
    assert info["modified_utc"]["min"].startswith("2026-10-01")
    assert info["modified_utc"]["max"].startswith("2026-10-09")
    assert "run_20260928.log" not in result.text
    caches = query(tool, extensions=[".pyc", ".tmp"], summary=True)
    assert metadata(caches.text)["extension_counts"] == {".tmp": 1, ".pyc": 1}
    summary = build_task_execution_summary([{
        "name": "list_files", "status": "success", "arguments": {"extensions": [".log"]},
        "observation": result.text, "execution_evidence": result.execution_evidence,
    }], {}, "completed")
    assert "2026-10-09" in summary and '".log": 2' in summary


@pytest.mark.parametrize("params", [
    {"extensions": []}, {"extensions": "log"}, {"extensions": ["*.log"]},
    {"extensions": ["../log"]}, {"extensions": [".log"], "pattern": "*"},
])
def test_invalid_suffix_filter_is_not_a_successful_empty_result(tmp_path, params):
    assert query(ListFilesTool(workspace_root=str(tmp_path)), **params).error_code == "INVALID_ARGUMENT"


def test_traversal_errors_keep_statistics_explicitly_incomplete(tmp_path, monkeypatch):
    def unreadable(root, *, followlinks, onerror):
        onerror(PermissionError(13, "access denied", str(tmp_path / "blocked")))
        return iter([])
    monkeypatch.setattr("app.agent_base.tools.my_tools.foundation_runtime.os.walk", unreadable)
    result = query(ListFilesTool(workspace_root=str(tmp_path)), extensions=[".log"], summary=True)
    assert result.error_code == "FILE_LIST_INCOMPLETE"
    assert not metadata(result.text)["scan_complete"]
    assert "(no matches)" not in result.text
    assert result.execution_evidence["scan_errors"][0]["path"].endswith("blocked")
