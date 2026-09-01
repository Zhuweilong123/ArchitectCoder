from pathlib import Path
from types import SimpleNamespace

from app.core import security


def test_repo_workspace_remains_allowed_when_external_roots_are_configured(monkeypatch, tmp_path):
    """Configured external roots extend, rather than replace, the repository root."""
    external_root = tmp_path / "external"
    external_root.mkdir()
    uml_dir = tmp_path / "uml"
    uml_dir.mkdir()
    monkeypatch.setattr(security, "get_settings", lambda: SimpleNamespace(
        workspace_roots=str(external_root),
        uml_dir=str(uml_dir),
    ))

    generated_src = Path(__file__).resolve().parents[2] / "generated" / "src"
    normalized, error = security.validate_agent_workspace_path(
        str(generated_src), kind="directory",
    )

    assert error is None
    assert Path(normalized) == generated_src.resolve()
