from radar_sim.legacy_debug_probe import LegacyDebugProbe


def test_legacy_debug_probe_emits_message():
    assert LegacyDebugProbe().emit("ok") == "[legacy] ok"
