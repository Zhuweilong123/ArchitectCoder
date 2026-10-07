"""The second-turn oracle accepts method injection and rejects ignored seeds."""
import json
from pathlib import Path
import runpy
import sys
from types import ModuleType

import numpy as np
import pytest


@pytest.mark.parametrize("ignore_seed", [False, True])
def test_method_seed_oracle_validates_behavior(monkeypatch, ignore_seed):
    class NoiseAdder:
        def addNoise(self, signal, SNR, seed=None):
            rng = np.random.default_rng(None if ignore_seed else seed)
            return signal + rng.normal(size=signal.shape)

    package = ModuleType("radar_sim")
    module = ModuleType("radar_sim.echo")
    module.NoiseAdder = NoiseAdder
    monkeypatch.setitem(sys.modules, "radar_sim", package)
    monkeypatch.setitem(sys.modules, "radar_sim.echo", module)
    root = Path(__file__).resolve().parents[2] / "evals"
    case = json.loads((root / "cases/radar_multiturn_greeting_revision_001.json").read_text(encoding="utf-8"))
    second = case["turns"][1]["hard_checkers"][0]
    third = case["turns"][2]["hard_checkers"][0]
    assert second["path"] != third["path"]
    oracle = runpy.run_path(str(root / "hidden_tests" / second["path"]))
    check = oracle["test_method_seed_is_reproducible_and_legacy_call_survives"]
    if ignore_seed:
        with pytest.raises(AssertionError):
            check()
    else:
        check()
