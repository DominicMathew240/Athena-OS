# SPDX-License-Identifier: Apache-2.0
"""Unit tests for `AudioNoiseAnalysisAdapter`'s internal DSP helpers.

Distinct from `test_interfaces_conformance.py::test_audio_noise_adapter_conforms_and_runs_end_to_end`,
which checks the `ModelAdapter` interface contract. These tests check the
algorithm itself: WAV sample-width decoding and short-clip handling.
"""

from __future__ import annotations

import importlib.util
import wave
from array import array

import pytest

_NUMPY_AVAILABLE = importlib.util.find_spec("numpy") is not None

pytestmark = pytest.mark.skipif(
    not _NUMPY_AVAILABLE,
    reason="numpy is an optional example dependency, not installed by default",
)


def _write_pcm_wav(path, *, sample_width: int, samples: list[int], sample_rate: int = 16_000) -> None:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(sample_rate)
        if sample_width == 3:
            raw = bytearray()
            for value in samples:
                raw += int(value).to_bytes(3, byteorder="little", signed=True)
            wav_file.writeframes(bytes(raw))
        else:
            type_code = {2: "h", 4: "i"}[sample_width]
            wav_file.writeframes(array(type_code, samples).tobytes())


def test_read_wav_mono_decodes_24_bit_pcm(tmp_path):
    from examples.adapters.audio_noise_adapter import _read_wav_mono

    max_val = 2**23 - 1
    min_val = -(2**23)
    path = tmp_path / "24bit.wav"
    _write_pcm_wav(path, sample_width=3, samples=[0, max_val, min_val, max_val // 2])

    decoded = _read_wav_mono(str(path))

    assert decoded.shape == (4,)
    assert decoded[0] == pytest.approx(0.0, abs=1e-6)
    assert decoded[1] == pytest.approx(1.0, abs=1e-6)
    assert decoded[2] == pytest.approx(-1.0, abs=1e-6)
    assert decoded[3] == pytest.approx(0.5, abs=1e-4)


def test_read_wav_mono_decodes_16_bit_pcm(tmp_path):
    from examples.adapters.audio_noise_adapter import _read_wav_mono

    path = tmp_path / "16bit.wav"
    _write_pcm_wav(path, sample_width=2, samples=[0, 32767, -32768])

    decoded = _read_wav_mono(str(path))

    assert decoded[0] == pytest.approx(0.0, abs=1e-6)
    assert decoded[1] == pytest.approx(1.0, abs=1e-4)
    assert decoded[2] == pytest.approx(-1.0, abs=1e-4)


def test_estimate_noise_floor_and_snr_short_clip_does_not_collapse_to_a_single_frame():
    """A clip shorter than one fixed-size frame must not silently force
    `snr_db == 0.0` (see `_frame_size_for`'s docstring for why that would be
    a silent-misclassification bug, not just a coarser estimate)."""
    import numpy as np

    from examples.adapters.audio_noise_adapter import _estimate_noise_floor_and_snr

    rng = np.random.default_rng(0)
    n_samples = 500  # well under the 1024-sample fixed frame size
    quiet_noise = rng.uniform(-0.01, 0.01, size=n_samples // 2)
    loud_tone = 0.8 * np.sin(np.linspace(0, 40 * np.pi, n_samples // 2))
    samples = np.concatenate([quiet_noise, loud_tone]).astype(np.float32)

    noise_floor_db, signal_db, snr_db = _estimate_noise_floor_and_snr(samples)

    assert snr_db != pytest.approx(0.0, abs=1e-6)
    assert snr_db > 6.0  # loud tone vs. quiet noise should read as a clean signal
    assert signal_db > noise_floor_db
