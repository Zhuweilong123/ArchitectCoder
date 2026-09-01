from app.core.config import Settings


def test_settings_accepts_deprecated_sub_agent_model_without_routing(monkeypatch):
    monkeypatch.setenv("SUB_AGENT_MODEL", "legacy-flash-model")

    settings = Settings(_env_file=None, deepseek_api_key="test-key")

    assert settings.deepseek_model
    assert settings.legacy_sub_agent_model == "legacy-flash-model"
