from backend.config import Settings
from pathlib import Path


def _settings(**overrides):
    values = {
        "llm_api_key": "test-key",
        "llm_base_url": "http://test-llm/v1",
        "llm_model_id": "test-model",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_settings_accepts_deprecated_sub_agent_model_without_routing(monkeypatch):
    monkeypatch.setenv("SUB_AGENT_MODEL", "legacy-flash-model")

    settings = _settings()

    assert settings.llm_model_id
    assert settings.legacy_sub_agent_model == "legacy-flash-model"


def test_settings_default_task_budget_is_200k():
    settings = _settings()

    assert settings.agent_per_run_execution_budget_tokens == 200000


def test_settings_default_subagent_budget_is_500k():
    settings = _settings()

    assert settings.agent_subagent_per_run_execution_budget_tokens == 500000


def test_settings_enables_main_agent_subagent_by_default():
    settings = _settings()

    assert settings.agent_main_subagent_enabled is True


def test_settings_allows_overriding_subagent_budget(monkeypatch):
    monkeypatch.setenv("AGENT_SUBAGENT_PER_RUN_EXECUTION_BUDGET_TOKENS", "120000")

    settings = _settings()

    assert settings.agent_subagent_per_run_execution_budget_tokens == 120000


def test_settings_exposes_pluggable_memory_defaults():
    settings = _settings()

    assert settings.agent_orchestration_enabled is True
    assert settings.agent_memory_enabled is True
    assert settings.agent_trace_enabled is True
    assert settings.agent_evals_enabled is True
    assert settings.agent_memory_provider == "extensions.memory:create"
    assert settings.agent_memory_recall_top_k == 3
    assert settings.agent_memory_recall_max_tokens == 500


def test_settings_exposes_pluggable_knowledge_graph_defaults():
    settings = _settings()

    assert settings.agent_knowledge_graph_enabled is True
    assert settings.agent_knowledge_graph_provider == "extensions.knowledge_graph:create"
    assert settings.agent_knowledge_graph_db_path == ""


def test_settings_points_managed_plugins_at_extensions_directory():
    settings = _settings()

    assert settings.agent_orchestrator_provider == "extensions.orchestration:create"
    assert settings.agent_memory_provider == "extensions.memory:create"
    assert settings.agent_trace_provider == "extensions.trace:create"
    assert settings.agent_evals_provider == "extensions.evals:create"
    assert settings.agent_knowledge_graph_provider == "extensions.knowledge_graph:create"


def test_settings_planner_budget_has_reasoning_headroom():
    settings = _settings()

    assert settings.agent_planner_max_tokens == 3000


def test_settings_resolves_relative_storage_from_backend_directory():
    settings = _settings(uml_dir="../temp/uml_files")

    expected = (Path(__file__).resolve().parents[2] / "../temp/uml_files").resolve()
    assert Path(settings.uml_dir) == expected
