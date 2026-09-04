"""Canonical paths for versioned evaluation data.

Evaluation execution code lives under :mod:`extensions.evals`, while the case
catalog and project fixtures are repository data under ``backend/evals``.
Keeping the lookup in one module prevents callers from coupling themselves to
the Python package layout.
"""

from __future__ import annotations

from pathlib import Path


def data_root() -> Path:
    """Return the repository-local evaluation data directory."""

    # ``.../extensions/evals/paths.py`` -> repository root -> backend.
    return Path(__file__).resolve().parents[2] / "backend" / "evals"


def cases_dir() -> Path:
    return data_root() / "cases"


def projects_dir() -> Path:
    return data_root() / "projects"


def fixtures_dir() -> Path:
    return data_root() / "fixtures"


def baseline_path() -> Path:
    return data_root() / "baseline.json"
