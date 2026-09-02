"""TransmitWaveformGen component (ITransmitSignal).

Implements the "TransmitWaveformGen Domain Model" class diagram:

* ``TransmitCoordinator`` is the component facade that receives timing
  parameters and coordinates generation (``generateTransmitSignal``).
* ``WaveformGenerator`` produces a single linear-FM pulse's I/Q complex data.
* ``PulseSequencer`` zero-pads a single pulse to a full PRT interval.
"""

from __future__ import annotations

import numpy as np

from numpy.typing import NDArray

from radar_sim.common import c

#: Nominal radar centre frequency (L-band, Hertz).
FC: float = 1.0e9

#: LFM sweep bandwidth (Hertz). A fixed system constant chosen well within
#: the sampling Nyquist band; with ``pulse_width * LFM_BANDWIDTH >> 1`` the
#: matched filter compresses each pulse into a narrow main lobe.
LFM_BANDWIDTH: float = 2.0e6


def _as_complex(samples: NDArray[np.float64]) -> NDArray[np.complex128]:
    return samples.astype(np.complex128)


class WaveformGenerator:
    """Generates a single linear-FM (chirp) pulse's I/Q complex data."""

    def generateLFMPulse(self, pulseWidth: float, sampleRate: float) -> NDArray[np.complex128]:
        if pulseWidth <= 0:
            raise ValueError(f"pulseWidth must be positive, got {pulseWidth}")
        if sampleRate <= 0:
            raise ValueError(f"sampleRate must be positive, got {sampleRate}")
        num_samples = int(round(pulseWidth * sampleRate))
        if num_samples < 1:
            raise ValueError(
                f"pulseWidth * sampleRate must be >= 1 sample, got {num_samples}"
            )
        times = np.arange(num_samples, dtype=np.float64) / sampleRate
        k = LFM_BANDWIDTH / pulseWidth
        phase = 2.0 * np.pi * (FC * times + 0.5 * k * times**2)
        return np.exp(1j * phase)


class PulseSequencer:
    """Zero-pads a single pulse into a full PRT pulse train."""

    def assemblePulseSequence(
        self,
        pulse: NDArray[np.complex128],
        PRT: float,
        sampleRate: float,
    ) -> NDArray[np.complex128]:
        if PRT <= 0:
            raise ValueError(f"PRT must be positive, got {PRT}")
        if sampleRate <= 0:
            raise ValueError(f"sampleRate must be positive, got {sampleRate}")
        period_samples = int(round(PRT * sampleRate))
        if period_samples < pulse.size:
            raise ValueError(
                f"PRT window ({period_samples} samples) is shorter than the "
                f"pulse ({pulse.size} samples)"
            )
        sequence = np.zeros(period_samples, dtype=np.complex128)
        sequence[-pulse.size:] = pulse
        return sequence


class TransmitCoordinator:
    """Component facade: receives timing parameters and coordinates generation."""

    def __init__(
        self,
        waveform_generator: WaveformGenerator | None = None,
        pulse_sequencer: PulseSequencer | None = None,
    ) -> None:
        self.waveformGenerator = waveform_generator if waveform_generator is not None else WaveformGenerator()
        self.pulseSequencer = pulse_sequencer if pulse_sequencer is not None else PulseSequencer()

    def generateTransmitSignal(
        self,
        prt: float,
        pulseWidth: float,
        sampleRate: float,
    ) -> NDArray[np.complex128]:
        if prt <= 0:
            raise ValueError(f"prt must be positive, got {prt}")
        if pulseWidth <= 0:
            raise ValueError(f"pulseWidth must be positive, got {pulseWidth}")
        if pulseWidth >= prt:
            raise ValueError(f"pulseWidth ({pulseWidth}) must be smaller than prt ({prt})")
        if sampleRate <= 0:
            raise ValueError(f"sampleRate must be positive, got {sampleRate}")
        pulse = self.waveformGenerator.generateLFMPulse(pulseWidth, sampleRate)
        return self.pulseSequencer.assemblePulseSequence(pulse, prt, sampleRate)

    def range_resolution(self, prt: float, pulseWidth: float) -> float:
        """Physical range resolution in metres for the given mode timing."""
        return c / (2.0 * LFM_BANDWIDTH)
