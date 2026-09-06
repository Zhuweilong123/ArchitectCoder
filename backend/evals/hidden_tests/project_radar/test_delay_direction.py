import numpy as np

from radar_sim.echo import DelayProcessor


def test_positive_integer_delay_moves_impulse_forward():
    signal = np.zeros(1024, dtype=np.complex128)
    signal[0] = 1.0

    output = DelayProcessor().applyDelay(signal, delay=64.0, sampleRate=1e6)

    assert np.abs(output[64]) > 0.9
    assert np.abs(output[0]) < 1e-8
