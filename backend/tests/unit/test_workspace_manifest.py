from pathlib import Path

import pytest

from app.runtime.workspace import WorkspaceManifest


@pytest.mark.parametrize("name,field", [
    ("design", "design_root"), ("src", "source_root"), ("test", "test_root"),
])
def test_optional_directory_is_discovered_only_when_it_is_a_directory(tmp_path, name, field):
    target = tmp_path / name
    assert getattr(WorkspaceManifest.from_paths(workspace_root=str(tmp_path)), field) == ""
    target.write_text("not a directory", encoding="utf-8")
    assert getattr(WorkspaceManifest.from_paths(workspace_root=str(tmp_path)), field) == ""
    target.unlink()
    target.mkdir()
    manifest = WorkspaceManifest.from_paths(workspace_root=str(tmp_path))
    assert Path(getattr(manifest, field)) == target.resolve()


@pytest.mark.parametrize("argument,field", [
    ("design_dir", "design_root"), ("source_dir", "source_root"), ("test_dir", "test_root"),
])
def test_explicit_directory_takes_precedence_over_discovery(tmp_path, argument, field):
    for name in ("design", "src", "test", "tests"):
        (tmp_path / name).mkdir()
    explicit = tmp_path / "custom"
    explicit.mkdir()
    manifest = WorkspaceManifest.from_paths(workspace_root=str(tmp_path), **{argument: str(explicit)})
    assert Path(getattr(manifest, field)) == explicit.resolve()


def test_tests_directory_is_discovered_but_two_test_directories_are_ambiguous(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    assert WorkspaceManifest.from_paths(workspace_root=str(tmp_path)).test_root == str(tests.resolve())
    (tmp_path / "test").mkdir()
    assert WorkspaceManifest.from_paths(workspace_root=str(tmp_path)).test_root == ""


def test_selected_project_implies_design_without_inventing_source_or_tests(tmp_path):
    design = tmp_path / "design" / "uml"
    design.mkdir(parents=True)
    project = design / "model.umlproj"
    project.write_text("{}", encoding="utf-8")
    manifest = WorkspaceManifest.from_paths(workspace_root=str(tmp_path), project_file=str(project))
    assert manifest.design_root == str(design.resolve())
    assert manifest.project_file == str(project.resolve())
    assert manifest.source_root == manifest.test_root == ""
    explicit = WorkspaceManifest.from_paths(
        workspace_root=str(tmp_path), project_file=str(project), design_dir=str(design.parent),
    )
    assert explicit.design_root == str(design.parent.resolve())


def test_discovery_uses_workspace_after_conventional_hint_is_promoted(tmp_path):
    design = tmp_path / "design"
    source = tmp_path / "src"
    tests = tmp_path / "test"
    for directory in (design, source, tests):
        directory.mkdir()
    manifest = WorkspaceManifest.from_paths(workspace_root=str(design), source_dir=str(source))
    assert manifest.workspace_root == str(tmp_path.resolve())
    assert manifest.design_root == str(design.resolve())
    assert manifest.test_root == str(tests.resolve())
