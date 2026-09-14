import json
from pathlib import Path

from app.runtime.task_contracts import ApprovalClass, NetworkPolicy, TaskKind
from app.runtime.task_contracts import TaskKind, TaskSpec, ToolchainProfile
from app.runtime.task_resolver import CallableTaskAdapter, TaskResolution, TaskResolver


def test_resolves_cpp_cmake_tasks_without_language_allowlist(tmp_path):
    (tmp_path / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20)\n", encoding="utf-8")

    result = TaskResolver().resolve("build", str(tmp_path))

    assert result.resolved
    assert result.toolchain.family == "cpp"
    assert result.task.kind is TaskKind.BUILD
    assert result.task.argv == ("cmake", "--build", "build")
    assert result.task.toolchain_id == "cpp-cmake"


def test_resolves_node_script_from_package_json(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"build": "vite build", "test": "vitest run"}}),
        encoding="utf-8",
    )

    result = TaskResolver().resolve("test", str(tmp_path), target="src")

    assert result.resolved
    assert result.task.argv == ("npm", "run", "test", "src")
    assert result.toolchain.family == "node"


def test_resolves_python_tasks_and_build_metadata(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[build-system]\nrequires = ['setuptools']\n[tool.pytest.ini_options]\n", encoding="utf-8",
    )

    test_result = TaskResolver().resolve("test", str(tmp_path))
    build_result = TaskResolver().resolve("build", str(tmp_path))

    assert test_result.task.argv == ("python", "-m", "pytest")
    assert build_result.task.argv == ("python", "-m", "build")


def test_unknown_task_does_not_fall_back_to_unrelated_command(tmp_path):
    (tmp_path / "CMakeLists.txt").write_text("", encoding="utf-8")

    result = TaskResolver().resolve("typecheck", str(tmp_path))

    assert not result.resolved
    assert "no 'typecheck' task" in result.reason


def test_rejects_shell_control_characters_in_manifest_target(tmp_path):
    (tmp_path / "Cargo.toml").write_text("[package]\nname='x'\nversion='0.1.0'\n", encoding="utf-8")

    result = TaskResolver().resolve("build", str(tmp_path), target="x; rm -rf .")

    assert not result.resolved
    assert "shell control" in result.reason


def test_explicit_manifest_controls_task_policy_and_toolchain(tmp_path):
    marker = tmp_path / ".architectcoder"
    marker.mkdir()
    (marker / "tasks.json").write_text(
        json.dumps({
            "language": "cpp",
            "toolchain": {"id": "cpp-clang", "version": "18"},
            "tasks": {
                "build": {
                    "argv": ["cmake", "--build", "build"],
                    "cwd": "workspace",
                    "network": "deny",
                    "approval": "sandbox_auto",
                    "resources": {"timeout_seconds": 120, "memory_mb": 2048},
                    "expected_outputs": ["build/bin/app"],
                },
            },
        }),
        encoding="utf-8",
    )

    result = TaskResolver().resolve("build", str(tmp_path))

    assert result.resolved
    assert result.toolchain.toolchain_id == "cpp-clang"
    assert result.toolchain.version == "18"
    assert result.task.toolchain_version == "18"
    assert result.task.network is NetworkPolicy.DENY
    assert result.task.approval is ApprovalClass.SANDBOX_AUTO
    assert result.task.resources.timeout_seconds == 120
    assert result.task.resources.memory_mb == 2048
    assert result.task.expected_outputs == ("build/bin/app",)


def test_invalid_manifest_is_a_typed_resolution_failure(tmp_path):
    marker = tmp_path / ".architectcoder"
    marker.mkdir()
    (marker / "tasks.json").write_text(
        '{"tasks": {"build": {"argv": ["cmake"], "network": "maybe"}}}',
        encoding="utf-8",
    )

    result = TaskResolver().resolve("build", str(tmp_path))

    assert not result.resolved
    assert "invalid task" in result.reason


def test_cpp_vertical_fixture_exposes_cmake_build_and_test_tasks():
    fixture = Path(__file__).parents[1] / "fixtures" / "cpp_vertical_slice"

    build = TaskResolver().resolve("build", str(fixture))
    test = TaskResolver().resolve("test", str(fixture))

    assert build.resolved and build.task.argv[:2] == ("cmake", "--build")
    assert test.resolved and test.task.argv[0] == "ctest"


def test_cmake_presets_resolve_configuration_and_prerequisite_plan(tmp_path):
    (tmp_path / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20)\n", encoding="utf-8")
    (tmp_path / "CMakePresets.json").write_text(json.dumps({
        "version": 2,
        "configurePresets": [{
            "name": "default", "generator": "Ninja",
            "binaryDir": "${sourceDir}/build",
        }],
        "buildPresets": [{"name": "default", "configurePreset": "default"}],
        "testPresets": [{"name": "default", "configurePreset": "default", "configuration": "Debug"}],
    }), encoding="utf-8")

    build = TaskResolver().resolve("build", str(tmp_path))
    test = TaskResolver().resolve("test", str(tmp_path))

    assert build.task.argv == ("cmake", "--build", "--preset", "default")
    assert [item.kind for item in build.prerequisites] == [TaskKind.CONFIGURE]
    assert test.task.argv == ("ctest", "--preset", "default")
    assert [item.kind for item in test.prerequisites] == [TaskKind.CONFIGURE, TaskKind.BUILD]
    assert test.plan.available_profiles == ("default",)


