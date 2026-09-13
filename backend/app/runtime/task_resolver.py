"""Resolve semantic project tasks without a language executable allowlist.

The resolver only discovers task intent and produces literal argv.  It does
not start processes or decide whether a host executor/sandbox may run them.
That decision remains the execution broker's responsibility.
"""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from app.runtime.task_contracts import (
    ApprovalClass,
    NetworkPolicy,
    ResourceLimits,
    TaskKind,
    TaskSpec,
    ToolchainProfile,
)


@dataclass(frozen=True, slots=True)
class TaskResolution:
    """Result of resolving one semantic task for a project."""

    task: TaskSpec | None = None
    toolchain: ToolchainProfile | None = None
    project_root: str = ""
    reason: str = ""

    @property
    def resolved(self) -> bool:
        return self.task is not None and self.toolchain is not None


class ProjectTaskAdapter(Protocol):
    """Plugin boundary for one project/build metadata format."""

    name: str

    def detect(self, root: Path) -> bool: ...

    def resolve(
        self, resolver: "TaskResolver", root: Path, task: str, target: str | None,
    ) -> TaskResolution: ...


@dataclass(frozen=True, slots=True)
class CallableTaskAdapter:
    """Small adapter bridge used by built-in and third-party resolvers."""

    name: str
    detector: Callable[[Path], bool]
    handler: Callable[["TaskResolver", Path, str, str | None], TaskResolution]

    def detect(self, root: Path) -> bool:
        return bool(self.detector(root))

    def resolve(
        self, resolver: "TaskResolver", root: Path, task: str, target: str | None,
    ) -> TaskResolution:
        return self.handler(resolver, root, task, target)


