import numpy as np

from radar_sim.echo import NoiseAdder


def test_noise_adder_seed_is_reproducible_and_legacy_call_survives():
    signal = np.ones(256, dtype=np.complex128)
    first = NoiseAdder(seed=123).addNoise(signal, SNR=10.0)
    second = NoiseAdder(seed=123).addNoise(signal, SNR=10.0)
    different = NoiseAdder(seed=124).addNoise(signal, SNR=10.0)

    assert np.array_equal(first, second)
    assert not np.array_equal(first, different)
    assert NoiseAdder().addNoise(signal, SNR=10.0).shape == signal.shape
