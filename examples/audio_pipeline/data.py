# SPDX-License-Identifier: Apache-2.0
"""Synthetic, illustrative-only WAV generation for the audio example pipeline.

Pure standard library (`wave`, `random`, `math`, `array`) -- no audio
library or `numpy` dependency here, so generating example data never
requires an optional dependency. `numpy` remains required only to *analyze*
the generated audio (`examples/adapters/audio_noise_adapter.py`), not to
produce it.

No production or customer-derived audio appears here or anywhere else in
this package, per the Gate 1 open-source data policy.
"""

from __future__ import annotations

import math
import random
import wave
from array import array

_SAMPLE_RATE = 16_000
_DURATION_SECONDS = 1.0
_TONE_HZ = 440.0

#: The synthetic "machine" runs in bursts rather than continuously -- tone on
#: for `_TONE_ON_SECONDS`, silent (background noise only) for
#: `_TONE_OFF_SECONDS`, repeating. This mirrors real intermittent-operation
#: acoustic monitoring, and gives the noise-floor estimator actual
#: noise-only frames to measure: a continuous, unmodulated tone would make
#: every frame's energy nearly identical regardless of background noise,
#: leaving nothing for a percentile-based noise-floor estimate to find.
_TONE_ON_SECONDS = 0.15
_TONE_OFF_SECONDS = 0.10


def write_synthetic_wav(path: str, *, noisy: bool, seed: int = 0) -> None:
    """Write a mono 16-bit PCM WAV file of an intermittent tone plus noise.

    `noisy=False` produces a tone burst pattern with light background noise
    (models a nominal recording); `noisy=True` produces the same pattern
    with heavy background noise (models a degraded/noisy recording), so the
    silent gaps between tone bursts carry noise loud enough to raise the
    estimated noise floor and lower the estimated SNR. Deterministic given
    `seed`, so example runs and tests are reproducible without shipping a
    binary fixture file.
    """
    rng = random.Random(seed)
    n_samples = int(_SAMPLE_RATE * _DURATION_SECONDS)
    tone_amplitude = 0.5
    noise_amplitude = 0.4 if noisy else 0.015
    period_samples = int((_TONE_ON_SECONDS + _TONE_OFF_SECONDS) * _SAMPLE_RATE)
    on_samples = int(_TONE_ON_SECONDS * _SAMPLE_RATE)

    samples = array("h")
    for i in range(n_samples):
        tone_is_on = (i % period_samples) < on_samples
        tone = tone_amplitude * math.sin(2 * math.pi * _TONE_HZ * i / _SAMPLE_RATE) if tone_is_on else 0.0
        noise = noise_amplitude * (rng.random() * 2 - 1)
        value = max(-1.0, min(1.0, tone + noise))
        samples.append(int(value * 32767))

    with wave.open(path, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)  # 16-bit PCM
        wav_file.setframerate(_SAMPLE_RATE)
        wav_file.writeframes(samples.tobytes())


__all__ = ["write_synthetic_wav"]
