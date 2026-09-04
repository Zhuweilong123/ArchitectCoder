from backend.config import Settings


def test_settings_accepts_deprecated_sub_agent_model_without_routing(monkeypatch):
    monkeypatch.setenv("SUB_AGENT_MODEL", "legacy-flash-model")

    settings = Settings(_env_file=None, deepseek_api_key="test-key")

    assert settings.deepseek_model
    assert settings.legacy_sub_agent_model == "legacy-flash-model"


def test_settings_default_task_budget_is_200k():
    settings = Settings(_env_file=None, deepseek_api_key="test-key")

    assert settings.agent_max_total_tokens == 200000


def test_settings_default_subagent_budget_is_500k():
    settings = Settings(_env_file=None, deepseek_api_key="test-key")

    assert settings.agent_subagent_max_total_tokens == 500000


def test_settings_enables_main_agent_subagent_by_default():
    settings = Settings(_env_file=None, deepseek_api_key="test-key")

    assert settings.agent_main_subagent_enabled is True


def test_settings_allows_overriding_subagent_budget(monkeypatch):
    monkeypatch.setenv("AGENT_SUBAGENT_MAX_TOTAL_TOKENS", "120000")

    settings = Settings(_env_file=None, deepseek_api_key="test-key")

    assert settings.agent_subagent_max_total_tokens == 120000


def test_settings_exposes_pluggable_memory_defaults():
    settings = Settings(_env_file=None, deepseek_api_key="test-key")

    assert settings.agent_orchestration_enabled is True
    assert settings.agent_memory_enabled is True
    assert settings.agent_trace_enabled is True
    assert settings.agent_evals_enabled is True
    assert settings.agent_memory_provider == "extensions.memory:create"
    assert settings.agent_memory_recall_top_k == 3
    assert settings.agent_memory_recall_max_tokens == 500


def test_settings_exposes_pluggable_knowledge_graph_defaults():
    settings = Settings(_env_file=None, deepseek_api_key="test-key")

    assert settings.agent_knowledge_graph_enabled is True
    assert settings.agent_knowledge_graph_provider == "extensions.knowledge_graph:create"
    assert settings.agent_knowledge_graph_db_path == ""


def test_settings_points_managed_plugins_at_extensions_directory():
    settings = Settings(_env_file=None, deepseek_api_key="test-key")

    assert settings.agent_orchestrator_provider == "extensions.orchestration:create"
    assert settings.agent_memory_provider == "extensions.memory:create"
    assert settings.agent_trace_provider == "extensions.trace:create"
    assert settings.agent_evals_provider == "extensions.evals:create"
    assert settings.agent_knowledge_graph_provider == "extensions.knowledge_graph:create"


def test_settings_planner_budget_has_reasoning_headroom():
    settings = Settings(_env_file=None, deepseek_api_key="test-key")

    assert settings.agent_planner_max_tokens == 3000
