"""ModeControl component (IModeControl).

Implements the "ModeControl Domain Model" class diagram:

* ``ModeController`` provides the mode switching entry point and coordinates
  parameter lookup through its ``ModeParamTable`` (association ``lookup``).
"""

from __future__ import annotations

from radar_sim.common import ModeEnum, ModeParams


class ModeParamTable:
    """Parameter lookup table for near / medium / far range modes.

    Class diagram attributes ``shortRangePRT`` ... ``longRangePulseWidth``
    are captured as a per-mode mapping inside :meth:`getParams`.
    """

    def getParams(self, mode: ModeEnum) -> ModeParams:
        if not isinstance(mode, ModeEnum):
            raise ValueError(f"mode must be a ModeEnum, got {mode!r}")
        return ModeParams(prt=mode.prt, pulse_width=mode.pulse_width)


class ModeController:
    """Mode switching entry point that coordinates parameter lookup.

    ``currentMode`` holds the active :class:`~radar_sim.common.ModeEnum`;
    ``paramsTable`` is the :class:`ModeParamTable` backing the ``lookup``
    association shown in the class diagram.
    """

    def __init__(self, params_table: ModeParamTable | None = None) -> None:
        self.currentMode: ModeEnum = ModeEnum.SHORT_RANGE
        self.paramsTable: ModeParamTable = (
            params_table if params_table is not None else ModeParamTable()
        )

    def setMode(self, mode: ModeEnum) -> None:
        if not isinstance(mode, ModeEnum):
            raise ValueError(f"mode must be a ModeEnum, got {mode!r}")
        self.currentMode = mode

    def getCurrentParams(self) -> ModeParams:
        return self.paramsTable.getParams(self.currentMode)
