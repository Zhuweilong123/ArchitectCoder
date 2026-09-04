"""Host-neutral filesystem operations for workspace mutations.

The Agent expresses file changes as semantic operations.  This adapter keeps
the implementation on the native filesystem API so callers do not need to
generate Windows, POSIX, or shell-specific commands.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path


class FileSystemOperationError(RuntimeError):
    """Raised when a native filesystem operation cannot be completed."""


class NativeFileSystem:
    """Native, OS-neutral file operations used by structured Agent tools."""

    @staticmethod
    def read_text(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise FileSystemOperationError(str(exc)) from exc

    @staticmethod
    def read_bytes(path: Path) -> bytes:
        try:
            return path.read_bytes()
        except OSError as exc:
            raise FileSystemOperationError(str(exc)) from exc

    @staticmethod
    def write_text(path: Path, content: str) -> None:
        NativeFileSystem.write_bytes(path, content.encode("utf-8"))

    @staticmethod
    def write_bytes(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent),
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        except OSError as exc:
            raise FileSystemOperationError(str(exc)) from exc
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass

    @staticmethod
    def delete_file(path: Path) -> None:
        try:
            path.unlink()
        except OSError as exc:
            raise FileSystemOperationError(str(exc)) from exc

    @staticmethod
    def delete_directory(path: Path) -> None:
        try:
            path.rmdir()
        except OSError as exc:
            raise FileSystemOperationError(str(exc)) from exc

    @staticmethod
    def move(source: Path, target: Path) -> None:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, target)
        except OSError as exc:
            raise FileSystemOperationError(str(exc)) from exc

    @staticmethod
    def copy_file(source: Path, target: Path) -> None:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        except OSError as exc:
            raise FileSystemOperationError(str(exc)) from exc

    @staticmethod
    def make_directory(path: Path) -> None:
        try:
            path.mkdir(parents=True, exist_ok=False)
        except OSError as exc:
            raise FileSystemOperationError(str(exc)) from exc


__all__ = ["FileSystemOperationError", "NativeFileSystem"]
