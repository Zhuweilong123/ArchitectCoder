"""Language-neutral source adapters for contract and graph fact extraction.

Execution is intentionally out of scope here.  Adapters turn source files
into the existing :class:`ArtifactFacts` contract, while toolchain selection
and process execution remain owned by the runtime resolver/broker.
"""

from __future__ import annotations

import ast
import asyncio
import json
import shlex
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from app.runtime.task_contracts import ExecutionPolicy, NetworkPolicy, ResourceLimits, TaskKind, TaskSpec

from .contracts import ArtifactFacts, ContractEntity


class LanguageAdapter(Protocol):
    """Extract normalized facts from one language family."""

    language: str

    def supports(self, path: str | Path) -> bool: ...

    def extract(
        self,
        path: str | Path,
        *,
        project_id: str = "",
        scope: str = "source",
        project_root: str | Path | None = None,
    ) -> ArtifactFacts: ...


def _diagnostic(message: str, *, code: str = "parse_error") -> dict[str, str]:
    return {"code": code, "message": str(message)}


def _line(node: ast.AST) -> int | None:
    value = getattr(node, "lineno", None)
    return int(value) if isinstance(value, int) else None


class PythonAstAdapter:
    """Extract classes/functions from Python's standard AST."""

    language = "python"

    def supports(self, path: str | Path) -> bool:
        return Path(path).suffix.lower() in {".py", ".pyi"}

    def extract(
        self,
        path: str | Path,
        *,
        project_id: str = "",
        scope: str = "source",
        project_root: str | Path | None = None,
    ) -> ArtifactFacts:
        source_path = Path(path).expanduser().resolve()
        try:
            tree = ast.parse(
                source_path.read_text(encoding="utf-8"),
                filename=str(source_path),
            )
        except (OSError, UnicodeError, SyntaxError) as exc:
            return ArtifactFacts(
                project_id=project_id,
                scope=scope,
                status="failed",
                diagnostics=(_diagnostic(str(exc)),),
                metadata={"language": self.language, "path": str(source_path)},
            )

        entities: list[ContractEntity] = []
        module_id = f"python:module:{source_path}"
        entities.append(ContractEntity(
            entity_id=module_id,
            entity_type="source_module",
            name=source_path.stem,
            path=str(source_path),
            line=1,
            attributes={"language": self.language},
        ))

        def visit(body: list[ast.stmt], parent_id: str = module_id) -> None:
            for node in body:
                if isinstance(node, ast.ClassDef):
                    entity_id = f"python:class:{source_path}:{node.name}:{_line(node) or 0}"
                    entities.append(ContractEntity(
                        entity_id=entity_id,
                        entity_type="source_class",
                        name=node.name,
                        path=str(source_path),
                        line=_line(node),
                        parent_id=parent_id,
                        attributes={"language": self.language},
                    ))
                    visit(node.body, entity_id)
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    entity_id = f"python:function:{source_path}:{node.name}:{_line(node) or 0}"
                    entities.append(ContractEntity(
                        entity_id=entity_id,
                        entity_type="source_method" if parent_id != module_id else "source_function",
                        name=node.name,
                        path=str(source_path),
                        line=_line(node),
                        parent_id=parent_id,
                        attributes={
                            "language": self.language,
                            "async": isinstance(node, ast.AsyncFunctionDef),
                        },
                    ))
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = tuple(alias.name for alias in node.names)
                    entities.append(ContractEntity(
                        entity_id=f"python:import:{source_path}:{_line(node) or 0}:{','.join(names)}",
                        entity_type="source_import",
                        name=", ".join(names),
                        path=str(source_path),
                        line=_line(node),
                        parent_id=parent_id,
                        attributes={"language": self.language},
                    ))

        visit(tree.body)
        return ArtifactFacts(
            project_id=project_id,
            scope=scope,
            status="success",
            entities=tuple(entities),
            metadata={"language": self.language, "path": str(source_path)},
        )


@dataclass(frozen=True)
class CompileCommand:
    directory: Path
    file: Path
    arguments: tuple[str, ...]


