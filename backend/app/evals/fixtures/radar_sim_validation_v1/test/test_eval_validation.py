"""Evaluation contract: reject non-finite radar domain parameters."""

import math

import numpy as np
import pytest

from radar_sim.common import ModeParams, Target
from radar_sim.echo import NoiseAdder


@pytest.mark.parametrize(
    "builder",
    [
        lambda: ModeParams(math.nan, 1e-6),
        lambda: ModeParams(math.inf, 1e-6),
        lambda: ModeParams(1e-3, math.nan),
        lambda: Target(math.nan),
        lambda: Target(1.0, math.nan),
    ],
)
def test_domain_objects_reject_non_finite_values(builder):
    with pytest.raises(ValueError):
        builder()


@pytest.mark.parametrize("snr", [math.nan, math.inf, -math.inf])
def test_noise_adder_rejects_non_finite_snr(snr):
    with pytest.raises(ValueError):
        NoiseAdder().addNoise(np.ones(32, dtype=np.complex128), SNR=snr)
