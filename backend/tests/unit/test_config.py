from app.core.config import Settings


def test_settings_accepts_deprecated_sub_agent_model_without_routing(monkeypatch):
    monkeypatch.setenv("SUB_AGENT_MODEL", "legacy-flash-model")

    settings = Settings(_env_file=None, deepseek_api_key="test-key")

    assert settings.deepseek_model
    assert settings.legacy_sub_agent_model == "legacy-flash-model"


def test_settings_default_task_budget_is_200k():
    settings = Settings(_env_file=None, deepseek_api_key="test-key")

    assert settings.agent_max_total_tokens == 200000


def test_settings_exposes_pluggable_memory_defaults():
    settings = Settings(_env_file=None, deepseek_api_key="test-key")

    assert settings.agent_memory_enabled is True
    assert settings.agent_memory_provider == "memory_system.provider:create"
    assert settings.agent_memory_recall_top_k == 3
    assert settings.agent_memory_recall_max_tokens == 500


def test_settings_planner_budget_has_reasoning_headroom():
    settings = Settings(_env_file=None, deepseek_api_key="test-key")

    assert settings.agent_planner_max_tokens == 3000
