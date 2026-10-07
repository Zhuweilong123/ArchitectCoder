"""Evaluation-owned layout below the host-provided runtime directory."""
from pathlib import Path
from app.agent_base.host_api.services import get_host_services


def data_root() -> Path:
    """Versioned evaluation data stays independent of the Python package layout."""
    return Path(__file__).resolve().parents[2] / "backend" / "evals"


def cases_dir() -> Path:
    return data_root() / "cases"


def projects_dir() -> Path:
    return data_root() / "projects"


def fixtures_dir() -> Path:
    return data_root() / "fixtures"


def baseline_path() -> Path:
    return data_root() / "baseline.json"


def evaluation_root():
    return get_host_services().runtime_path("evals")


def evaluation_results_dir():
    return evaluation_root() / "results"


def evaluation_traces_dir():
    return evaluation_root() / "traces"
