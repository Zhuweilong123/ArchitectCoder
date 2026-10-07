"""Copy evaluation artifacts using consistent snapshots for SQLite files."""

from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import time


def _is_sqlite(path: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    with path.open("rb") as stream:
        return stream.read(16) == b"SQLite format 3\x00"


def _ignore_sidecars(directory, names):
    directory = Path(directory)
    ignored = []
    for name in names:
        for suffix in ("-wal", "-shm", "-journal"):
            if name.endswith(suffix) and _is_sqlite(directory / name[:-len(suffix)]):
                ignored.append(name)
                break
    return ignored


def _copy_file(source, destination):
    source = Path(source)
    if not _is_sqlite(source):
        return shutil.copy2(source, destination)

    # Raw copying the main file can lose committed WAL transactions; copying
    # SHM on Windows can fail even when the database is operating normally.
    # backup() includes committed WAL contents without copying its sidecars.
    deadline = time.monotonic() + 10.0

    def progress(status, remaining, total):
        if time.monotonic() >= deadline:
            raise TimeoutError(f"SQLite snapshot timed out: {source}")

    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as reader:
        with closing(sqlite3.connect(str(destination))) as writer:
            reader.backup(writer, pages=256, progress=progress, sleep=0.05)
    return str(destination)


def copy_workspace_snapshot(workspace: Path, destination: Path) -> None:
    """Copy ordinary artifacts and standalone SQLite database snapshots."""
    shutil.copytree(workspace, destination, symlinks=True, dirs_exist_ok=True,
                    ignore=_ignore_sidecars, copy_function=_copy_file)
