"""Canonical runtime artifact paths."""

from __future__ import annotations

from pathlib import Path

from .settings import Settings, get_settings


def runtime_root(settings: Settings | None = None) -> Path:
    current = settings or get_settings()
    return Path(current.uml_dir).resolve().parent


def evaluation_root(settings: Settings | None = None) -> Path:
    return runtime_root(settings) / "evals"


def evaluation_results_dir(settings: Settings | None = None) -> Path:
    return evaluation_root(settings) / "results"


def evaluation_traces_dir(settings: Settings | None = None) -> Path:
    return evaluation_root(settings) / "traces"


def evaluation_runs_dir(settings: Settings | None = None) -> Path:
    return evaluation_root(settings) / "runs"


def chat_log_dir(settings: Settings | None = None) -> Path:
    return runtime_root(settings) / "chat_log"


__all__ = [
    "chat_log_dir",
    "evaluation_results_dir",
    "evaluation_root",
    "evaluation_runs_dir",
    "evaluation_traces_dir",
    "runtime_root",
]