class TaskResolver:
    """Resolve tasks through an injectable project-adapter registry."""

    def __init__(self, adapters: tuple[ProjectTaskAdapter, ...] | None = None) -> None:
        self._adapters = tuple(adapters or self._built_in_adapters())

    def register(self, adapter: ProjectTaskAdapter) -> None:
        """Register an adapter for callers that own a resolver instance.

        Registration is intentionally instance-local: a plugin cannot mutate
        another conversation's task policy or the process-wide defaults.
        """
        self._adapters = (*self._adapters, adapter)

    def _built_in_adapters(self) -> tuple[ProjectTaskAdapter, ...]:
        return (
            CallableTaskAdapter(
                "architectcoder-manifest",
                lambda root: (root / ".architectcoder").exists(),
                lambda owner, root, task, target: owner._from_architectcoder_manifest(root, task, target),
            ),
            CallableTaskAdapter(
                "node-package",
                lambda root: (root / "package.json").is_file(),
                lambda owner, root, task, target: owner._from_package_json(root, task, target),
            ),
            CallableTaskAdapter(
                "python-project",
                lambda root: (root / "pyproject.toml").is_file(),
                lambda owner, root, task, target: owner._from_pyproject(root, task, target),
            ),
            CallableTaskAdapter(
                "cpp-cmake",
                lambda root: (root / "CMakeLists.txt").is_file(),
                lambda owner, root, task, target: owner._from_cmake(root, task, target),
            ),
            CallableTaskAdapter(
                "rust-cargo",
                lambda root: (root / "Cargo.toml").is_file(),
                lambda owner, root, task, target: owner._from_cargo(root, task, target),
            ),
            CallableTaskAdapter(
                "go-module",
                lambda root: (root / "go.mod").is_file(),
                lambda owner, root, task, target: owner._from_go(root, task, target),
            ),
            CallableTaskAdapter(
                "maven",
                lambda root: (root / "pom.xml").is_file(),
                lambda owner, root, task, target: owner._from_maven(root, task, target),
            ),
            CallableTaskAdapter(
                "gradle",
                lambda root: any((root / marker).is_file() for marker in (
                    "build.gradle", "build.gradle.kts", "gradlew", "gradlew.bat",
                )),
                lambda owner, root, task, target: owner._from_gradle(root, task, target),
            ),
            CallableTaskAdapter(
                "dotnet",
                lambda root: bool(tuple(root.glob("*.sln")) or tuple(root.glob("*.csproj"))),
                lambda owner, root, task, target: owner._from_dotnet(root, task, target),
            ),
        )

    def resolve(
        self,
        task: str,
        project_root: str,
        *,
        target: str | None = None,
    ) -> TaskResolution:
        task_name = str(task or "").strip().lower()
        if not task_name:
            return TaskResolution(reason="task must not be empty")
        root = self._find_project_root(project_root)
        if root is None:
            return TaskResolution(reason="no supported project manifest found")

        adapter = next((item for item in self._adapters if item.detect(root)), None)
        if adapter is None:  # pragma: no cover - _find_project_root is exhaustive
            return TaskResolution(project_root=str(root), reason="no supported project manifest found")
        result = adapter.resolve(self, root, task_name, target)
        if result.project_root:
            return result
        return TaskResolution(
            task=result.task,
            toolchain=result.toolchain,
            project_root=str(root),
            reason=result.reason,
        )

    def _find_project_root(self, path: str) -> Path | None:
        candidate = Path(path).expanduser()
        if not candidate.exists():
            return None
        if candidate.is_file():
            candidate = candidate.parent
        candidate = candidate.resolve()
        for directory in (candidate, *candidate.parents):
            if any(adapter.detect(directory) for adapter in self._adapters):
                return directory
        return None

    @staticmethod
    def _target_args(args: tuple[str, ...], target: str | None) -> tuple[str, ...]:
        if target is None or not str(target).strip():
            return args
        value = str(target).strip()
        if any(char in value for char in "\r\n;|><`$"):
            return args + ("__invalid_target__",)
        return args + (value,)

    @staticmethod
    def _make(
        root: Path,
        *,
        task_id: str,
        kind: TaskKind,
        argv: tuple[str, ...],
        toolchain: ToolchainProfile,
        target: str | None,
        source: str,
        cwd: str = "workspace",
        network: NetworkPolicy = NetworkPolicy.DENY,
        approval: ApprovalClass = ApprovalClass.SANDBOX_AUTO,
        resources: ResourceLimits | None = None,
        expected_outputs: tuple[str, ...] = (),
    ) -> TaskResolution:
        if target and any(char in str(target) for char in "\r\n;|><`$"):
            return TaskResolution(project_root=str(root), reason="target contains shell control characters")
        task = TaskSpec(
            task_id=task_id,
            kind=kind,
            argv=TaskResolver._target_args(argv, target),
            cwd=cwd,
            toolchain_id=toolchain.toolchain_id,
            toolchain_version=toolchain.version,
            network=network,
            approval=approval,
            resources=resources or ResourceLimits(),
            expected_outputs=expected_outputs,
            source=source,
        )
        return TaskResolution(task=task, toolchain=toolchain, project_root=str(root))

    def _from_package_json(self, root: Path, task: str, target: str | None) -> TaskResolution:
        try:
            data = json.loads((root / "package.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            return TaskResolution(project_root=str(root), reason=f"invalid package.json: {exc}")
        scripts = data.get("scripts") if isinstance(data, dict) else None
        if not isinstance(scripts, dict):
            return TaskResolution(project_root=str(root), reason="package.json has no scripts object")
        script = task if task in scripts else {"run": "start"}.get(task)
        if not script or script not in scripts:
            return TaskResolution(project_root=str(root), reason=f"package.json has no '{task}' script")
        toolchain = ToolchainProfile(
            toolchain_id="node-package-manager",
            family="node",
            executor="project-toolchain",
            capabilities=tuple(str(name) for name in scripts),
        )
        return self._make(
            root, task_id=f"node.{script}", kind=TaskKind(task) if task in TaskKind._value2member_map_ else TaskKind.CUSTOM,
            argv=("npm", "run", script), toolchain=toolchain, target=target, source="package.json",
        )

    def _from_pyproject(self, root: Path, task: str, target: str | None) -> TaskResolution:
        try:
            data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
            return TaskResolution(project_root=str(root), reason=f"invalid pyproject.toml: {exc}")
        commands = {
            "test": ("python", "-m", "pytest"),
            "lint": ("ruff", "check"),
            "format": ("ruff", "format"),
            "typecheck": ("mypy",),
        }
        if task == "build" and isinstance(data.get("build-system"), dict):
            commands["build"] = ("python", "-m", "build")
        argv = commands.get(task)
        if argv is None:
            return TaskResolution(project_root=str(root), reason=f"pyproject.toml has no '{task}' task")
        toolchain = ToolchainProfile(
            toolchain_id="python-project",
            family="python",
            executor="project-toolchain",
            capabilities=tuple(commands),
        )
        return self._make(
            root, task_id=f"python.{task}", kind=TaskKind(task), argv=argv,
            toolchain=toolchain, target=target, source="pyproject.toml",
        )

    def _from_cmake(self, root: Path, task: str, target: str | None) -> TaskResolution:
        commands: dict[str, tuple[str, ...]] = {
            "configure": ("cmake", "-S", ".", "-B", "build"),
            "build": ("cmake", "--build", "build"),
            "test": ("ctest", "--test-dir", "build", "--output-on-failure"),
        }
        argv = commands.get(task)
        if argv is None:
            return TaskResolution(project_root=str(root), reason=f"CMake project has no '{task}' task")
        toolchain = ToolchainProfile(
            toolchain_id="cpp-cmake",
            family="cpp",
            executor="project-toolchain",
            capabilities=tuple(commands),
        )
        return self._make(
            root, task_id=f"cpp.{task}", kind=TaskKind(task), argv=argv,
            toolchain=toolchain, target=target, source="CMakeLists.txt",
        )

    def _from_cargo(self, root: Path, task: str, target: str | None) -> TaskResolution:
        commands: dict[str, tuple[str, ...]] = {
            "build": ("cargo", "build"),
            "test": ("cargo", "test"),
            "check": ("cargo", "check"),
            "lint": ("cargo", "clippy"),
            "format": ("cargo", "fmt", "--", "--check"),
        }
        argv = commands.get(task)
        if argv is None:
            return TaskResolution(project_root=str(root), reason=f"Cargo project has no '{task}' task")
        toolchain = ToolchainProfile(
            toolchain_id="rust-cargo",
            family="rust",
            executor="project-toolchain",
            capabilities=tuple(commands),
        )
        return self._make(
            root, task_id=f"rust.{task}", kind=TaskKind.CUSTOM if task == "check" else TaskKind(task),
            argv=argv, toolchain=toolchain, target=target, source="Cargo.toml",
        )

    def _from_go(self, root: Path, task: str, target: str | None) -> TaskResolution:
        commands = {
            "build": ("go", "build", "./..."),
            "test": ("go", "test", "./..."),
            "lint": ("go", "vet", "./..."),
            "format": ("go", "fmt", "./..."),
        }
        argv = commands.get(task)
        if argv is None:
            return TaskResolution(project_root=str(root), reason=f"go.mod has no '{task}' task")
        toolchain = ToolchainProfile(
            toolchain_id="go-module", family="go", executor="project-toolchain",
            capabilities=tuple(commands),
        )
        return self._make(
            root, task_id=f"go.{task}", kind=TaskKind(task), argv=argv,
            toolchain=toolchain, target=target, source="go.mod",
        )

    def _from_maven(self, root: Path, task: str, target: str | None) -> TaskResolution:
        commands = {
            "build": ("mvn", "-B", "package", "-DskipTests"),
            "test": ("mvn", "-B", "test"),
            "verify": ("mvn", "-B", "verify"),
        }
        argv = commands.get(task)
        if argv is None:
            return TaskResolution(project_root=str(root), reason=f"pom.xml has no '{task}' task")
        toolchain = ToolchainProfile(
            toolchain_id="java-maven", family="java", executor="project-toolchain",
            capabilities=tuple(commands),
        )
        return self._make(
            root, task_id=f"maven.{task}", kind=TaskKind.CUSTOM if task == "verify" else TaskKind(task),
            argv=argv, toolchain=toolchain, target=target, source="pom.xml",
        )

    def _from_gradle(self, root: Path, task: str, target: str | None) -> TaskResolution:
        wrapper = (
            "./gradlew" if (root / "gradlew").is_file() and os.name != "nt"
            else "gradlew.bat" if (root / "gradlew.bat").is_file() and os.name == "nt"
            else "gradle"
        )
        commands = {
            "build": (wrapper, "build"),
            "test": (wrapper, "test"),
            "lint": (wrapper, "check"),
            "format": (wrapper, "spotlessCheck"),
        }
        argv = commands.get(task)
        if argv is None:
            return TaskResolution(project_root=str(root), reason=f"Gradle project has no '{task}' task")
        toolchain = ToolchainProfile(
            toolchain_id="java-gradle", family="java", executor="project-toolchain",
            capabilities=tuple(commands),
        )
        return self._make(
            root, task_id=f"gradle.{task}", kind=TaskKind(task), argv=argv,
            toolchain=toolchain, target=target, source="gradle-build",
        )

    def _from_dotnet(self, root: Path, task: str, target: str | None) -> TaskResolution:
        project = next(iter(sorted((*root.glob("*.sln"), *root.glob("*.csproj")))), None)
        project_arg = (str(project.name),) if project is not None else ()
        commands = {
            "build": ("dotnet", "build", *project_arg),
            "test": ("dotnet", "test", *project_arg),
            "format": ("dotnet", "format", "--verify-no-changes", *project_arg),
        }
        argv = commands.get(task)
        if argv is None:
            return TaskResolution(project_root=str(root), reason=f".NET project has no '{task}' task")
        toolchain = ToolchainProfile(
            toolchain_id="dotnet-project", family="dotnet", executor="project-toolchain",
            capabilities=tuple(commands),
        )
        return self._make(
            root, task_id=f"dotnet.{task}", kind=TaskKind(task), argv=argv,
            toolchain=toolchain, target=target, source="dotnet-project",
        )

    def _from_architectcoder_manifest(self, root: Path, task: str, target: str | None) -> TaskResolution:
        """Resolve the explicit project task manifest.

        The manifest is declarative: argv is literal and all policy fields are
        parsed into the same contracts used by auto-detected adapters.  It is
        still untrusted input; the execution broker must enforce the policy.
        """
        manifest = root / ".architectcoder"
        if manifest.is_dir():
            manifest = manifest / "tasks.json"
        if not manifest.is_file():
            return TaskResolution(project_root=str(root), reason=".architectcoder marker has no tasks.json")
        try:
            data: Any = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            return TaskResolution(project_root=str(root), reason=f"invalid tasks.json: {exc}")
        entry = data.get("tasks", {}).get(task) if isinstance(data, dict) else None
        if not isinstance(entry, dict) or not isinstance(entry.get("argv"), list):
            return TaskResolution(project_root=str(root), reason=f"tasks.json has no literal argv for '{task}'")
        try:
            kind = TaskKind(task) if task in TaskKind._value2member_map_ else TaskKind.CUSTOM
            language = str(data.get("language", "unknown"))
            toolchain_value = data.get("toolchain", "project")
            toolchain_id = (
                str(toolchain_value.get("id", "project"))
                if isinstance(toolchain_value, dict)
                else str(toolchain_value)
            )
            version = (
                str(toolchain_value.get("version", "unknown"))
                if isinstance(toolchain_value, dict)
                else "unknown"
            )
            toolchain = ToolchainProfile(
                toolchain_id=toolchain_id,
                family=language,
                version=version,
                executor="project-toolchain",
            )
            network = NetworkPolicy(str(entry.get("network", "deny")))
            approval = ApprovalClass(str(entry.get("approval", "sandbox_auto")))
            raw_resources = entry.get("resources", {})
            if not isinstance(raw_resources, dict):
                raise ValueError("resources must be an object")
            resources = ResourceLimits(
                timeout_seconds=float(raw_resources.get("timeout_seconds", 600.0)),
                cpu_seconds=(
                    float(raw_resources["cpu_seconds"])
                    if raw_resources.get("cpu_seconds") is not None else None
                ),
                memory_mb=(
                    int(raw_resources["memory_mb"])
                    if raw_resources.get("memory_mb") is not None else None
                ),
                disk_mb=(
                    int(raw_resources["disk_mb"])
                    if raw_resources.get("disk_mb") is not None else None
                ),
                max_processes=(
                    int(raw_resources["max_processes"])
                    if raw_resources.get("max_processes") is not None else None
                ),
            )
            expected_outputs = entry.get("expected_outputs", [])
            if not isinstance(expected_outputs, list):
                raise ValueError("expected_outputs must be an array")
            return self._make(
                root, task_id=f"manifest.{task}", kind=kind,
                argv=tuple(str(value) for value in entry["argv"]),
                toolchain=toolchain, target=target, source=str(manifest.relative_to(root)),
                cwd=str(entry.get("cwd", "workspace")), network=network,
                approval=approval, resources=resources,
                expected_outputs=tuple(str(value) for value in expected_outputs),
            )
        except (TypeError, ValueError) as exc:
            return TaskResolution(project_root=str(root), reason=f"invalid task '{task}': {exc}")


__all__ = ["CallableTaskAdapter", "ProjectTaskAdapter", "TaskResolution", "TaskResolver"]
