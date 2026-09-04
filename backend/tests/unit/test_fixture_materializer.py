import pytest

from extensions.evals.fixture_materializer import materialize_fixture
from extensions.evals.models import ProjectManifest


def test_materialize_fixture_copies_base_then_overlay(tmp_path, monkeypatch):
    fixtures = tmp_path / "fixtures"
    base = fixtures / "base"
    overlay = fixtures / "overlay"
    workspace = tmp_path / "workspace"
    (base / "src").mkdir(parents=True)
    (overlay / "src").mkdir(parents=True)
    workspace.mkdir()
    (base / "src" / "common.py").write_text("base", encoding="utf-8")
    (base / "src" / "bug.py").write_text("old", encoding="utf-8")
    (overlay / "src" / "bug.py").write_text("fixed", encoding="utf-8")
    (overlay / "src" / "__pycache__").mkdir()
    (overlay / "src" / "__pycache__" / "ignored.pyc").write_bytes(b"generated")

    monkeypatch.setattr(
        "extensions.evals.fixture_materializer.fixtures_dir", lambda: fixtures,
    )
    manifest = ProjectManifest(id="overlay", fixture="overlay", base_fixture="base")

    materialize_fixture(overlay, workspace, manifest)

    assert (workspace / "src" / "common.py").read_text(encoding="utf-8") == "base"
    assert (workspace / "src" / "bug.py").read_text(encoding="utf-8") == "fixed"
    assert not (workspace / "src" / "__pycache__").exists()


def test_materialize_fixture_rejects_base_escape(tmp_path, monkeypatch):
    fixtures = tmp_path / "fixtures"
    overlay = fixtures / "overlay"
    overlay.mkdir(parents=True)
    monkeypatch.setattr(
        "extensions.evals.fixture_materializer.fixtures_dir", lambda: fixtures,
    )
    manifest = ProjectManifest.model_construct(
        id="overlay", fixture="overlay", base_fixture="../outside",
    )

    with pytest.raises(ValueError, match="escapes fixtures directory"):
        materialize_fixture(overlay, tmp_path / "workspace", manifest)
