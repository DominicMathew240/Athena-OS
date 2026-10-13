# SPDX-License-Identifier: Apache-2.0
"""Reference adapter: classical-DSP acoustic noise analysis behind the
Athena `ModelAdapter` interface.

Unlike `yolo_adapter.py` (which wraps a pretrained third-party model), this
adapter wraps no model at all — it is a from-scratch, fully inspectable
signal-processing implementation (frame energy + a percentile noise-floor
estimate, the same idea classic noise-gate/spectral-gating tools use). It
exists to prove two things at once: the `ModelAdapter` interface is
modality-agnostic (this one declares `Modality.SENSOR_TIMESERIES`, not an
image modality), and a non-technical user does not need a trained model or
downloaded weights to get a working reference adapter — read the algorithm
below, swap in your own trained classifier's `.predict` call where noted,
and you have a real one.

`numpy` is an optional dependency of this example, not of `athena-sdk`.
Importing this module is safe even without it installed; instantiating
`AudioNoiseAnalysisAdapter` raises a clear `ImportError` if it is missing.
WAV decoding uses only the Python standard library (`wave`), so no audio
library beyond `numpy` is required.
"""

from __future__ import annotations

import wave
from datetime import datetime, timezone

from athena_sdk.contracts import AssetStateFinding, Modality, Observation, Severity
from athena_sdk.interfaces.adapter import ModelAdapter
from athena_sdk.interfaces.capability import (
    AdapterCapabilities,
    AdapterInterfaceVersion,
)

try:
    import numpy as np
except ImportError:  # pragma: no cover - exercised via optional-dependency skip
    np = None  # type: ignore[assignment]

#: Frame size for short-time energy analysis, in samples. 1024 samples at a
#: typical 16-48kHz sample rate is a standard, uncontroversial window for
#: this kind of coarse noise-floor estimate.
_FRAME_SIZE = 1024

#: The clip must be long enough to yield at least this many frames at
#: `_FRAME_SIZE` before that fixed size is used; otherwise the frame size
#: shrinks (see `_frame_size_for`) so a short clip still gets a meaningful
#: multi-frame noise-floor estimate.
_MIN_FRAMES = 8

#: Frame size never shrinks below this, even for a very short clip.
_MIN_FRAME_SIZE = 32

#: Frames at or below this energy percentile are assumed to be
#: background/noise-only (i.e. between whatever signal of interest the
#: recording captures) -- the classic percentile noise-floor heuristic.
_NOISE_FLOOR_PERCENTILE = 10

#: Below this estimated signal-to-noise ratio, the recording's signal is
#: judged to be masked by noise rather than merely noisy.
_LOW_SNR_DB_THRESHOLD = 6.0

#: Above this estimated noise-floor level (dBFS, i.e. relative to full
#: scale), background noise itself is judged elevated even if a signal is
#: still discernible above it.
_ELEVATED_NOISE_FLOOR_DB_THRESHOLD = -30.0

_EPS = 1e-12


class AudioNoiseAnalysisAdapter(ModelAdapter):
    """`ModelAdapter` wrapper around a classical noise-floor/SNR estimate.

    Reads one mono or multi-channel PCM WAV file per `Observation.source_uri`,
    estimates its background noise floor and signal-to-noise ratio via
    short-time frame energy, and classifies the result into a small,
    fixed label taxonomy (`nominal` / `elevated_noise_floor` /
    `signal_masked_by_noise`). Designed to model an industrial
    acoustic-condition-monitoring use case (e.g. flagging a machine
    recording whose background noise floor has risen, or whose signal of
    interest is no longer distinguishable from noise) without requiring any
    trained model or downloaded weights.

    This is a reference algorithm, not a production noise-cancellation
    system: it makes no attempt to actually remove noise from the signal,
    only to characterize it. A real adapter following this same pattern
    would replace `_analyze` with a call into your own trained
    classifier/regressor.
    """

    ADAPTER_NAME = "audio-noise-analysis-reference"

    def __init__(
        self,
        *,
        adapter_version: str = "0.1.0",
        low_snr_db_threshold: float = _LOW_SNR_DB_THRESHOLD,
        elevated_noise_floor_db_threshold: float = _ELEVATED_NOISE_FLOOR_DB_THRESHOLD,
    ) -> None:
        if np is None:
            raise ImportError(
                "The 'numpy' package is required to use AudioNoiseAnalysisAdapter. "
                "Install it with `pip install numpy`."
            )
        self._adapter_version = adapter_version
        self._low_snr_db_threshold = low_snr_db_threshold
        self._elevated_noise_floor_db_threshold = elevated_noise_floor_db_threshold

    @property
    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            interface_version=AdapterInterfaceVersion.V1,
            adapter_name=self.ADAPTER_NAME,
            adapter_version=self._adapter_version,
            task_type="acoustic_noise_analysis",
            modalities=[Modality.SENSOR_TIMESERIES],
            supports_batch=False,
            labels=["nominal", "elevated_noise_floor", "signal_masked_by_noise"],
        )

    def predict(self, observations: list[Observation]) -> list[AssetStateFinding]:
        if not observations:
            return []
        return [self._analyze(observation) for observation in observations]

    def _analyze(self, observation: Observation) -> AssetStateFinding:
        samples = _read_wav_mono(observation.source_uri)
        noise_floor_db, signal_db, snr_db = _estimate_noise_floor_and_snr(samples)

        label, confidence, severity = self._classify(noise_floor_db, snr_db)

        return AssetStateFinding(
            finding_id=f"{observation.observation_id}-noise-analysis",
            asset_id=observation.metadata.get("asset_id", "unknown"),
            observation_ids=[observation.observation_id],
            model_id=self.ADAPTER_NAME,
            label=label,
            confidence=confidence,
            severity=severity,
            is_unknown_state=False,
            created_at=datetime.now(timezone.utc),
            metadata={
                "noise_floor_db": round(noise_floor_db, 2),
                "signal_level_db": round(signal_db, 2),
                "estimated_snr_db": round(snr_db, 2),
            },
        )

    def _classify(
        self, noise_floor_db: float, snr_db: float
    ) -> tuple[str, float, Severity]:
        if snr_db < self._low_snr_db_threshold:
            deficit = self._low_snr_db_threshold - snr_db
            confidence = min(1.0, 0.5 + deficit / 20.0)
            return "signal_masked_by_noise", confidence, Severity.HIGH
        if noise_floor_db > self._elevated_noise_floor_db_threshold:
            excess = noise_floor_db - self._elevated_noise_floor_db_threshold
            confidence = min(1.0, 0.5 + excess / 20.0)
            return "elevated_noise_floor", confidence, Severity.MEDIUM
        return "nominal", 0.9, Severity.INFO

    def health(self) -> bool:
        return np is not None


