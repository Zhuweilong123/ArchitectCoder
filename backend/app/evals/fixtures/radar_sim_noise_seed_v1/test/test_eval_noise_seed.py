"""Evaluation contract: seeded noise generation is reproducible."""

import numpy as np

from radar_sim.echo import NoiseAdder


def test_noise_adder_seed_reproduces_output():
    signal = np.ones(256, dtype=np.complex128)
    first = NoiseAdder(seed=123).addNoise(signal, SNR=10.0)
    second = NoiseAdder(seed=123).addNoise(signal, SNR=10.0)
    assert np.array_equal(first, second)
