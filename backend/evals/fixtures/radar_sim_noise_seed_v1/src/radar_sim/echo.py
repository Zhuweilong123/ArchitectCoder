"""EchoSimulation component (IEchoSignal).

Implements the "EchoSimulation Domain Model" class diagram:

* ``EchoSimulator`` is the echo generation coordinator, combining delay and
  noise. Its attributes ``sceneManager``, ``delayProcessor`` and ``noiseAdder``
  hold the three collaborators below.
* ``TargetSceneManager`` maintains a distance/RCS list and computes delays.
* ``DelayProcessor`` performs fractional-sample delay shifting.
* ``NoiseAdder`` overlays complex Gaussian white noise at a given SNR.
"""

from __future__ import annotations

import numpy as np

from numpy.typing import NDArray

from radar_sim.common import c, Target


class TargetSceneManager:
    """Maintains target distance and RCS lists and computes delays."""

    def __init__(self) -> None:
        self.targets: list[Target] = []

    def addTarget(self, distance: float, rcs: float = 1.0) -> None:
        self.targets.append(Target(distance=distance, rcs=rcs))

    def getTargetDelays(self, sampleRate: float) -> NDArray[np.float64]:
        """Two-way delay in sample units for each target.

        ``delay_samples = distance * 2 / c * sampleRate``.
        """
        if sampleRate <= 0:
            raise ValueError(f"sampleRate must be positive, got {sampleRate}")
        return np.array(
            [t.distance * 2.0 / c * sampleRate for t in self.targets],
            dtype=np.float64,
        )


class DelayProcessor:
    """Applies a fractional-sample delay to a complex signal."""

    def applyDelay(
        self,
        signal: NDArray[np.complex128],
        delay: float,
        sampleRate: float,
    ) -> NDArray[np.complex128]:
        if signal.size == 0:
            return signal.copy()
        if delay < 0:
            raise ValueError(f"delay must be non-negative, got {delay}")
        if sampleRate <= 0:
            raise ValueError(f"sampleRate must be positive, got {sampleRate}")

        n = signal.size
        freqs = np.fft.fftfreq(n, d=1.0 / sampleRate)
        spectrum = np.fft.fft(signal)
        phase_shift = np.exp(-2j * np.pi * freqs * delay / sampleRate)
        delayed = np.fft.ifft(spectrum * phase_shift)
        if np.iscomplexobj(signal):
            return delayed.astype(np.complex128)
        return delayed.real.astype(signal.dtype)


class NoiseAdder:
    """Overlays complex Gaussian white noise at a given SNR."""

    def addNoise(
        self, signal: NDArray[np.complex128], SNR: float
    ) -> NDArray[np.complex128]:
        if signal.size == 0:
            return signal.copy()
        signal = np.asarray(signal, dtype=np.complex128)
        power = float(np.mean(np.abs(signal) ** 2))
        if power <= 0:
            return np.zeros_like(signal)
        snr_linear = 10.0 ** (SNR / 10.0)
        noise_power = power / snr_linear
        noise = np.sqrt(noise_power / 2.0) * (
            np.random.randn(signal.size) + 1j * np.random.randn(signal.size)
        )
        return signal + noise


class EchoSimulator:
    """Echo generation coordinator: combines delay and noise per target."""

    def __init__(
        self,
        scene_manager: TargetSceneManager | None = None,
        delay_processor: DelayProcessor | None = None,
        noise_adder: NoiseAdder | None = None,
    ) -> None:
        self.sceneManager = (
            scene_manager if scene_manager is not None else TargetSceneManager()
        )
        self.delayProcessor = (
            delay_processor if delay_processor is not None else DelayProcessor()
        )
        self.noiseAdder = noise_adder if noise_adder is not None else NoiseAdder()

    def setTargets(self, targets: list[Target]) -> None:
        if not targets:
            raise ValueError("targets must not be empty")
        self.sceneManager.targets = list(targets)

    def generateEcho(
        self,
        txSignal: NDArray[np.complex128],
        SNR: float,
        sampleRate: float,
    ) -> NDArray[np.complex128]:
        if txSignal.size == 0:
            raise ValueError("txSignal must not be empty")
        if sampleRate <= 0:
            raise ValueError(f"sampleRate must be positive, got {sampleRate}")

        delays = self.sceneManager.getTargetDelays(sampleRate)
        total = np.zeros_like(np.asarray(txSignal, dtype=np.complex128))
        for target, delay in zip(self.sceneManager.targets, delays):
            delayed = self.delayProcessor.applyDelay(
                txSignal, delay, sampleRate
            )
            delayed = delayed * (target.rcs**0.5)
            total += delayed
        return self.noiseAdder.addNoise(total, SNR)
