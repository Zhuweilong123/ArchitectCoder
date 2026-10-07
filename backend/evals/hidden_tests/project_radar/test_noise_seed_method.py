"""Second-turn oracle: seed is supplied through the method argument."""
import numpy as np

from radar_sim.echo import NoiseAdder


def test_method_seed_is_reproducible_and_legacy_call_survives():
    signal = np.ones(256, dtype=np.complex128)
    first = NoiseAdder().addNoise(signal, SNR=10.0, seed=123)
    second = NoiseAdder().addNoise(signal, SNR=10.0, seed=123)
    different = NoiseAdder().addNoise(signal, SNR=10.0, seed=124)

    assert np.array_equal(first, second)
    assert not np.array_equal(first, different)
    assert first.shape == signal.shape
    assert not np.array_equal(first, signal)
    assert NoiseAdder().addNoise(signal, SNR=10.0).shape == signal.shape
