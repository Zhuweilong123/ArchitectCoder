"""Tests for the ModeControl component (IModeControl)."""

import pytest

from radar_sim.common import ModeEnum, ModeParams
from radar_sim.mode_control import ModeController, ModeParamTable


class TestModeParamTable:
    def test_returns_params_for_each_mode(self):
        table = ModeParamTable()
        for mode in ModeEnum:
            params = table.getParams(mode)
            assert isinstance(params, ModeParams)
            assert params.prt == mode.prt
            assert params.pulse_width == mode.pulse_width

    def test_long_range_matches_sequence_diagram_timing(self):
        params = ModeParamTable().getParams(ModeEnum.LONG_RANGE)
        assert params.prt == pytest.approx(3e-3)
        assert params.pulse_width == pytest.approx(100e-6)

    def test_rejects_non_mode_enum(self):
        table = ModeParamTable()
        with pytest.raises(ValueError):
            table.getParams("LONG_RANGE")  # type: ignore[arg-type]


class TestModeController:
    def test_defaults_to_short_range(self):
        controller = ModeController()
        assert controller.currentMode is ModeEnum.SHORT_RANGE

    def test_set_mode_changes_current_mode(self):
        controller = ModeController()
        controller.setMode(ModeEnum.LONG_RANGE)
        assert controller.currentMode is ModeEnum.LONG_RANGE

    def test_get_current_params_reflects_mode(self):
        controller = ModeController()
        controller.setMode(ModeEnum.LONG_RANGE)
        params = controller.getCurrentParams()
        assert params.prt == pytest.approx(3e-3)
        assert params.pulse_width == pytest.approx(100e-6)

    def test_set_mode_rejects_invalid_value(self):
        controller = ModeController()
        with pytest.raises(ValueError):
            controller.setMode("LONG_RANGE")  # type: ignore[arg-type]
