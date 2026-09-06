"""Shared domain types used across all four components."""

from __future__ import annotations

import enum

import numpy as np

from numpy.typing import NDArray

#: Speed of light in metres per second.
c: float = 2.99792458e8

#: A complex I/Q sample vector.
ComplexSignal = NDArray[np.complex128]


class ModeEnum(enum.Enum):
    """Radar operating modes with their PRT and pulse width parameters."""

    SHORT_RANGE = (500e-6, 10e-6)
    MEDIUM_RANGE = (1e-3, 50e-6)
    LONG_RANGE = (3e-3, 100e-6)

    def __init__(self, prt: float, pulse_width: float) -> None:
        self._prt = float(prt)
        self._pulse_width = float(pulse_width)

    @property
    def prt(self) -> float:
        return self._prt

    @property
    def pulse_width(self) -> float:
        return self._pulse_width


class ModeParams:
    """Waveform timing parameters for a mode.

    Attributes:
        prt: Pulse repetition time in seconds.
        pulse_width: Transmit pulse width in seconds.
    """

    __slots__ = ("prt", "pulse_width")

    def __init__(self, prt: float, pulse_width: float) -> None:
        if prt <= 0:
            raise ValueError(f"prt must be positive, got {prt}")
        if pulse_width <= 0 or pulse_width >= prt:
            raise ValueError(
                "pulse_width must satisfy 0 < pulse_width < prt, "
                f"got {pulse_width}"
            )
        self.prt = float(prt)
        self.pulse_width = float(pulse_width)

    def __repr__(self) -> str:
        return (
            f"ModeParams(prt={self.prt:.3g}, "
            f"pulse_width={self.pulse_width:.3g})"
        )

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, ModeParams)
            and self.prt == other.prt
            and self.pulse_width == other.pulse_width
        )


class Target:
    """A simulated point target.

    Attributes:
        distance: Range in metres.
        rcs: Radar cross section in square metres.
    """

    __slots__ = ("distance", "rcs")

    def __init__(self, distance: float, rcs: float = 1.0) -> None:
        if distance < 0:
            raise ValueError(f"distance must be non-negative, got {distance}")
        if rcs <= 0:
            raise ValueError(f"rcs must be positive, got {rcs}")
        self.distance = float(distance)
        self.rcs = float(rcs)

    def __repr__(self) -> str:
        return f"Target(distance={self.distance:.3g}, rcs={self.rcs:.3g})"


class Peak:
    """A detected target peak in the range profile.

    Attributes:
        index: Sample index in the range profile.
        range_m: Physical range in metres.
        amplitude: Peak magnitude.
    """

    __slots__ = ("index", "range_m", "amplitude")

    def __init__(self, index: int, range_m: float, amplitude: float) -> None:
        self.index = int(index)
        self.range_m = float(range_m)
        self.amplitude = float(amplitude)

    def __repr__(self) -> str:
        return (
            f"Peak(index={self.index}, range_m={self.range_m:.3g}, "
            f"amplitude={self.amplitude:.3g})"
        )


Signal = np.ndarray
