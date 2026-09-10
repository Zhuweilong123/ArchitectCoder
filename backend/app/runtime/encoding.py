"""Encoding helpers for process output on Windows and POSIX hosts."""

from __future__ import annotations

import codecs
import os


def decode_process_output(data: bytes | str | None) -> str:
    """Decode captured process output without corrupting Chinese text."""
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    if not data:
        return ""
    if data.startswith(codecs.BOM_UTF8):
        return data.decode("utf-8-sig", errors="replace")
    if data.startswith(codecs.BOM_UTF16_LE) or data.startswith(codecs.BOM_UTF16_BE):
        return data.decode("utf-16", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        if os.name == "nt":
            return data.decode("gbk", errors="replace")
        return data.decode("utf-8", errors="replace")