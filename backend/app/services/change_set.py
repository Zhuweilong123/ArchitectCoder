"""Agent 变更集：为文件工具提供可审计、可回滚的提交边界。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

from app.services.project_repository import ProjectConflictError, ProjectRepository

logger = logging.getLogger(__name__)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _atomic_write(path: Path, content: str) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".rollback", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


@dataclass
class ChangeRecord:
    path: str
    before_exists: bool
    before_sha256: str
    after_sha256: str


class ChangeSet:
    """一个 Agent run 的文件变更日志。

    文件工具仍然即时写入（这样审核工具能读取 after 状态），但每次写入都
    保留 before 快照；run 成功时 commit，异常/人工要求时可 rollback。
    """

    def __init__(
        self,
        project_file: str = "",
        *,
        project_repository: ProjectRepository | None = None,
    ):
        self.project_file = project_file
        self.project_repository = project_repository or ProjectRepository()
        self._before: dict[str, tuple[bool, str]] = {}
        self._records: dict[str, ChangeRecord] = {}
        self._project_revisions: dict[str, int] = {}
        self._lock = threading.RLock()
        self.status = "open"

    def begin(self) -> None:
        with self._lock:
            self._before.clear()
            self._records.clear()
            self._project_revisions.clear()
            if self.project_file and self.project_file.lower().endswith(".umlproj"):
                project_path = str(Path(self.project_file).resolve())
                if os.path.isfile(project_path):
                    self._project_revisions[project_path] = self.project_repository.revision(project_path)
            self.status = "open"

    @property
    def has_changes(self) -> bool:
        return bool(self._records)

    def record(self, path: str, before_exists: bool, before: str, after: str) -> None:
        resolved = str(Path(path).resolve())
        with self._lock:
            if resolved.lower().endswith(".umlproj") and resolved not in self._project_revisions:
                if os.path.isfile(resolved):
                    self._project_revisions[resolved] = self.project_repository.revision(resolved)
            if resolved not in self._before:
                self._before[resolved] = (before_exists, before)
            initial_exists, initial = self._before[resolved]
            if initial_exists and initial == after:
                self._records.pop(resolved, None)
                return
            if not initial_exists and not after:
                self._records.pop(resolved, None)
                return
            self._records[resolved] = ChangeRecord(
                path=resolved,
                before_exists=initial_exists,
                before_sha256=_sha256(initial) if initial_exists else "",
                after_sha256=_sha256(after),
            )

    def manifest(self) -> list[dict]:
        with self._lock:
            return [asdict(record) for record in self._records.values()]

    def commit(self) -> list[dict]:
        with self._lock:
            if self.status != "open":
                return self.manifest()
            manifest = self.manifest()

        project_paths = [
            record["path"] for record in manifest
            if record["path"].lower().endswith(".umlproj")
        ]
        try:
            for project_path in dict.fromkeys(project_paths):
                expected = self._project_revisions.get(project_path)
                if expected is not None:
                    record = next(
                        item for item in manifest if item["path"] == project_path
                    )
                    path = Path(project_path)
                    if not path.is_file():
                        raise ProjectConflictError(project_path, expected, 0)
                    if _sha256(path.read_text(encoding="utf-8")) != record["after_sha256"]:
                        raise ProjectConflictError(
                            project_path,
                            expected,
                            self.project_repository.revision(project_path),
                        )
                    self.project_repository.commit_external_change(
                        project_path,
                        expected_revision=expected,
                    )
        except ProjectConflictError:
            with self._lock:
                self.status = "conflict"
            raise

        with self._lock:
            self.status = "committed"

        refresh_paths = [
            record["path"] for record in manifest
            if record["path"].lower().endswith((".umlproj", ".uml"))
        ]
        if self.project_file and os.path.isfile(self.project_file):
            refresh_paths.append(self.project_file)
        for project_path in dict.fromkeys(refresh_paths):
            self._refresh_kg(project_path)
        return manifest


    def _legacy_commit(self) -> list[dict]:
        with self._lock:
            if self.status != "open":
                return self.manifest()
            manifest = self.manifest()
            self.status = "committed"

        # 只有提交边界触发 KG，避免同一轮多次 edit 产生多次重建。
        project_paths = [r["path"] for r in manifest if r["path"].lower().endswith((".umlproj", ".uml"))]
        if self.project_file and os.path.isfile(self.project_file):
            project_paths.append(self.project_file)
        for project_path in dict.fromkeys(project_paths):
            self._refresh_kg(project_path)
        return manifest

    def rollback(self) -> list[dict]:
        with self._lock:
            if self.status == "rolled_back":
                return self.manifest()
            records = list(self._records.values())
            before = dict(self._before)
            self.status = "rolled_back"
        for record in reversed(records):
            exists, content = before.get(record.path, (False, ""))
            path = Path(record.path)
            try:
                if path.exists():
                    current = path.read_text(encoding="utf-8")
                    if _sha256(current) != record.after_sha256:
                        logger.warning(
                            "[ChangeSet] skip rollback after external change: %s",
                            path,
                        )
                        continue
                if exists:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    _atomic_write(path, content)
                elif path.exists():
                    path.unlink()
            except Exception:
                logger.exception("[ChangeSet] rollback failed: %s", path)
        return [asdict(record) for record in records]

    def _refresh_kg(self, project_path: str) -> None:
        try:
            from app.services.file_service import _rebuild_kg_async
            _rebuild_kg_async(self.project_repository.load(project_path), project_path)
        except Exception:
            logger.exception("[ChangeSet] KG refresh failed: %s", project_path)
