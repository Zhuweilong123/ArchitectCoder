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

    def __init__(self, project_file: str = ""):
        self.project_file = project_file
        self._before: dict[str, tuple[bool, str]] = {}
        self._records: dict[str, ChangeRecord] = {}
        self._lock = threading.RLock()
        self.status = "open"

    def begin(self) -> None:
        with self._lock:
            self._before.clear()
            self._records.clear()
            self.status = "open"

    @property
    def has_changes(self) -> bool:
        return bool(self._records)

    def record(self, path: str, before_exists: bool, before: str, after: str) -> None:
        resolved = str(Path(path).resolve())
        with self._lock:
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
                if exists:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    _atomic_write(path, content)
                elif path.exists():
                    path.unlink()
            except Exception:
                logger.exception("[ChangeSet] rollback failed: %s", path)
        return [asdict(record) for record in records]

    @staticmethod
    def _refresh_kg(project_path: str) -> None:
        try:
            from app.services.file_service import load_project, _rebuild_kg_async
            _rebuild_kg_async(load_project(project_path), project_path)
        except Exception:
            logger.exception("[ChangeSet] KG refresh failed: %s", project_path)

