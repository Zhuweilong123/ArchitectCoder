from types import SimpleNamespace

import pytest

from app.core.production import validate_production_settings


@pytest.fixture
def production_settings(tmp_path):
    return SimpleNamespace(api_host="127.0.0.1", strict_production=True, debug=False,
                           internal_api_token="random-test-credential-at-least-32-bytes",
                           cors_origins=["https://architectcoder.example.com"],
                           workspace_roots=str(tmp_path), project_dir=str(tmp_path / "projects"),
                           runtime_dir=str(tmp_path / "runtime"))


def test_valid_production_and_local_configuration(production_settings):
    validate_production_settings(production_settings)
    production_settings.api_host = "0.0.0.0"
    validate_production_settings(production_settings)
    production_settings.api_host = "127.0.0.1"
    production_settings.strict_production = False
    production_settings.internal_api_token = ""
    production_settings.debug = True
    validate_production_settings(production_settings)


@pytest.mark.parametrize("field,value", [
    ("debug", True), ("internal_api_token", ""), ("internal_api_token", "short"),
    ("internal_api_token", "replace-with-a-random-token-at-least-32-bytes"),
    ("cors_origins", ["*"]), ("cors_origins", []),
    ("cors_origins", ["https://example.com/path"]),
    ("workspace_roots", ""), ("workspace_roots", "relative/project"),
])
def test_incomplete_production_configuration_refuses_startup(production_settings, field, value):
    setattr(production_settings, field, value)
    with pytest.raises(RuntimeError):
        validate_production_settings(production_settings)


def test_non_loopback_requires_strict_mode(production_settings):
    production_settings.strict_production = False
    production_settings.api_host = "0.0.0.0"
    with pytest.raises(RuntimeError, match="STRICT_PRODUCTION"):
        validate_production_settings(production_settings)


def test_filesystem_root_and_nonexistent_workspace_are_rejected(production_settings, tmp_path):
    for root in (tmp_path.anchor, str(tmp_path / "missing")):
        production_settings.workspace_roots = root
        with pytest.raises(RuntimeError, match="WORKSPACE_ROOTS"):
            validate_production_settings(production_settings)


@pytest.mark.parametrize("field", ["project_dir", "runtime_dir"])
def test_storage_cannot_expand_allowed_paths_to_the_filesystem_root(production_settings, tmp_path, field):
    setattr(production_settings, field, tmp_path.anchor)
    with pytest.raises(RuntimeError, match=field.upper()):
        validate_production_settings(production_settings)
