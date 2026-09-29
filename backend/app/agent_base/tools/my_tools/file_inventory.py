"""On-demand file metrics for ``list_files``, independent of model and graph.

Byte and physical-line counts are exact for scanned text files. Structural
counts are deliberately named hints: a small language-neutral lexical scan
cannot identify every declaration or resolve dependency edges.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_MAX_SCAN_BYTES = 2_000_000
_DECLARATION = re.compile(
    r"^\s*(?:(?:export|public|private|protected|static|async|abstract|"
    r"final|sealed|internal|extern|default)\s+)*"
    r"(?:def|class|function|fn|func|struct|enum|type|record|trait|interface)"
    r"\s+[$A-Za-z_][$\w]*\b"
)
_INTERFACE = re.compile(
    r"^\s*(?:(?:export|public|private|protected|abstract|default)\s+)*"
    r"(?:interface|trait)\s+[$A-Za-z_][$\w]*\b"
)
_DEPENDENCY = re.compile(
    r"^\s*(?:import\b|from\b.*\bimport\b|use\b|using\b|"
    r"#\s*include\b|.*\brequire\s*\()"
)


def _text_hints(content: str) -> tuple[int, int, int]:
    symbols = interfaces = dependencies = 0
    for line in content.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(("//", "/*", "*", "--")):
            continue
        if stripped.startswith("#") and not stripped.startswith("#include"):
            continue
        symbols += bool(_DECLARATION.match(line))
        interfaces += bool(_INTERFACE.match(line))
        dependencies += bool(_DEPENDENCY.match(line))
    return symbols, interfaces, dependencies


def _physical_lines(data: bytes) -> int:
    if not data:
        return 0
    breaks = data.count(b"\n") + data.count(b"\r") - data.count(b"\r\n")
    return breaks + int(data[-1:] not in {b"\n", b"\r"})


@dataclass(frozen=True)
class FileSignals:
    bytes: int
    lines: int | None = None
    symbol_hints: int | None = None
    interface_hints: int | None = None
    dependency_hints: int | None = None

    def compact(self) -> str:
        def value(number: int | None) -> str:
            return str(number) if number is not None else "?"

        return " ".join((
            f"B{self.bytes}", f"L{value(self.lines)}",
            f"S{value(self.symbol_hints)}", f"I{value(self.interface_hints)}",
            f"D{value(self.dependency_hints)}",
        ))


@lru_cache(maxsize=2048)
def _cached_signals(path: str, size: int, mtime_ns: int) -> FileSignals:
    unknown = FileSignals(bytes=size)
    if size > _MAX_SCAN_BYTES:
        return unknown
    try:
        data = Path(path).read_bytes()
    except OSError:
        return unknown
    if b"\0" in data[:4096]:
        return unknown
    content = data.decode("utf-8", errors="replace")
    symbols, interfaces, dependencies = _text_hints(content)
    return FileSignals(
        bytes=size, lines=_physical_lines(data), symbol_hints=symbols,
        interface_hints=interfaces, dependency_hints=dependencies,
    )


def inspect_file(path: Path) -> FileSignals | None:
    """Return cached, language-neutral measurements for a regular file."""
    try:
        stat = path.stat()
        if not path.is_file():
            return None
    except OSError:
        return None
    return _cached_signals(str(path), stat.st_size, stat.st_mtime_ns)


def file_metrics(path: Path) -> str:
    """Return compact metrics, or an empty string for directories and races."""
    signals = inspect_file(path)
    return signals.compact() if signals else ""