class ClangAstAdapter:
    """Extract C++ facts through Clang's JSON AST dump.

    ``compile_commands.json`` is authoritative for include paths, defines,
    language mode, and target flags.  The adapter never invokes a shell and
    accepts an injectable runner so projects can test parsing without Clang
    installed on the host.
    """

    language = "cpp"

    def __init__(
        self,
        *,
        clang_executable: str = "clang++",
        runner: Callable[..., Any] | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.clang_executable = str(clang_executable).strip() or "clang++"
        # A direct host subprocess would bypass the execution Broker.  The
        # caller must inject a runner backed by the selected worker; leaving it
        # unset is an explicit, typed-unavailable state.
        self.runner = runner
        self.timeout_seconds = max(0.1, float(timeout_seconds))

    def supports(self, path: str | Path) -> bool:
        return Path(path).suffix.lower() in {
            ".c", ".cc", ".cp", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx",
        }

    def extract(
        self,
        path: str | Path,
        *,
        project_id: str = "",
        scope: str = "source",
        project_root: str | Path | None = None,
    ) -> ArtifactFacts:
        source_path = Path(path).expanduser().resolve()
        root = Path(project_root).expanduser().resolve() if project_root else source_path.parent
        database = root / "compile_commands.json"
        if not database.is_file():
            return self._failed(
                project_id, scope, source_path,
                "compile_commands.json is required for C++ AST extraction",
                code="compile_database_missing",
            )
        try:
            command = self._find_command(database, source_path)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            return self._failed(project_id, scope, source_path, str(exc), code="compile_database_invalid")
        if command is None:
            return self._failed(
                project_id, scope, source_path,
                f"no compile command found for {source_path}",
                code="compile_command_missing",
            )

        try:
            argv = self._ast_argv(command, source_path)
        except ValueError as exc:
            return self._failed(
                project_id, scope, source_path, str(exc), code="compile_command_invalid",
            )
        if self.runner is None:
            return self._failed(
                project_id, scope, source_path,
                "Clang runner is not configured; inject a Broker-backed runner",
                code="clang_runner_unconfigured",
            )
        try:
            result = self.runner(
                argv,
                cwd=str(command.directory),
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return self._failed(project_id, scope, source_path, str(exc), code="clang_unavailable")
        stdout = result.stdout or b""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if int(getattr(result, "returncode", 1)) != 0:
            stderr = result.stderr or b""
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            return self._failed(
                project_id, scope, source_path,
                str(stderr).strip() or f"clang exited with code {result.returncode}",
                code="clang_parse_failed",
            )
        try:
            document = json.loads(str(stdout))
        except json.JSONDecodeError as exc:
            return self._failed(project_id, scope, source_path, str(exc), code="clang_json_invalid")
        entities = tuple(self._entities(document, source_path))
        return ArtifactFacts(
            project_id=project_id,
            scope=scope,
            status="success",
            entities=entities,
            metadata={
                "language": self.language,
                "path": str(source_path),
                "compile_directory": str(command.directory),
                "compiler": command.arguments[0] if command.arguments else self.clang_executable,
            },
        )

    def _failed(
        self,
        project_id: str,
        scope: str,
        source_path: Path,
        message: str,
        *,
        code: str,
    ) -> ArtifactFacts:
        return ArtifactFacts(
            project_id=project_id,
            scope=scope,
            status="failed",
            diagnostics=(_diagnostic(message, code=code),),
            metadata={"language": self.language, "path": str(source_path)},
        )

    @staticmethod
    def _find_command(database: Path, source_path: Path) -> CompileCommand | None:
        data = json.loads(database.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("compile_commands.json must contain an array")
        target = source_path.resolve()
        parsed: list[CompileCommand] = []
        for entry in data:
            if not isinstance(entry, dict):
                continue
            directory = Path(str(entry.get("directory") or database.parent)).expanduser()
            if not directory.is_absolute():
                directory = (database.parent / directory).resolve()
            raw_file = Path(str(entry.get("file") or ""))
            file_path = raw_file if raw_file.is_absolute() else directory / raw_file
            raw_arguments = entry.get("arguments")
            if isinstance(raw_arguments, list) and all(isinstance(item, str) for item in raw_arguments):
                arguments = tuple(raw_arguments)
            elif isinstance(entry.get("command"), str):
                arguments = tuple(shlex.split(entry["command"], posix=True))
            else:
                raise ValueError("compile command must contain arguments or command")
            if not arguments:
                raise ValueError("compile command arguments must not be empty")
            command = CompileCommand(directory.resolve(), file_path.resolve(), arguments)
            if command.file == target:
                return command
            parsed.append(command)
        # Header units are normally absent from compile_commands.json. Reuse
        # a translation-unit command from the nearest directory so include
        # paths/defines remain authoritative, while _ast_argv replaces the
        # original source argument with the requested header.
        if source_path.suffix.lower() in {".h", ".hh", ".hpp", ".hxx"}:
            candidates = [
                command for command in parsed
                if command.directory == source_path.parent
                or command.directory in source_path.parents
            ]
            if candidates:
                return candidates[0]
        return None

    def _ast_argv(self, command: CompileCommand, source_path: Path) -> list[str]:
        args = list(command.arguments)
        compiler = args[0]
        compiler_name = Path(compiler).name.lower()
        if compiler_name not in {"clang", "clang++", "clang-cl", "clang.exe", "clang++.exe", "clang-cl.exe"}:
            raise ValueError(
                f"compile command compiler must be Clang, got {compiler_name!r}"
            )
        filtered: list[str] = []
        index = 1
        while index < len(args):
            value = args[index]
            if value in {"-c", "--compile"}:
                index += 1
                continue
            if value in {"-o", "--output"}:
                index += 2
                continue
            try:
                candidate = Path(value)
                resolved = (command.directory / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
            except OSError:
                resolved = None
            if resolved in {source_path, command.file}:
                index += 1
                continue
            filtered.append(value)
            index += 1
        return [
            compiler,
            *filtered,
            "-Xclang", "-ast-dump=json",
            "-fsyntax-only",
            str(source_path),
        ]

    @staticmethod
    def _entities(document: Any, source_path: Path) -> list[ContractEntity]:
        entities: list[ContractEntity] = []
        accepted = {
            "NamespaceDecl": "source_namespace",
            "CXXRecordDecl": "source_class",
            "RecordDecl": "source_class",
            "EnumDecl": "source_enum",
            "FunctionDecl": "source_function",
            "CXXMethodDecl": "source_method",
            "FieldDecl": "source_field",
        }

        def walk(node: Any, parent_id: str = "") -> None:
            if not isinstance(node, dict):
                return
            kind = str(node.get("kind") or "")
            name = str(node.get("name") or "")
            implicit = bool(node.get("isImplicit"))
            location = node.get("loc") if isinstance(node.get("loc"), dict) else {}
            line = location.get("line")
            line_number = int(line) if isinstance(line, int) else None
            entity_id = parent_id
            if kind in accepted and name and not implicit:
                entity_id = f"cpp:{accepted[kind]}:{source_path}:{name}:{line_number or 0}"
                entities.append(ContractEntity(
                    entity_id=entity_id,
                    entity_type=accepted[kind],
                    name=name,
                    path=str(source_path),
                    line=line_number,
                    parent_id=parent_id,
                    attributes={"language": "cpp", "clang_kind": kind},
                ))
            for child in node.get("inner", ()) if isinstance(node.get("inner"), list) else ():
                walk(child, entity_id)

        walk(document)
        return entities


def broker_command_runner(broker: Any) -> Callable[..., Any]:
    """Adapt an async execution Broker to Clang's synchronous runner shape.

    The returned callable never invokes ``subprocess`` itself.  It submits a
    ``TaskSpec`` to the broker and converts the structured evidence into a
    ``CompletedProcess``-compatible object for the AST adapter.  If called
    from an active event loop, the coroutine is isolated in a short-lived
    helper thread so synchronous contract collectors remain safe.
    """

    def run(argv: list[str] | tuple[str, ...], *, cwd: str, capture_output=True,
            timeout: float = 60.0, check: bool = False, **_: Any) -> Any:
        task = TaskSpec(
            task_id="clang.ast",
            kind=TaskKind.CUSTOM,
            argv=tuple(argv),
            network=NetworkPolicy.DENY,
            resources=ResourceLimits(timeout_seconds=float(timeout)),
        )
        policy = ExecutionPolicy(
            sandbox=str(getattr(broker, "sandbox_name", "workspace")),
            network=NetworkPolicy.DENY,
        )

        async def execute() -> Any:
            return await broker.execute(task, cwd, policy=policy)

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            evidence = asyncio.run(execute())
        else:
            result_box: list[Any] = []
            error_box: list[BaseException] = []

            def worker() -> None:
                try:
                    result_box.append(asyncio.run(execute()))
                except BaseException as exc:  # propagate to the caller thread
                    error_box.append(exc)

            thread = threading.Thread(target=worker, name="clang-broker-runner")
            thread.start()
            thread.join(max(float(timeout) + 5.0, 5.0))
            if thread.is_alive():
                raise TimeoutError("Broker-backed Clang runner did not return before its deadline")
            if error_box:
                raise error_box[0]
            evidence = result_box[0]
        output = str(getattr(evidence, "output", "") or "")
        success = getattr(evidence, "status", "") == "success"
        return subprocess.CompletedProcess(
            list(argv),
            0 if success else int(getattr(evidence, "exit_code", None) or 1),
            stdout=output.encode("utf-8") if success else b"",
            stderr=b"" if success else output.encode("utf-8"),
        )

    return run


class LanguageAdapterRegistry:
    """Extensible adapter registry; languages are plugins, not an allowlist."""

    def __init__(self, adapters: tuple[LanguageAdapter, ...] = ()) -> None:
        self._adapters: list[LanguageAdapter] = list(adapters)

    def register(self, adapter: LanguageAdapter) -> None:
        self._adapters.append(adapter)

    def adapter_for(self, path: str | Path) -> LanguageAdapter | None:
        return next((adapter for adapter in self._adapters if adapter.supports(path)), None)

    def extract(
        self,
        path: str | Path,
        *,
        project_id: str = "",
        scope: str = "source",
        project_root: str | Path | None = None,
    ) -> ArtifactFacts:
        adapter = self.adapter_for(path)
        if adapter is None:
            return ArtifactFacts(
                project_id=project_id,
                scope=scope,
                status="unsupported",
                diagnostics=(_diagnostic(
                    f"no language adapter registered for {Path(path).suffix or 'this file'}",
                    code="language_adapter_missing",
                ),),
                metadata={"path": str(Path(path).expanduser().resolve())},
            )
        return adapter.extract(
            path,
            project_id=project_id,
            scope=scope,
            project_root=project_root,
        )


def default_language_adapters() -> LanguageAdapterRegistry:
    """Build the built-in registry; callers may register more adapters."""
    return LanguageAdapterRegistry((PythonAstAdapter(), ClangAstAdapter()))


__all__ = [
    "ClangAstAdapter",
    "CompileCommand",
    "LanguageAdapter",
    "LanguageAdapterRegistry",
    "PythonAstAdapter",
    "broker_command_runner",
    "default_language_adapters",
]
