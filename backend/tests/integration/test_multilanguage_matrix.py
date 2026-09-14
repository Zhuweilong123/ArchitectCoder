"""M6.1 compatibility matrix for language-neutral task resolution."""

import json

import pytest

from app.runtime.task_contracts import NetworkPolicy, TaskKind
from app.runtime.task_resolver import TaskResolver


@pytest.mark.parametrize(
    ("marker", "task", "family", "program"),
    [
        ("package.json", "test", "node", "npm"),
        ("pyproject.toml", "test", "python", "python"),
        ("CMakeLists.txt", "build", "cpp", "cmake"),
        ("Cargo.toml", "test", "rust", "cargo"),
        ("go.mod", "test", "go", "go"),
        ("pom.xml", "test", "java", "mvn"),
        ("build.gradle", "test", "java", "gradle"),
        ("sample.csproj", "test", "dotnet", "dotnet"),
    ],
)
def test_standard_project_matrix_resolves_semantic_task(
    tmp_path, marker, task, family, program,
):
    contents = {
        "package.json": json.dumps({"scripts": {"test": "test"}}),
        "pyproject.toml": "[tool.pytest.ini_options]\n",
        "CMakeLists.txt": "cmake_minimum_required(VERSION 3.20)\n",
        "Cargo.toml": "[package]\nname='matrix'\nversion='0.1.0'\n",
        "go.mod": "module matrix.test\n\ngo 1.22\n",
        "pom.xml": "<project></project>",
        "build.gradle": "plugins {}",
        "sample.csproj": "<Project />",
    }
    (tmp_path / marker).write_text(contents[marker], encoding="utf-8")

    result = TaskResolver().resolve(task, str(tmp_path))

    assert result.resolved
    assert result.toolchain.family == family
    assert result.task.kind is TaskKind(task)
    assert result.task.argv[0] == program
    assert result.task.network is NetworkPolicy.DENY
    assert result.task.toolchain_id == result.toolchain.toolchain_id
    assert result.task.toolchain_version == result.toolchain.version


def test_custom_manifest_matrix_preserves_declared_toolchain_and_policy(tmp_path):
    marker = tmp_path / ".architectcoder"
    marker.mkdir()
    (marker / "tasks.json").write_text(
        json.dumps({
            "language": "zig",
            "toolchain": {"id": "zig", "version": "0.13"},
            "tasks": {
                "check-all": {
                    "argv": ["zig", "build", "test"],
                    "network": "deny",
                    "resources": {"timeout_seconds": 30},
                },
            },
        }),
        encoding="utf-8",
    )

    result = TaskResolver().resolve("check-all", str(tmp_path))

    assert result.resolved
    assert result.task.kind is TaskKind.CUSTOM
    assert result.task.argv == ("zig", "build", "test")
    assert result.task.toolchain_id == "zig"
    assert result.task.toolchain_version == "0.13"
    assert result.task.resources.timeout_seconds == 30
