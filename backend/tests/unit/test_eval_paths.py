from pathlib import Path

from extensions.evals.paths import baseline_path, cases_dir, data_root, fixtures_dir, projects_dir


def test_evaluation_data_paths_are_repo_local_and_separate_from_runtime_code():
    backend_root = Path(__file__).resolve().parents[2]

    assert data_root() == backend_root / "evals"
    assert cases_dir() == data_root() / "cases"
    assert fixtures_dir() == data_root() / "fixtures"
    assert projects_dir() == data_root() / "projects"
    assert baseline_path() == data_root() / "baseline.json"
    assert baseline_path().is_file()
    assert cases_dir().is_dir()
    assert fixtures_dir().is_dir()
    assert projects_dir().is_dir()
    assert not (backend_root / "app" / "evals" / "cases").exists()
    assert not (backend_root / "app" / "evals" / "fixtures").exists()
    assert not (backend_root / "app" / "evals" / "projects").exists()
