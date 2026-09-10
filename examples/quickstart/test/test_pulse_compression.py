"""Tests for the PulseCompression component (IRangeProfile)."""

import numpy as np
import pytest

from radar_sim.common import c
from radar_sim.pulse_compression import (
    MatchedFilterBuilder,
    PeakDetector,
    PulseCompressor,
)
from radar_sim.transmit import LFM_BANDWIDTH


def _lfm_pulse(pulse_width: float, sample_rate: float, fc: float = 1e9):
    n = int(round(pulse_width * sample_rate))
    t = np.arange(n) / sample_rate
    k = LFM_BANDWIDTH / pulse_width
    return np.exp(1j * 2 * np.pi * (fc * t + 0.5 * k * t**2))


class TestMatchedFilterBuilder:
    def test_builds_conjugated_spectrum(self):
        ref = np.array([1 + 1j, 2 - 1j, 3 + 2j], dtype=np.complex128)
        coeffs = MatchedFilterBuilder().buildFilter(ref)
        assert np.allclose(coeffs, np.conj(np.fft.fft(ref)))

    def test_rejects_empty_reference(self):
        with pytest.raises(ValueError):
            MatchedFilterBuilder().buildFilter(np.array([], dtype=np.complex128))


class TestPulseCompressor:
    def test_matched_filter_compresses_chirp(self):
        sample_rate = 10e6
        pulse_width = 50e-6
        pulse = _lfm_pulse(pulse_width, sample_rate)

        compressor = PulseCompressor()
        compressor.buildFilter(pulse)

        # Place the echo 400 samples after the reference.
        echo = np.zeros(2048, dtype=np.complex128)
        echo[400:400 + pulse.size] = pulse

        profile = compressor.compress(echo)
        peak = np.argmax(profile)

        # The compression peak is narrow and sits near sample 400.
        assert abs(peak - 400) <= 2
        assert profile[peak] > profile[0] * 50

    def test_compress_requires_built_filter(self):
        compressor = PulseCompressor()
        with pytest.raises(RuntimeError):
            compressor.compress(np.ones(16, dtype=np.complex128))


class TestPeakDetector:
    def test_detects_single_peak(self):
        profile = np.zeros(512)
        profile[100] = 10.0
        peaks = PeakDetector().detect(profile, sampleRate=1e6)
        assert len(peaks) == 1
        assert peaks[0].index == 100

    def test_range_conversion_uses_two_way_delay(self):
        profile = np.zeros(512)
        profile[100] = 10.0
        peaks = PeakDetector().detect(profile, sampleRate=1e6)
        expected_range = 100 * c / (2.0 * 1e6)
        assert peaks[0].range_m == pytest.approx(expected_range)

    def test_applies_range_offset(self):
        profile = np.zeros(512)
        profile[50] = 10.0
        peaks = PeakDetector().detect(profile, sampleRate=1e6, rangeOffset=1000.0)
        assert peaks[0].range_m == pytest.approx(1000.0 + 50 * c / (2.0 * 1e6))

    def test_empty_profile_returns_empty(self):
        assert PeakDetector().detect(np.array([]), sampleRate=1e6) == []


class TestEndToEnd:
    def test_detects_target_ranges(self):
        """Sequence-diagram flow: transmit -> echo -> compress -> detect.

        Targets at 5, 25 and 150 km must be detected near their true ranges.
        """
        sample_rate = 10e6
        prt, pulse_width = 3e-3, 100e-6
        tx_signal = _lfm_pulse(pulse_width, sample_rate)
        n_prt = int(round(prt * sample_rate))
        tx_train = np.zeros(n_prt, dtype=np.complex128)
        tx_train[:tx_signal.size] = tx_signal

        compressor = PulseCompressor()
        compressor.buildFilter(tx_train)

        target_km = [5.0, 25.0, 150.0]
        delay_samples = [d * 1e3 * 2.0 / c * sample_rate for d in target_km]
        echo = np.zeros(n_prt, dtype=np.complex128)
        for delay in delay_samples:
            idx = int(round(delay))
            echo[idx:idx + tx_signal.size] += tx_signal

        profile = compressor.compress(echo)
        peaks = compressor.detector.detect(profile, sampleRate=sample_rate)

        detected = sorted(p.range_m / 1e3 for p in peaks)
        assert len(detected) == 3
        assert detected[0] == pytest.approx(5.0, rel=0.02)
        assert detected[1] == pytest.approx(25.0, rel=0.02)
        assert detected[2] == pytest.approx(150.0, rel=0.02)
