"""Tests for the EchoSimulation component (IEchoSignal)."""

import numpy as np
import pytest

from radar_sim.common import c, Target
from radar_sim.echo import (
    DelayProcessor,
    EchoSimulator,
    NoiseAdder,
    TargetSceneManager,
)


class TestTargetSceneManager:
    def test_add_target_and_delay_conversion(self):
        scene = TargetSceneManager()
        scene.addTarget(distance=150e3, rcs=1.0)
        delays = scene.getTargetDelays(sampleRate=10e6)
        expected = 150e3 * 2.0 / c * 10e6
        assert delays.shape == (1,)
        assert delays[0] == pytest.approx(expected)

    def test_multiple_targets_preserve_order(self):
        scene = TargetSceneManager()
        for d in (5e3, 25e3, 150e3):
            scene.addTarget(distance=d, rcs=1.0)
        delays = scene.getTargetDelays(sampleRate=10e6)
        assert delays[0] < delays[1] < delays[2]
        assert np.all(np.diff(delays) > 0)


class TestDelayProcessor:
    @pytest.fixture
    def signal(self):
        # A real impulse-like burst that FFT shifting handles cleanly.
        x = np.zeros(1024, dtype=np.complex128)
        x[0] = 1.0
        return x

    def test_zero_delay_is_identity(self, signal):
        proc = DelayProcessor()
        out = proc.applyDelay(signal=signal, delay=0.0, sampleRate=1e6)
        assert np.allclose(out, signal)

    def test_integer_delay_shifts_impulse(self, signal):
        proc = DelayProcessor()
        out = proc.applyDelay(signal=signal, delay=64.0, sampleRate=1e6)
        assert np.allclose(out[64], 1.0)
        assert np.abs(out[0]) < 1e-8

    def test_fractional_delay_spreads_impulse(self, signal):
        proc = DelayProcessor()
        out = proc.applyDelay(signal=signal, delay=100.5, sampleRate=1e6)
        # Peak must land near the fractional position and stay real-ish.
        peak_idx = int(np.argmax(np.abs(out)))
        assert abs(peak_idx - 100) <= 1
        assert out[peak_idx].imag < 1e-2


class TestNoiseAdder:
    def test_noise_level_matches_snr(self):
        signal = np.ones(20000, dtype=np.complex128) * 1.0
        noisy = NoiseAdder().addNoise(signal=signal, SNR=10.0)
        measured = float(np.mean(np.abs(noisy) ** 2) / np.mean(np.abs(signal) ** 2))
        assert measured == pytest.approx(1.1, rel=0.1)

    def test_empty_signal_passthrough(self):
        empty = np.array([], dtype=np.complex128)
        out = NoiseAdder().addNoise(empty, SNR=10.0)
        assert out.shape == (0,)


class TestEchoSimulator:
    @pytest.fixture
    def tx_signal(self):
        return np.ones(1000, dtype=np.complex128)

    def test_echo_length_matches_tx(self, tx_signal):
        sim = EchoSimulator()
        sim.setTargets([Target(distance=25e3)])
        echo = sim.generateEcho(txSignal=tx_signal, SNR=30.0, sampleRate=10e6)
        assert echo.shape == tx_signal.shape

    def test_echo_conserves_energy_at_high_snr(self, tx_signal):
        sim = EchoSimulator()
        sim.setTargets([Target(distance=0.0, rcs=1.0)])
        echo = sim.generateEcho(txSignal=tx_signal, SNR=60.0, sampleRate=10e6)
        tx_power = float(np.mean(np.abs(tx_signal) ** 2))
        echo_power = float(np.mean(np.abs(echo) ** 2))
        assert echo_power == pytest.approx(tx_power, rel=0.2)

    def test_empty_targets_raises(self):
        sim = EchoSimulator()
        with pytest.raises(ValueError):
            sim.setTargets([])
