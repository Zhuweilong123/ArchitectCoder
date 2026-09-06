"""评测确定性检查器。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from .models import CheckerResult


def _safe_path(workspace: Path, relative_path: str) -> Path:
    candidate = (workspace / relative_path).resolve()
    if not candidate.is_relative_to(workspace.resolve()):
        raise ValueError(f"path escapes evaluation workspace: {relative_path}")
    return candidate


class Checker:
    name = "checker"

    async def check(self, workspace: Path) -> CheckerResult:
        raise NotImplementedError


class FileExistsChecker(Checker):
    name = "file_exists"

    def __init__(self, path: str):
        self.path = path

    async def check(self, workspace: Path) -> CheckerResult:
        try:
            path = _safe_path(workspace, self.path)
            passed = path.is_file()
            return CheckerResult(checker=self.name, passed=passed,
                                 score=1.0 if passed else 0.0,
                                 message=f"{self.path} {'exists' if passed else 'does not exist'}")
        except ValueError as exc:
            return CheckerResult(checker=self.name, passed=False, message=str(exc))


class FileAbsentChecker(Checker):
    name = "file_absent"

    def __init__(self, path: str):
        self.path = path

    async def check(self, workspace: Path) -> CheckerResult:
        try:
            path = _safe_path(workspace, self.path)
            passed = not path.exists()
            return CheckerResult(checker=self.name, passed=passed,
                                 score=1.0 if passed else 0.0,
                                 message=f"{self.path} {'absent' if passed else 'still exists'}")
        except ValueError as exc:
            return CheckerResult(checker=self.name, passed=False, message=str(exc))


class FileContainsChecker(Checker):
    name = "file_contains"

    def __init__(self, path: str, text: str):
        self.path = path
        self.text = text

    async def check(self, workspace: Path) -> CheckerResult:
        try:
            path = _safe_path(workspace, self.path)
            content = path.read_text(encoding="utf-8")
            passed = self.text in content
            return CheckerResult(checker=self.name, passed=passed,
                                 score=1.0 if passed else 0.0,
                                 message="expected text found" if passed else "expected text not found")
        except (OSError, UnicodeError, ValueError) as exc:
            return CheckerResult(checker=self.name, passed=False, message=str(exc))


class FileNotContainsChecker(Checker):
    name = "file_not_contains"

    def __init__(self, path: str, text: str):
        self.path = path
        self.text = text

    async def check(self, workspace: Path) -> CheckerResult:
        try:
            path = _safe_path(workspace, self.path)
            content = path.read_text(encoding="utf-8")
            passed = self.text not in content
            return CheckerResult(
                checker=self.name,
                passed=passed,
                score=1.0 if passed else 0.0,
                message=("unexpected text absent" if passed else "unexpected text found"),
            )
        except (OSError, UnicodeError, ValueError) as exc:
            return CheckerResult(checker=self.name, passed=False, message=str(exc))


class JsonFieldChecker(Checker):
    name = "json_field"

    def __init__(self, path: str, field: str, expected: Any):
        self.path = path
        self.field = field
        self.expected = expected

    async def check(self, workspace: Path) -> CheckerResult:
        try:
            data = json.loads(_safe_path(workspace, self.path).read_text(encoding="utf-8"))
            actual: Any = data
            for part in self.field.split("."):
                actual = actual[part]
            passed = actual == self.expected
            return CheckerResult(checker=self.name, passed=passed,
                                 score=1.0 if passed else 0.0,
                                 message=f"actual={actual!r}, expected={self.expected!r}")
        except (OSError, UnicodeError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            return CheckerResult(checker=self.name, passed=False, message=str(exc))


class PytestChecker(Checker):
    name = "pytest"

    def __init__(self, path: str = ".", args: list[str] | None = None, timeout: float = 120.0):
        self.path = path
        self.args = args or []
        self.timeout = min(max(timeout, 1.0), 600.0)

    async def check(self, workspace: Path) -> CheckerResult:
        try:
            target = _safe_path(workspace, self.path)
            command = [sys.executable, "-m", "pytest", "-q", str(target), *self.args]
            proc = await asyncio.to_thread(
                subprocess.run, command, cwd=str(workspace), capture_output=True,
                text=True, timeout=self.timeout,
            )
            output = (proc.stdout + proc.stderr).strip()[-4000:]
            passed = proc.returncode == 0
            return CheckerResult(checker=self.name, passed=passed,
                                 score=1.0 if passed else 0.0,
                                 message=output or ("pytest passed" if passed else "pytest failed"),
                                 details={"returncode": proc.returncode})
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            return CheckerResult(checker=self.name, passed=False, message=str(exc))


class HiddenPytestChecker(PytestChecker):
    """Run repository-hidden tests against the isolated workspace source."""

    name = "hidden_pytest"

    async def check(self, workspace: Path) -> CheckerResult:
        try:
            hidden_root = Path(__file__).resolve().parents[2] / "backend" / "evals" / "hidden_tests"
            target = (hidden_root / self.path).resolve()
            if not target.is_relative_to(hidden_root.resolve()):
                raise ValueError(f"hidden test path escapes hidden_tests: {self.path}")
            env = os.environ.copy()
            source = str((workspace / "src").resolve())
            env["PYTHONPATH"] = source + os.pathsep + env.get("PYTHONPATH", "")
            command = [sys.executable, "-m", "pytest", "-q", str(target), *self.args]
            proc = await asyncio.to_thread(
                subprocess.run, command, cwd=str(workspace), capture_output=True,
                text=True, timeout=self.timeout, env=env,
            )
            output = (proc.stdout + proc.stderr).strip()[-4000:]
            passed = proc.returncode == 0
            return CheckerResult(
                checker=self.name, passed=passed, score=1.0 if passed else 0.0,
                message=output or ("hidden pytest passed" if passed else "hidden pytest failed"),
                details={"returncode": proc.returncode, "path": self.path},
            )
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            return CheckerResult(checker=self.name, passed=False, message=str(exc))


class AnswerContainsAllChecker(Checker):
    name = "answer_contains_all"

    def __init__(self, texts: list[str], answer: str = ""):
        self.texts = [str(text) for text in texts]
        self.answer = answer

    async def check(self, workspace: Path) -> CheckerResult:
        missing = [text for text in self.texts if text not in self.answer]
        passed = not missing
        return CheckerResult(
            checker=self.name,
            passed=passed,
            score=1.0 if passed else 0.0,
            message="answer contains all required facts" if passed else f"missing answer facts: {missing}",
            details={"missing": missing},
        )


class AnswerOrderedContainsChecker(Checker):
    name = "answer_ordered_contains"

    def __init__(self, texts: list[str], answer: str = ""):
        self.texts = [str(text) for text in texts]
        self.answer = answer

    async def check(self, workspace: Path) -> CheckerResult:
        cursor = 0
        for text in self.texts:
            match = self.answer.find(text, cursor)
            if match < 0:
                return CheckerResult(
                    checker=self.name, passed=False, score=0.0,
                    message=f"answer item not found in order: {text}",
                )
            cursor = match + len(text)
        return CheckerResult(
            checker=self.name, passed=True, score=1.0,
            message="answer facts appear in the required order",
        )


class TracePolicyChecker(Checker):
    name = "trace_policy"

    def __init__(self, trace_path: str = "", runtime: dict[str, Any] | None = None,
                 max_tool_calls: int | None = None, required_tools: list[str] | None = None,
                 forbidden_tools: list[str] | None = None):
        self.trace_path = trace_path
        self.runtime = runtime or {}
        self.max_tool_calls = max_tool_calls
        self.required_tools = [str(item) for item in (required_tools or [])]
        self.forbidden_tools = [str(item) for item in (forbidden_tools or [])]

    async def check(self, workspace: Path) -> CheckerResult:
        events: list[dict[str, Any]] = []
        try:
            if self.trace_path:
                events = [
                    json.loads(line)
                    for line in Path(self.trace_path).read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
            tools = [str(event.get("tool_name") or "") for event in events if event.get("event_type") == "tool_call"]
            observed_calls = self.runtime.get("turn_tool_calls")
            if observed_calls is None:
                observed_calls = self.runtime.get("total_tool_calls")
            if observed_calls is None:
                observed_calls = len(tools)
            if self.max_tool_calls is not None and int(observed_calls) > self.max_tool_calls:
                return CheckerResult(
                    checker=self.name, passed=False, score=0.0,
                    message=f"tool calls {observed_calls} exceed {self.max_tool_calls}",
                    details={"tool_calls": observed_calls},
                )
            scoped_tools = self.runtime.get("turn_tool_names")
            if scoped_tools is None:
                scoped_tools = tools
            scoped_tools = [str(tool) for tool in scoped_tools]
            # Production DevAgent uses the foundation batch mutation tool for
            # file edits. Keep legacy case terminology compatible without
            # hiding the actual observed tool names in the result details.
            aliases = {"edit_file": "apply_changes", "write_file": "apply_changes"}
            canonical_tools = {aliases.get(tool, tool) for tool in scoped_tools}
            canonical_required = [aliases.get(tool, tool) for tool in self.required_tools]
            canonical_forbidden = [aliases.get(tool, tool) for tool in self.forbidden_tools]
            missing = [tool for tool in self.required_tools if aliases.get(tool, tool) not in canonical_tools]
            forbidden = [tool for tool in self.forbidden_tools if aliases.get(tool, tool) in canonical_tools]
            passed = not missing and not forbidden
            return CheckerResult(
                checker=self.name, passed=passed, score=1.0 if passed else 0.0,
                message="trace policy satisfied" if passed else f"missing={missing}, forbidden={forbidden}",
                details={
                    "tool_calls": observed_calls,
                    "tools": scoped_tools,
                    "canonical_tools": sorted(canonical_tools),
                    "required_tools": canonical_required,
                    "forbidden_tools": canonical_forbidden,
                    "missing": missing,
                    "forbidden": forbidden,
                },
            )
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            return CheckerResult(checker=self.name, passed=False, message=str(exc))


def _load_uml(workspace: Path, path: str) -> dict[str, Any]:
    document = json.loads(_safe_path(workspace, path).read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("diagrams"), list):
        raise ValueError("UML project must contain a diagrams array")
    return document


def _selected_diagrams(document: dict[str, Any], name: str = "") -> list[dict[str, Any]]:
    diagrams = document["diagrams"]
    if not name:
        return diagrams
    return [diagram for diagram in diagrams if diagram.get("name") == name]


class UMLValidChecker(Checker):
    name = "uml_valid"

    def __init__(self, path: str):
        self.path = path

    async def check(self, workspace: Path) -> CheckerResult:
        try:
            document = _load_uml(workspace, self.path)
            diagrams = document["diagrams"]
            passed = bool(diagrams) and all(isinstance(item, dict) for item in diagrams)
            return CheckerResult(
                checker=self.name, passed=passed, score=1.0 if passed else 0.0,
                message=f"{len(diagrams)} UML diagrams loaded",
                details={"diagram_count": len(diagrams)},
            )
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            return CheckerResult(checker=self.name, passed=False, message=str(exc))


class UMLContainsChecker(Checker):
    name = "uml_contains"

    def __init__(self, path: str, kind: str, name: str, diagram: str = ""):
        self.path = path
        self.kind = kind
        self.name_to_find = name
        self.diagram = diagram

    async def check(self, workspace: Path) -> CheckerResult:
        try:
            document = _load_uml(workspace, self.path)
            diagrams = _selected_diagrams(document, self.diagram)
            found = False
            for item in diagrams:
                if self.kind == "diagram":
                    found = item.get("name") == self.name_to_find
                elif self.kind == "component":
                    found = any(x.get("name") == self.name_to_find for x in item.get("components", []))
                elif self.kind == "class":
                    found = any(x.get("name") == self.name_to_find for x in item.get("classes", []))
                elif self.kind == "message":
                    found = any(self.name_to_find in x.get("label", "") for x in item.get("messages", []))
                else:
                    raise ValueError(f"unsupported UML entity kind: {self.kind}")
                if found:
                    break
            return CheckerResult(
                checker=self.name, passed=found, score=1.0 if found else 0.0,
                message=f"{self.kind} {self.name_to_find!r} {'found' if found else 'not found'}",
            )
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            return CheckerResult(checker=self.name, passed=False, message=str(exc))


class UMLComponentNamesChecker(Checker):
    """Assert the exact component names in one UML component diagram."""

    name = "uml_component_names"

    def __init__(self, path: str, names: list[str], diagram: str = ""):
        self.path = path
        self.expected = sorted(str(name) for name in names)
        self.diagram = diagram

    async def check(self, workspace: Path) -> CheckerResult:
        try:
            document = _load_uml(workspace, self.path)
            actual: list[str] = []
            for diagram in _selected_diagrams(document, self.diagram):
                actual.extend(
                    str(item.get("name", ""))
                    for item in diagram.get("components", [])
                )
            actual = sorted(name for name in actual if name)
            passed = actual == self.expected
            return CheckerResult(
                checker=self.name,
                passed=passed,
                score=1.0 if passed else 0.0,
                message=f"components={actual!r}, expected={self.expected!r}",
                details={"actual": actual, "expected": self.expected},
            )
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            return CheckerResult(checker=self.name, passed=False, message=str(exc))


class UMLRelationChecker(Checker):
    name = "uml_relation"

    def __init__(self, path: str, source: str, target: str, relation_type: str = "", diagram: str = ""):
        self.path = path
        self.source = source
        self.target = target
        self.relation_type = relation_type
        self.diagram = diagram

    @staticmethod
    def _resolve_name(items: list[dict[str, Any]], identifier: str) -> str:
        for item in items:
            if item.get("id") == identifier:
                return item.get("name", identifier)
        return identifier

    async def check(self, workspace: Path) -> CheckerResult:
        try:
            document = _load_uml(workspace, self.path)
            found = False
            for diagram in _selected_diagrams(document, self.diagram):
                components = diagram.get("components", [])
                classes = diagram.get("classes", [])
                entities = components + classes
                for relation_key in ("comp_relations", "relations"):
                    for relation in diagram.get(relation_key, []):
                        source = self._resolve_name(entities, relation.get("source", ""))
                        target = self._resolve_name(entities, relation.get("target", ""))
                        if source == self.source and target == self.target and (
                            not self.relation_type or relation.get("type") == self.relation_type
                        ):
                            found = True
                            break
                    if found:
                        break
                if found:
                    break
            return CheckerResult(
                checker=self.name, passed=found, score=1.0 if found else 0.0,
                message=f"relation {self.source}->{self.target} {'found' if found else 'not found'}",
            )
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            return CheckerResult(checker=self.name, passed=False, message=str(exc))


class UMLMethodChecker(Checker):
    name = "uml_method"

    def __init__(self, path: str, class_name: str, method: str, diagram: str = ""):
        self.path = path
        self.class_name = class_name
        self.method = method
        self.diagram = diagram

    async def check(self, workspace: Path) -> CheckerResult:
        try:
            document = _load_uml(workspace, self.path)
            found = any(
                any(
                    item.get("name") == self.class_name
                    and any(method.get("name") == self.method for method in item.get("methods", []))
                    for item in diagram.get("classes", [])
                )
                for diagram in _selected_diagrams(document, self.diagram)
            )
            return CheckerResult(
                checker=self.name, passed=found, score=1.0 if found else 0.0,
                message=f"{self.class_name}.{self.method} {'found' if found else 'not found'}",
            )
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            return CheckerResult(checker=self.name, passed=False, message=str(exc))


class UMLMethodSignatureChecker(Checker):
    name = "uml_method_signature"

    def __init__(self, path: str, class_name: str, method: str, params: str,
                 return_type: str = "", diagram: str = ""):
        self.path = path
        self.class_name = class_name
        self.method = method
        self.params = params
        self.return_type = return_type
        self.diagram = diagram

    async def check(self, workspace: Path) -> CheckerResult:
        try:
            document = _load_uml(workspace, self.path)
            actual: dict[str, Any] | None = None
            for diagram in _selected_diagrams(document, self.diagram):
                for item in diagram.get("classes", []):
                    if item.get("name") != self.class_name:
                        continue
                    for method in item.get("methods", []):
                        if method.get("name") == self.method:
                            actual = method
                            break
                    if actual is not None:
                        break
                if actual is not None:
                    break
            passed = bool(actual) and actual.get("params", "") == self.params
            if self.return_type:
                passed = passed and actual.get("return_type", "") == self.return_type
            return CheckerResult(
                checker=self.name, passed=passed, score=1.0 if passed else 0.0,
                message=(
                    f"{self.class_name}.{self.method} signature matches"
                    if passed else
                    f"actual={actual!r}, expected params={self.params!r}, return={self.return_type!r}"
                ),
                details={"actual": actual, "expected_params": self.params, "expected_return": self.return_type},
            )
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            return CheckerResult(checker=self.name, passed=False, message=str(exc))


class UMLAbsentChecker(Checker):
    name = "uml_absent"

    def __init__(self, path: str, kind: str, name: str, diagram: str = "", class_name: str = "", method: str = ""):
        self.path = path
        self.kind = kind
        self.name_to_find = name
        self.diagram = diagram
        self.class_name = class_name
        self.method = method

    async def check(self, workspace: Path) -> CheckerResult:
        try:
            document = _load_uml(workspace, self.path)
            found = False
            for item in _selected_diagrams(document, self.diagram):
                if self.kind == "class":
                    found = any(x.get("name") == self.name_to_find for x in item.get("classes", []))
                elif self.kind == "component":
                    found = any(x.get("name") == self.name_to_find for x in item.get("components", []))
                elif self.kind == "message":
                    found = any(self.name_to_find in x.get("label", "") for x in item.get("messages", []))
                elif self.kind == "method":
                    found = any(
                        item_class.get("name") == self.class_name
                        and any(method.get("name") == self.method for method in item_class.get("methods", []))
                        for item_class in item.get("classes", [])
                    )
                elif self.kind == "diagram":
                    found = item.get("name") == self.name_to_find
                else:
                    raise ValueError(f"unsupported UML absent kind: {self.kind}")
                if found:
                    break
            passed = not found
            return CheckerResult(
                checker=self.name, passed=passed, score=1.0 if passed else 0.0,
                message=f"{self.kind} {self.name_to_find!r} {'absent' if passed else 'still present'}",
            )
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            return CheckerResult(checker=self.name, passed=False, message=str(exc))


class UMLSequenceChecker(Checker):
    name = "uml_sequence"

    def __init__(self, path: str, labels: list[str], diagram: str = ""):
        self.path = path
        self.labels = labels
        self.diagram = diagram

    async def check(self, workspace: Path) -> CheckerResult:
        try:
            document = _load_uml(workspace, self.path)
            messages = []
            for diagram in _selected_diagrams(document, self.diagram):
                messages.extend(sorted(diagram.get("messages", []), key=lambda item: item.get("order", 0)))
            cursor = 0
            for expected in self.labels:
                match = next((index for index in range(cursor, len(messages))
                              if expected in messages[index].get("label", "")), None)
                if match is None:
                    return CheckerResult(checker=self.name, passed=False, score=0.0,
                                         message=f"sequence label not found: {expected}")
                cursor = match + 1
            return CheckerResult(checker=self.name, passed=True, score=1.0,
                                 message=f"{len(self.labels)} sequence labels found in order")
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            return CheckerResult(checker=self.name, passed=False, message=str(exc))


class UMLSequenceExactChecker(Checker):
    name = "uml_sequence_exact"

    def __init__(self, path: str, labels: list[str], diagram: str = ""):
        self.path = path
        self.labels = labels
        self.diagram = diagram

    async def check(self, workspace: Path) -> CheckerResult:
        try:
            document = _load_uml(workspace, self.path)
            diagrams = _selected_diagrams(document, self.diagram)
            messages = []
            for diagram in diagrams:
                messages.extend(sorted(diagram.get("messages", []), key=lambda item: item.get("order", 0)))
            actual = [str(message.get("label", "")) for message in messages]
            passed = len(actual) == len(self.labels) and all(
                expected in actual[index] for index, expected in enumerate(self.labels)
            )
            return CheckerResult(
                checker=self.name, passed=passed, score=1.0 if passed else 0.0,
                message=f"sequence={actual!r}, expected labels={self.labels!r}",
                details={"actual": actual, "expected": self.labels},
            )
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            return CheckerResult(checker=self.name, passed=False, message=str(exc))


class PathsUnchangedChecker(Checker):
    name = "paths_unchanged"

    def __init__(self, paths: list[str], baseline: dict[str, str | None] | None = None):
        self.paths = paths
        self.baseline = baseline or {}

    @staticmethod
    def _digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    async def check(self, workspace: Path) -> CheckerResult:
        changed: list[str] = []
        for relative_path in self.paths:
            try:
                path = _safe_path(workspace, relative_path)
                current = self._digest(path) if path.is_file() else None
                if current != self.baseline.get(relative_path):
                    changed.append(relative_path)
            except (OSError, ValueError):
                changed.append(relative_path)
        passed = not changed
        return CheckerResult(
            checker=self.name, passed=passed, score=1.0 if passed else 0.0,
            message="protected paths unchanged" if passed else f"changed paths: {changed}",
            details={"changed": changed},
        )


def build_checkers(
    configs: list[dict[str, Any]],
    baseline: dict[str, str | None] | None = None,
    answer: str = "",
    trace_path: str = "",
    runtime: dict[str, Any] | None = None,
) -> list[Checker]:
    result: list[Checker] = []
    for config in configs:
        kind = config.get("type", "")
        if kind == "file_exists":
            result.append(FileExistsChecker(config["path"]))
        elif kind == "file_absent":
            result.append(FileAbsentChecker(config["path"]))
        elif kind == "file_contains":
            result.append(FileContainsChecker(config["path"], config["text"]))
        elif kind == "file_not_contains":
            result.append(FileNotContainsChecker(config["path"], config["text"]))
        elif kind == "json_field":
            result.append(JsonFieldChecker(config["path"], config["field"], config.get("expected")))
        elif kind == "pytest":
            result.append(PytestChecker(config.get("path", "."), config.get("args"), config.get("timeout", 120)))
        elif kind == "hidden_pytest":
            result.append(HiddenPytestChecker(config.get("path", "."), config.get("args"), config.get("timeout", 120)))
        elif kind == "answer_contains_all":
            result.append(AnswerContainsAllChecker(config.get("texts", []), answer))
        elif kind == "answer_ordered_contains":
            result.append(AnswerOrderedContainsChecker(config.get("texts", []), answer))
        elif kind == "trace_policy":
            result.append(TracePolicyChecker(
                trace_path=trace_path,
                runtime=runtime,
                max_tool_calls=config.get("max_tool_calls"),
                required_tools=config.get("required_tools"),
                forbidden_tools=config.get("forbidden_tools"),
            ))
        elif kind == "uml_valid":
            result.append(UMLValidChecker(config["path"]))
        elif kind == "uml_contains":
            result.append(UMLContainsChecker(config["path"], config["kind"], config["name"], config.get("diagram", "")))
        elif kind == "uml_component_names":
            result.append(UMLComponentNamesChecker(config["path"], config["names"], config.get("diagram", "")))
        elif kind == "uml_relation":
            result.append(UMLRelationChecker(config["path"], config["source"], config["target"], config.get("relation_type", ""), config.get("diagram", "")))
        elif kind == "uml_method":
            result.append(UMLMethodChecker(config["path"], config["class_name"], config["method"], config.get("diagram", "")))
        elif kind == "uml_method_signature":
            result.append(UMLMethodSignatureChecker(
                config["path"], config["class_name"], config["method"],
                config["params"], config.get("return_type", ""), config.get("diagram", ""),
            ))
        elif kind == "uml_absent":
            result.append(UMLAbsentChecker(
                config["path"], config["kind"], config["name"], config.get("diagram", ""),
                config.get("class_name", ""), config.get("method", ""),
            ))
        elif kind == "uml_sequence":
            result.append(UMLSequenceChecker(config["path"], config["labels"], config.get("diagram", "")))
        elif kind == "uml_sequence_exact":
            result.append(UMLSequenceExactChecker(config["path"], config["labels"], config.get("diagram", "")))
        elif kind == "paths_unchanged":
            result.append(PathsUnchangedChecker(config["paths"], baseline))
        else:
            raise ValueError(f"unsupported checker type: {kind}")
    return result
