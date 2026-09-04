"""Tests for the central extension plugin manager."""

from types import SimpleNamespace

from app.agent_base.core.plugins import PluginManager, PluginSpec


class _DemoProvider:
    def ping(self):
        return "pong"


def test_plugin_manager_loads_custom_extension_and_records_state(monkeypatch):
    import sys

    module_name = "test_managed_extension"
    monkeypatch.setitem(
        sys.modules,
        module_name,
        SimpleNamespace(create=lambda **kwargs: _DemoProvider()),
    )
    manager = PluginManager((PluginSpec(
        name="demo",
        enabled_setting="demo_enabled",
        provider_setting="demo_provider",
        default_provider="unused:create",
        required_methods=("ping",),
    ),))
    settings = SimpleNamespace(
        demo_enabled=True,
        demo_provider=f"{module_name}:create",
    )

    provider = manager.load("demo", settings=settings)

    assert provider.ping() == "pong"
    assert manager.status() == [{
        "name": "demo",
        "provider": f"{module_name}:create",
        "status": "loaded",
        "error": "",
    }]


def test_plugin_manager_marks_disabled_slot_without_importing_provider():
    manager = PluginManager((PluginSpec(
        name="disabled",
        enabled_setting="demo_enabled",
        provider_setting="demo_provider",
        default_provider="missing.module:create",
        required_methods=("ping",),
    ),))

    assert manager.load(
        "disabled",
        settings=SimpleNamespace(
            demo_enabled=False,
            demo_provider="missing.module:create",
        ),
    ) is None
    assert manager.status()[0]["status"] == "disabled"
