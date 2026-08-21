"""文件系统原语工具测试（safe_path + read/write/edit/glob/bash）。"""
import asyncio
import json
from pathlib import Path

import pytest

from app.agent_base.tools.review import ReviewManager
from app.agent_base.tools.my_tools.file_system_tools import (
    BashTool, safe_path, create_file_system_tools, _decode_output,
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
    result = _run(bash, {"command": "format c:"})
    assert "denied" in result
    assert "high-risk" in result


def test_bash_empty_command(workspace):
    bash = _tool_by_name(_tools(workspace), "bash")
    assert "non-empty" in _run(bash, {"command": ""})


# ── bash 敏感命令审核（两级防护） ────────────────────────

class _FakeProgress:
    """收集 emit 的审核事件，替代 ProgressRelay。"""

    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


def _bash_with_review(workspace, review_timeout=5):
    src, test = workspace
    mgr = ReviewManager()
    progress = _FakeProgress()
    bash = BashTool(str(src), str(test), review_manager=mgr,
                    progress=progress, review_timeout=review_timeout)
    return bash, mgr, progress


# 含敏感子串但实际无害的命令（批准时只执行 echo）
SENSITIVE_CMD = 'echo "git reset --hard is risky"'


def test_bash_sensitive_no_channel_denied(workspace):
    """敏感命令 + 无审核通道 → fail closed 拒绝。"""
    bash = _tool_by_name(_tools(workspace), "bash")
    result = _run(bash, {"command": SENSITIVE_CMD})
    assert "requires human approval" in result
    assert "NOT executed" in result


def test_bash_sensitive_accept_executes(workspace):
    """敏感命令 + 用户批准 → 正常执行。"""
    bash, mgr, progress = _bash_with_review(workspace)

    async def _scenario():
        task = asyncio.create_task(bash._execute({"command": SENSITIVE_CMD}))
        await asyncio.sleep(0.05)  # 让 submit + emit 完成
        mgr.resolve(0, json.dumps({"decision": "accept", "feedback": ""}))
        return await task

    result = asyncio.run(_scenario())
    assert "git reset --hard is risky" in result  # echo 真的执行了
    # 审核事件已推送（前端据此弹卡）
    assert any(e.get("event") == "review" and e.get("review_type") == "bash_command"
               for e in progress.events)


def test_bash_sensitive_reject_blocked(workspace):
    """敏感命令 + 用户拒绝 → 不执行，反馈喂回 agent。"""
    bash, mgr, _ = _bash_with_review(workspace)

    async def _scenario():
        task = asyncio.create_task(bash._execute({"command": SENSITIVE_CMD}))
        await asyncio.sleep(0.05)
        mgr.resolve(0, json.dumps({"decision": "reject", "feedback": "太危险"}))
        return await task

    result = asyncio.run(_scenario())
    assert "rejected by user" in result
    assert "太危险" in result
    assert "NOT executed" in result


def test_bash_sensitive_timeout_denied(workspace):
    """敏感命令 + 审核超时 → fail closed 拒绝，并推送 review_timeout。"""
    bash, mgr, progress = _bash_with_review(workspace, review_timeout=0.1)
    result = _run(bash, {"command": SENSITIVE_CMD})
    assert "timed out" in result
    assert "NOT executed" in result
    assert any(e.get("event") == "review_timeout" for e in progress.events)


def test_bash_high_risk_denied_even_with_channel(workspace):
    """高危命令即使有审核通道也直接拒绝，不产生审核请求。"""
    bash, mgr, progress = _bash_with_review(workspace)
    result = _run(bash, {"command": "diskpart"})
    assert "denied" in result
    assert "high-risk" in result
    assert not mgr.has_pending()
    assert not progress.events


def test_decode_output_gbk_fallback():
    # GBK 编码的中文（cmd.exe 错误信息）→ UTF-8 解码失败后回退 locale 解码
    assert _decode_output("不是内部或外部命令".encode("gbk")) == "不是内部或外部命令"
    # UTF-8 正常解码
    assert _decode_output("你好".encode("utf-8")) == "你好"
    # 纯 ASCII 两种编码一致
    assert _decode_output(b"hello") == "hello"


def test_read_file_allows_design_dir(tmp_path):
    src = tmp_path / "src"
    design = tmp_path / "uml"
    src.mkdir()
    design.mkdir()
    (design / "proj.umlproj").write_text("diagram", encoding="utf-8")

    tools = create_file_system_tools(str(src), "", str(design))
    read = next(t for t in tools if t.name == "read_file")

    # design_dir 内的绝对路径文件可读（不再被 workspace 守卫拒绝）
    assert "diagram" in _run(read, {"path": str(design / "proj.umlproj")})


def test_read_file_relative_tries_all_roots(tmp_path):
    src = tmp_path / "src"
    test = tmp_path / "tests"
    src.mkdir()
    test.mkdir()
    # 文件在 test 目录（第二个 root），不在 source（第一个 root）
    (test / "test_a.py").write_text("in test", encoding="utf-8")

    tools = create_file_system_tools(str(src), str(test))
    read = next(t for t in tools if t.name == "read_file")

    # 相对路径按顺序尝试所有 root，能在 test_dir 找到
    assert "in test" in _run(read, {"path": "test_a.py"})