def _read_wav_mono(path: str):
    """Read a PCM WAV file into a mono `float32` array scaled to [-1, 1].

    Multi-channel input is averaged down to mono. Only integer PCM sample
    widths (8/16/24/32-bit) are supported, matching what `wave` itself can
    decode -- this is a reference example, not a general audio-format
    decoder.
    """
    with wave.open(path, "rb") as wav_file:
        n_channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        n_frames = wav_file.getnframes()
        raw = wav_file.readframes(n_frames)

    dtype_by_width = {1: np.uint8, 2: np.int16, 4: np.int32}
    if sample_width == 3:
        # No native numpy dtype for 24-bit PCM: unpack each little-endian
        # 3-byte sample into an int32, then sign-extend from bit 23.
        raw_bytes = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        unsigned = raw_bytes[:, 0] | (raw_bytes[:, 1] << 8) | (raw_bytes[:, 2] << 16)
        samples = np.where(unsigned >= (1 << 23), unsigned - (1 << 24), unsigned).astype(np.float32)
        full_scale = float(2**23)
    elif sample_width in dtype_by_width:
        samples = np.frombuffer(raw, dtype=dtype_by_width[sample_width])
        if sample_width == 1:
            # 8-bit WAV PCM is unsigned; center it before normalizing.
            samples = samples.astype(np.float32) - 128.0
            full_scale = 128.0
        else:
            samples = samples.astype(np.float32)
            full_scale = float(2 ** (8 * sample_width - 1))
    else:
        raise ValueError(f"Unsupported WAV sample width: {sample_width} bytes")

    if n_channels > 1:
        samples = samples.reshape(-1, n_channels).mean(axis=1)

    return samples / full_scale


def _frame_size_for(n_samples: int) -> int:
    """Pick the analysis frame size for a clip of `n_samples`.

    Fixed at `_FRAME_SIZE` once the clip is long enough to yield at least
    `_MIN_FRAMES` frames at that size. For a shorter clip, the frame size
    shrinks proportionally (floored at `_MIN_FRAME_SIZE`) so the clip still
    yields several frames instead of collapsing to a single one.

    A single frame is a real bug, not just a coarser estimate: with only one
    frame, the `_NOISE_FLOOR_PERCENTILE`-th percentile and the median in
    `_estimate_noise_floor_and_snr` are both that same one frame's energy,
    which forces `snr_db` to exactly `0.0` regardless of what the clip
    actually contains -- and `0.0` is always below `_LOW_SNR_DB_THRESHOLD`,
    so every short clip would silently and confidently classify as
    `signal_masked_by_noise` no matter how clean it is.
    """
    if n_samples >= _FRAME_SIZE * _MIN_FRAMES:
        return _FRAME_SIZE
    return max(_MIN_FRAME_SIZE, n_samples // _MIN_FRAMES)


def _estimate_noise_floor_and_snr(samples) -> tuple[float, float, float]:
    """Return `(noise_floor_db, signal_level_db, snr_db)` for `samples`.

    Splits `samples` into overlapping frames (75% overlap, i.e. hop size
    `frame_size // 4`) and computes each frame's RMS energy. The overlap
    matters: a signal that is only intermittently present (e.g. a machine
    that runs in bursts) has genuine noise-only stretches between bursts,
    but a *non-overlapping* fixed frame grid can straddle every burst
    boundary depending on phase and never land a frame fully inside a quiet
    stretch. Sliding the frame in small hops makes that near-certain instead
    of phase-dependent. The `_NOISE_FLOOR_PERCENTILE`-th percentile of frame
    energies is treated as the noise floor (the quietest frames are assumed
    to carry no signal of interest), and the median frame energy as the
    overall signal level. `snr_db` is simply the dB difference between the
    two -- a coarse but standard estimate, not a psychoacoustic model.
    """
    frame_size = _frame_size_for(len(samples))
    hop = max(1, frame_size // 4)
    if len(samples) < frame_size:
        frames = samples.reshape(1, -1)
    else:
        n_frames = (len(samples) - frame_size) // hop + 1
        frames = np.lib.stride_tricks.sliding_window_view(samples, frame_size)[::hop][:n_frames]

    frame_rms = np.sqrt(np.mean(np.square(frames), axis=1) + _EPS)
    noise_floor_rms = np.percentile(frame_rms, _NOISE_FLOOR_PERCENTILE)
    signal_rms = np.median(frame_rms)

    noise_floor_db = 20.0 * np.log10(noise_floor_rms + _EPS)
    signal_db = 20.0 * np.log10(signal_rms + _EPS)
    snr_db = signal_db - noise_floor_db
    return float(noise_floor_db), float(signal_db), float(snr_db)


__all__ = ["AudioNoiseAnalysisAdapter"]
