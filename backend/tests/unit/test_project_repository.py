"""Tests for project persistence and optimistic revision checks."""

import json

import pytest

from app.models.uml import Project, UmlDiagram
from app.services.change_set import ChangeSet
from app.services.project_repository import ProjectConflictError, ProjectRepository


def test_save_assigns_and_increments_revision(tmp_path):
    repository = ProjectRepository()
    filepath = tmp_path / "demo.umlproj"

    first = repository.save(Project(name="demo"), filepath)
    assert first.revision == 1
    assert repository.load(filepath).revision == 1

    second = repository.save(
        first.project.model_copy(update={"name": "demo-updated"}),
        filepath,
        expected_revision=first.revision,
    )
    assert second.revision == 2
    assert repository.load(filepath).name == "demo-updated"


def test_stale_save_is_rejected_without_overwriting_file(tmp_path):
    repository = ProjectRepository()
    filepath = tmp_path / "demo.umlproj"

    first = repository.save(Project(name="demo"), filepath)
    second = repository.save(
        first.project.model_copy(update={"name": "newer"}),
        filepath,
        expected_revision=first.revision,
    )

    with pytest.raises(ProjectConflictError) as error:
        repository.save(
            first.project.model_copy(update={"name": "stale"}),
            filepath,
            expected_revision=first.revision,
        )

    assert error.value.expected_revision == 1
    assert error.value.actual_revision == 2
    assert repository.load(filepath).name == "newer"
    assert second.revision == repository.load(filepath).revision


def test_legacy_uml_is_loaded_as_revision_zero_project(tmp_path):
    repository = ProjectRepository()
    filepath = tmp_path / "legacy.uml"
    filepath.write_text(
        json.dumps(UmlDiagram(name="legacy").model_dump()),
        encoding="utf-8",
    )

    project = repository.load(filepath)

    assert project.name == "legacy"
    assert len(project.diagrams) == 1
    assert project.revision == 0


def test_changeset_versions_external_project_edit_on_commit(tmp_path):
    repository = ProjectRepository()
    filepath = tmp_path / "demo.umlproj"
    initial = repository.save(Project(name="demo"), filepath)
    changes = ChangeSet(str(filepath), project_repository=repository)
    changes.begin()

    filepath.write_text(
        json.dumps(initial.project.model_copy(update={"name": "edited"}).model_dump()),
        encoding="utf-8",
    )
    changes.record(
        str(filepath),
        True,
        initial.project.model_dump_json(),
        filepath.read_text(encoding="utf-8"),
    )

    changes.commit()

    persisted = repository.load(filepath)
    assert persisted.name == "edited"
    assert persisted.revision == 2
    assert changes.status == "committed"


def test_changeset_rejects_external_edit_after_agent_write(tmp_path):
    repository = ProjectRepository()
    filepath = tmp_path / "demo.umlproj"
    initial = repository.save(Project(name="demo"), filepath)
    changes = ChangeSet(str(filepath), project_repository=repository)
    changes.begin()

    agent_content = json.dumps(
        initial.project.model_copy(update={"name": "agent-edit"}).model_dump(),
    )
    filepath.write_text(agent_content, encoding="utf-8")
    changes.record(
        str(filepath), True, initial.project.model_dump_json(), agent_content,
    )
    filepath.write_text(
        json.dumps(initial.project.model_copy(update={"name": "external-edit"}).model_dump()),
        encoding="utf-8",
    )

    with pytest.raises(ProjectConflictError):
        changes.commit()

    assert changes.status == "conflict"
    assert repository.load(filepath).name == "external-edit"
    assert repository.load(filepath).revision == 1


def test_rejected_candidate_restores_project_and_allows_revision(tmp_path):
    repository = ProjectRepository()
    filepath = tmp_path / "demo.umlproj"
    repository.save(Project(name="demo"), filepath)
    original = filepath.read_text(encoding="utf-8")
    changes = ChangeSet(str(filepath), project_repository=repository)
    changes.begin()

    rejected = json.dumps({**json.loads(original), "name": "rejected", "revision": 2})
    filepath.write_text(rejected, encoding="utf-8")
    changes.record(str(filepath), True, original, rejected)
    assert changes.restore_path_for_review(str(filepath))
    assert filepath.read_text(encoding="utf-8") == original
    assert not changes.has_changes

    revised = json.dumps({**json.loads(original), "name": "revised"})
    filepath.write_text(revised, encoding="utf-8")
    changes.record(str(filepath), True, original, revised)
    changes.commit()
    assert repository.load(filepath).name == "revised"
    assert repository.revision(filepath) == 2


def test_project_grid_defaults_and_legacy_diagram_migration():
    assert Project(name="new").grid_settings.grid_color == "#f59e0b"
    assert Project.model_validate({"diagrams": [{"name": "new"}]}).grid_settings.grid_color == "#f59e0b"

    legacy = Project.model_validate({
        "name": "legacy",
        "active_diagram_index": 1,
        "diagrams": [
            {"name": "first", "grid_color": "#111111", "grid_size": 10},
            {"name": "active", "grid_color": "#ff8800", "grid_size": 50,
             "grid_visible": False, "grid_thickness": 3, "snap_to_grid": False},
        ],
    })
    assert legacy.grid_settings.model_dump() == {
        "grid_visible": False,
        "grid_size": 50,
        "grid_color": "#ff8800",
        "grid_thickness": 3,
        "snap_to_grid": False,
    }
    assert Project.model_validate(legacy.model_dump()).grid_settings == legacy.grid_settings
