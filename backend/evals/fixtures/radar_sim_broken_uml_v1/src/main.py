"""End-to-end demo of the radar signal processing flow.

This script follows the "Full Radar Signal Processing Flow" sequence diagram
from the UML design, at the sequence's Long-Range settings:

* switch to ``ModeEnum.LONG_RANGE`` and read PRT / pulse width
* generate the transmit pulse train
* build the matched filter from the transmit signal
* inject targets, generate echo, compress and print detected ranges
"""

import numpy as np

from radar_sim import ModeEnum, Target
from radar_sim.echo import EchoSimulator
from radar_sim.mode_control import ModeController
from radar_sim.pulse_compression import PulseCompressor
from radar_sim.transmit import TransmitCoordinator

#: Sampling rate (Hz). PRT * sampleRate stays integer so the PRT window holds
#: an exact number of samples, keeping the transmitted signal periodic.
SAMPLE_RATE = 10.0e6


def run() -> None:
    np.random.seed(20260730)

    # 1. ModeControl: switch to LongRange and read the waveform timing.
    mode_controller = ModeController()
    mode_controller.setMode(ModeEnum.LONG_RANGE)
    params = mode_controller.getCurrentParams()
    print(f"[ModeControl] mode={mode_controller.currentMode.name}")
    print(f"[ModeControl] params={params}")

    # 2. TransmitWaveformGen: generate the transmit pulse train.
    tx_coordinator = TransmitCoordinator()
    tx_signal = tx_coordinator.generateTransmitSignal(
        prt=params.prt,
        pulseWidth=params.pulse_width,
        sampleRate=SAMPLE_RATE,
    )
    print(f"[TransmitWaveformGen] tx_signal: {len(tx_signal)} samples "
          f"({len(tx_signal) / SAMPLE_RATE * 1e3:.2f} ms)")

    # 3. PulseCompression: build the matched filter from the transmit signal.
    pulse_compressor = PulseCompressor()
    pulse_compressor.buildFilter(tx_signal)
    print("[PulseCompression] matched filter built")

    # 4. EchoSimulation: inject targets and generate the echo.
    distances_km = [5.0, 25.0, 150.0]
    echo_simulator = EchoSimulator()
    echo_simulator.setTargets([Target(distance=d * 1e3) for d in distances_km])
    echo_signal = echo_simulator.generateEcho(
        txSignal=tx_signal,
        SNR=20.0,
        sampleRate=SAMPLE_RATE,
    )
    print(f"[EchoSimulation] echo: {len(echo_signal)} samples")

    # 5. PulseCompression: compress and detect peaks.
    range_profile = pulse_compressor.compress(echo_signal)
    peaks = pulse_compressor.detector.detect(
        rangeProfile=range_profile,
        sampleRate=SAMPLE_RATE,
    )
    print(f"[PulseCompression] detected {len(peaks)} peak(s)")
    for peak in peaks:
        print(f"  range = {peak.range_m / 1e3:8.2f} km   "
              f"amplitude = {peak.amplitude:10.2f}   index = {peak.index}")

    if not peaks:
        print("No peaks detected.")


if __name__ == "__main__":
    run()
