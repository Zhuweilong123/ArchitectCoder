"""Regression checks for boundaries that prevent dependency cycles."""

from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
        elif isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
    return result


def test_trace_reader_does_not_depend_on_jsonl_writer():
    imports = _imports(REPO_ROOT / "extensions" / "trace" / "trace_reader.py")

    assert "extensions.trace.chat_trace" not in imports
    assert "extensions.trace.format" in imports


def test_performance_view_does_not_depend_on_batch_manager():
    imports = _imports(REPO_ROOT / "extensions" / "evals" / "performance.py")

    assert "extensions.evals.batches" not in imports
    assert "summary" in imports


def test_knowledge_graph_tools_do_not_depend_on_concrete_provider():
    imports = _imports(REPO_ROOT / "extensions" / "knowledge_graph" / "tools.py")

    assert "extensions.knowledge_graph.provider" not in imports


def test_plugin_provider_defaults_have_one_owner():
    settings_source = (REPO_ROOT / "backend" / "config" / "settings.py").read_text(encoding="utf-8")
    manager_source = (REPO_ROOT / "backend" / "app" / "agent_base" / "core" / "plugins.py").read_text(encoding="utf-8")

    assert "extensions.trace:create" not in settings_source
    assert "extensions.trace:create" not in manager_source
    assert "plugin_defaults" in settings_source
    assert "plugin_defaults" in manager_source
