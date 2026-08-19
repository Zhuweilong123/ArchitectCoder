"""文件系统原语工具测试（safe_path + read/write/edit/glob/bash）。"""
import asyncio
from pathlib import Path

import pytest

from app.agent_base.tools.my_tools.file_system_tools import (
    safe_path, create_file_system_tools,
)


def _run(tool, params):
    return asyncio.run(tool._execute(params))


@pytest.fixture
def workspace(tmp_path):
    src = tmp_path / "src"
    test = tmp_path / "tests"
    src.mkdir()
    test.mkdir()
    (src / "a.py").write_text("line0\nline1\nline2\nline3\n", encoding="utf-8")
    (test / "test_a.py").write_text("def test():\n    pass\n", encoding="utf-8")
    return src, test


def _tools(workspace):
    src, test = workspace
    return create_file_system_tools(str(src), str(test))


def _tool_by_name(tools, name):
    return next(t for t in tools if t.name == name)


# ── safe_path ──────────────────────────────────────────

def test_safe_path_blocks_escape(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    roots = [str(src)]

    # 绝对路径在 root 外 → 拒绝
    with pytest.raises(ValueError):
        safe_path(str(outside), roots)
    # 相对路径 .. 逃逸 → 拒绝
    with pytest.raises(ValueError):
        safe_path("../outside.txt", roots)
    # 合法相对路径 → 通过
    assert safe_path("a.py", roots) == (src / "a.py").resolve()


def test_safe_path_allows_absolute_in_second_root(workspace):
    src, test = workspace
    roots = [str(src), str(test)]
    # 绝对路径落在第二个 root（test_dir）内 → 通过
    assert safe_path(str(test / "test_a.py"), roots) == (test / "test_a.py").resolve()


# ── read_file ──────────────────────────────────────────

def test_read_file(workspace):
    read = _tool_by_name(_tools(workspace), "read_file")
    assert _run(read, {"path": "a.py"}) == "line0\nline1\nline2\nline3"
    assert _run(read, {"path": "a.py", "offset": 1, "limit": 3}) == "line1\nline2\nline3"
    # 截断时带剩余行提示
    assert "more lines" in _run(read, {"path": "a.py", "offset": 1, "limit": 2})
    assert "not found" in _run(read, {"path": "missing.py"})


# ── write_file / edit_file ─────────────────────────────

def test_write_and_edit_file(workspace):
    src, _ = workspace
    tools = _tools(workspace)
    write = _tool_by_name(tools, "write_file")
    edit = _tool_by_name(tools, "edit_file")
    read = _tool_by_name(tools, "read_file")

    assert "Wrote" in _run(write, {"path": "b.py", "content": "x = 1\n"})
    assert (src / "b.py").read_text(encoding="utf-8") == "x = 1\n"

    assert "Edited" in _run(edit, {"path": "b.py", "old_text": "x = 1", "new_text": "x = 2"})
    assert (src / "b.py").read_text(encoding="utf-8") == "x = 2\n"

    # old_text 未命中 → 错误
    assert "not found" in _run(edit, {"path": "b.py", "old_text": "nope", "new_text": "y"})


def test_write_file_blocks_escape(workspace):
    write = _tool_by_name(_tools(workspace), "write_file")
    result = _run(write, {"path": "../escape.py", "content": "bad"})
    assert "escapes workspace" in result


# ── glob ───────────────────────────────────────────────

def test_glob(workspace):
    gl = _tool_by_name(_tools(workspace), "glob")
    result = _run(gl, {"pattern": "*.py"})
    assert "a.py" in result
    assert "test_a.py" in result


# ── bash ───────────────────────────────────────────────

def test_bash_echo(workspace):
    bash = _tool_by_name(_tools(workspace), "bash")
    assert _run(bash, {"command": "echo hello"}).strip() == "hello"


def test_bash_deny_list(workspace):
    bash = _tool_by_name(_tools(workspace), "bash")
    result = _run(bash, {"command": "sudo ls"})
    assert "denied" in result


def test_bash_empty_command(workspace):
    bash = _tool_by_name(_tools(workspace), "bash")
    assert "non-empty" in _run(bash, {"command": ""})
