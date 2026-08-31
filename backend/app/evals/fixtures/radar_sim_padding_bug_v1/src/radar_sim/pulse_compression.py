"""PulseCompression component (IRangeProfile).

Implements the "PulseCompression Domain Model" class diagram:

* ``PulseCompressor`` coordinates matched filtering and peak detection and
  outputs the range profile. Its attributes ``filterCoefficients``,
  ``builder`` and ``detector`` hold the collaborators below.
* ``MatchedFilterBuilder`` FFT-conjugates a reference signal to produce the
  frequency-domain matched coefficients.
* ``PeakDetector`` searches peaks and converts them to physical ranges.
"""

from __future__ import annotations

import numpy as np

from numpy.typing import NDArray

from radar_sim.common import c, Peak


class MatchedFilterBuilder:
    """Builds frequency-domain matched coefficients from a reference signal."""

    def buildFilter(
        self, refSignal: NDArray[np.complex128]
    ) -> NDArray[np.complex128]:
        ref = np.asarray(refSignal, dtype=np.complex128)
        if ref.size == 0:
            raise ValueError("refSignal must not be empty")
        return np.conj(np.fft.fft(ref))


class PeakDetector:
    """Searches peaks in the range profile and converts them to distances."""

    def detect(
        self,
        rangeProfile: NDArray[np.float64],
        sampleRate: float,
        rangeOffset: float = 0.0,
        snrThreshold: float = 6.0,
    ) -> list[Peak]:
        profile = np.asarray(rangeProfile, dtype=np.float64)
        if profile.size < 3:
            return []
        if sampleRate <= 0:
            raise ValueError(f"sampleRate must be positive, got {sampleRate}")

        amplitude = np.abs(profile)
        global_max = float(amplitude.max())
        if global_max <= 0:
            return []
        noise = float(np.median(amplitude))
        snr_linear = 10.0 ** (snrThreshold / 10.0)
        # Accept a local maximum only if it clears the noise floor scaled to
        # the requested SNR, and is a genuine main lobe rather than a
        # ~-13 dB sinc sidelobe of the matched filter.
        min_amplitude = max(0.3 * global_max, noise * snr_linear)

        peaks: list[Peak] = []
        for i in range(1, amplitude.size - 1):
            if (
                amplitude[i] > amplitude[i - 1]
                and amplitude[i] > amplitude[i + 1]
            ):
                if amplitude[i] >= min_amplitude:
                    peaks.append(
                        Peak(
                            index=i,
                            range_m=rangeOffset + float(i) * c / (2.0 * sampleRate),
                            amplitude=float(amplitude[i]),
                        )
                    )
        return peaks


class PulseCompressor:
    """Coordinates matched filtering and peak detection, outputting a range profile."""

    def __init__(
        self,
        builder: MatchedFilterBuilder | None = None,
        detector: PeakDetector | None = None,
    ) -> None:
        self.filterCoefficients: NDArray[np.complex128] | None = None
        self._reference: NDArray[np.complex128] | None = None
        self.builder = builder if builder is not None else MatchedFilterBuilder()
        self.detector = detector if detector is not None else PeakDetector()

    def buildFilter(self, refSignal: NDArray[np.complex128]) -> None:
        ref = np.asarray(refSignal, dtype=np.complex128)
        if ref.size == 0:
            raise ValueError("refSignal must not be empty")
        self._reference = ref
        self.filterCoefficients = self.builder.buildFilter(ref)

    def compress(self, echoSignal: NDArray[np.complex128]) -> NDArray[np.float64]:
        echo = np.asarray(echoSignal, dtype=np.complex128)
        if echo.size == 0:
            raise ValueError("echoSignal must not be empty")
        if self._reference is None:
            raise RuntimeError("buildFilter() must be called before compress()")
        ref = self._reference
        # Matched filter: conjugate spectrum of the reference, zero-padded to
        # the echo length so the correlation peak lands at the echo delay.
        ref_padded = np.zeros(echo.size, dtype=np.complex128)
        n_use = min(ref.size, echo.size)
        ref_padded[:n_use] = ref[:n_use]
        echo_spec = np.fft.fft(echo)
        filter_spec = np.conj(np.fft.fft(ref_padded))
        compressed = np.fft.ifft(echo_spec * filter_spec)
        return np.abs(compressed)
