"""Tests for the TransmitWaveformGen component (ITransmitSignal)."""

import numpy as np
import pytest

from radar_sim.transmit import (
    PulseSequencer,
    TransmitCoordinator,
    WaveformGenerator,
)


class TestWaveformGenerator:
    def test_pulse_length_matches_pulse_width(self):
        pulse = WaveformGenerator().generateLFMPulse(
            pulseWidth=50e-6, sampleRate=10e6
        )
        assert pulse.shape == (500,)
        assert np.iscomplexobj(pulse)

    def test_pulse_has_unit_envelope(self):
        pulse = WaveformGenerator().generateLFMPulse(
            pulseWidth=50e-6, sampleRate=10e6
        )
        assert np.allclose(np.abs(pulse), 1.0)

    def test_chirp_frequency_sweeps_positive(self):
        pulse = WaveformGenerator().generateLFMPulse(
            pulseWidth=50e-6, sampleRate=10e6
        )
        phase = np.unwrap(np.angle(pulse))
        # LFM with positive chirp rate: instantaneous frequency increases.
        inst_freq = np.diff(phase) / (2 * np.pi) * 10e6
        assert inst_freq[0] < inst_freq[-1]

    def test_rejects_non_positive_params(self):
        gen = WaveformGenerator()
        with pytest.raises(ValueError):
            gen.generateLFMPulse(pulseWidth=0.0, sampleRate=10e6)
        with pytest.raises(ValueError):
            gen.generateLFMPulse(pulseWidth=50e-6, sampleRate=0.0)
        with pytest.raises(ValueError):
            gen.generateLFMPulse(pulseWidth=1e-9, sampleRate=10e6)


class TestPulseSequencer:
    def test_zero_pads_to_prt_window(self):
        pulse = np.ones(50, dtype=np.complex128)
        seq = PulseSequencer().assemblePulseSequence(
            pulse=pulse, PRT=500e-6, sampleRate=1e5
        )
        assert seq.shape == (50,)
        # 50 samples at 1e5 Hz == 500 us PRT window; pulse fills it exactly.
        assert np.all(seq == 1.0)

    def test_prt_longer_than_pulse_leaves_trailing_zeros(self):
        pulse = np.ones(50, dtype=np.complex128)
        seq = PulseSequencer().assemblePulseSequence(
            pulse=pulse, PRT=1000e-6, sampleRate=1e5
        )
        assert seq.shape == (100,)
        assert np.all(seq[:50] == 1.0)
        assert np.all(seq[50:] == 0.0)

    def test_rejects_prt_shorter_than_pulse(self):
        pulse = np.ones(50, dtype=np.complex128)
        with pytest.raises(ValueError):
            PulseSequencer().assemblePulseSequence(
                pulse=pulse, PRT=10e-6, sampleRate=1e5
            )


class TestTransmitCoordinator:
    @pytest.fixture
    def tx(self):
        return TransmitCoordinator()

    def test_signal_length_equals_prt_window(self):
        tx = TransmitCoordinator()
        prt, pulse_width, fs = 3e-3, 100e-6, 10e6
        signal = tx.generateTransmitSignal(prt, pulse_width, fs)
        assert signal.shape == (int(round(prt * fs)),)

    def test_only_pulse_region_is_nonzero(self):
        tx = TransmitCoordinator()
        signal = tx.generateTransmitSignal(3e-3, 100e-6, 10e6)
        n_pulse = int(round(100e-6 * 10e6))
        assert np.count_nonzero(signal) == n_pulse
        assert np.all(signal[n_pulse:] == 0.0)

    def test_range_resolution_matches_bandwidth(self):
        tx = TransmitCoordinator()
        # Fixed 2 MHz LFM bandwidth -> c / (2 * 2e6) = 74.95 m
        res = tx.range_resolution(3e-3, 100e-6)
        assert res == pytest.approx(2.99792458e8 / (2 * 2e6), rel=1e-6)

    def test_rejects_invalid_timing(self):
        tx = TransmitCoordinator()
        with pytest.raises(ValueError):
            tx.generateTransmitSignal(0.0, 100e-6, 10e6)  # prt <= 0
        with pytest.raises(ValueError):
            tx.generateTransmitSignal(3e-3, 3e-3, 10e6)  # pulse >= prt