def test_cmake_preset_build_skips_configure_when_cache_exists(tmp_path):
    (tmp_path / "CMakeLists.txt").write_text("", encoding="utf-8")
    (tmp_path / "CMakePresets.json").write_text(json.dumps({
        "version": 2,
        "configurePresets": [{"name": "default", "binaryDir": "${sourceDir}/build"}],
        "buildPresets": [{"name": "default", "configurePreset": "default"}],
    }), encoding="utf-8")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "CMakeCache.txt").write_text("", encoding="utf-8")

    result = TaskResolver().resolve("build", str(tmp_path))

    assert result.resolved
    assert result.prerequisites == ()


def test_cmake_preset_profile_selects_linked_release_variant(tmp_path):
    (tmp_path / "CMakeLists.txt").write_text("", encoding="utf-8")
    (tmp_path / "CMakePresets.json").write_text(json.dumps({
        "version": 2,
        "configurePresets": [
            {"name": "default", "displayName": "Default (Debug)", "binaryDir": "${sourceDir}/build"},
            {"name": "release", "displayName": "Release", "binaryDir": "${sourceDir}/build-release"},
        ],
        "buildPresets": [
            {"name": "default", "configurePreset": "default"},
            {"name": "release", "configurePreset": "release"},
        ],
        "testPresets": [
            {"name": "default", "configurePreset": "default", "configuration": "Debug"},
            {"name": "release", "configurePreset": "release", "configuration": "Release"},
        ],
    }), encoding="utf-8")

    result = TaskResolver().resolve("test", str(tmp_path), profile="release")

    assert result.resolved
    assert result.task.argv == ("ctest", "--preset", "release")
    assert result.plan.profile == "release"
    assert result.plan.available_profiles == ("default", "release")
    assert result.prerequisites[0].argv == ("cmake", "--preset", "release")


def test_cmake_unknown_profile_reports_available_profiles(tmp_path):
    (tmp_path / "CMakeLists.txt").write_text("", encoding="utf-8")
    (tmp_path / "CMakePresets.json").write_text(json.dumps({
        "version": 2,
        "configurePresets": [
            {"name": "default", "displayName": "Debug"},
            {"name": "release", "displayName": "Release"},
        ],
    }), encoding="utf-8")

    result = TaskResolver().resolve("configure", str(tmp_path), profile="asan")

    assert not result.resolved
    assert result.available_profiles == ("default", "release")


def test_custom_project_adapter_can_be_registered_without_core_changes(tmp_path):
    marker = tmp_path / "custom.build"
    marker.write_text("", encoding="utf-8")

    def resolve(_resolver, root, task, target):
        if task != "assemble":
            return TaskResolution(project_root=str(root), reason="custom task missing")
        return TaskResolution(
            task=TaskSpec(
                task_id="custom.assemble", kind=TaskKind.CUSTOM,
                argv=("custom-builder", "assemble"),
            ),
            toolchain=ToolchainProfile(
                toolchain_id="custom-builder", family="custom",
            ),
            project_root=str(root),
        )

    # Detection/registration is intentionally exercised independently of the
    # built-in language adapters; a real plugin can return its own TaskSpec.
    adapter = CallableTaskAdapter("custom", lambda root: (root / "custom.build").is_file(), resolve)
    resolver = TaskResolver((adapter,))
    result = resolver.resolve("assemble", str(tmp_path))

    assert result.project_root == str(tmp_path)
    assert result.resolved
    assert result.task.argv == ("custom-builder", "assemble")


def test_resolves_go_module_tasks(tmp_path):
    (tmp_path / "go.mod").write_text("module example.test\n\ngo 1.22\n", encoding="utf-8")

    result = TaskResolver().resolve("test", str(tmp_path))

    assert result.resolved
    assert result.toolchain.family == "go"
    assert result.task.argv == ("go", "test", "./...")


def test_resolves_maven_and_gradle_tasks(tmp_path):
    (tmp_path / "pom.xml").write_text("<project></project>", encoding="utf-8")
    maven = TaskResolver().resolve("build", str(tmp_path))
    assert maven.task.argv == ("mvn", "-B", "package", "-DskipTests")

    (tmp_path / "pom.xml").unlink()
    (tmp_path / "build.gradle").write_text("plugins {}", encoding="utf-8")
    gradle = TaskResolver().resolve("test", str(tmp_path))
    assert gradle.toolchain.family == "java"
    assert gradle.task.argv == ("gradle", "test")


def test_resolves_dotnet_project_tasks(tmp_path):
    (tmp_path / "sample.csproj").write_text("<Project />", encoding="utf-8")

    result = TaskResolver().resolve("test", str(tmp_path))

    assert result.resolved
    assert result.toolchain.family == "dotnet"
    assert result.task.argv == ("dotnet", "test", "sample.csproj")
