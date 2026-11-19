"""The audio modality plugin: WAV sound clips with one label per clip.

Everything clip-specific about a sound project lives here: which WAV files are accepted (and
what their metadata says), and how a clip label is checked. The messages are plain language on
purpose; the view names the file and refuses the whole batch when one is bad.

Only uncompressed PCM WAV is accepted (the RIFF kind the standard library's ``wave`` module
reads) - not MP3, FLAC or other compressed formats, whose decoders are their own dependency.
The ``.wave`` spelling is accepted too: it is the same format, and recorders occasionally use
that suffix.
"""

from __future__ import annotations

import io
import wave
from typing import Any

from django.core.exceptions import ValidationError

from .base import ModalityPlugin, check_not_empty, digest_upload

MAX_AUDIO_BYTES = 20 * 1024 * 1024
#: Sample rates the backend's feature step is tuned for. Below 8 kHz the mel bands cannot tell
#: machine hums apart; above 48 kHz is recording-studio territory a fault detector never needs.
MIN_SAMPLE_RATE = 8_000
MAX_SAMPLE_RATE = 48_000
#: 8, 16, 24 or 32-bit PCM. The training backend reads all four; anything else the ``wave``
#: module would refuse anyway.
SAMPLE_WIDTHS = {1, 2, 3, 4}
#: One clip is one classification example. Below 0.25 s there is not enough of a window for the
#: mel-spectrogram's frequency bins to settle (a 1024-sample FFT is already 64 ms); above 60 s
#: a clip stops being "one sound event" and should be split before uploading.
MIN_DURATION_SECONDS = 0.25
MAX_DURATION_SECONDS = 60.0
AUDIO_EXTENSIONS = {".wav", ".wave"}
AUDIO_CONTENT_TYPE = "audio/wav"
#: What the asset list and operator sentences call these files.
ASSET_NOUN = "sound clip"


def safe_filename(upload_name: str) -> str:
    basename = (upload_name or "clip").replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join(character for character in basename if character.isprintable()).strip()
    return cleaned[:255] or "clip"


class AudioModalityPlugin(ModalityPlugin):
    modality = "audio"
    asset_noun = ASSET_NOUN
    annotation_kind = "clip_labels"

    def validate_upload(self, upload) -> dict[str, Any]:
        check_not_empty(upload, "Sound clips")
        if upload.size > MAX_AUDIO_BYTES:
            raise ValidationError("Each sound clip must be 20 MB or smaller.")

        name = (upload.name or "").rsplit("/", 1)[-1]
        suffix = name.lower().rsplit(".", 1)[-1] if "." in name else ""
        if f".{suffix}" not in AUDIO_EXTENSIONS:
            raise ValidationError("Only WAV sound clips are supported.")

        sha256, size_bytes = digest_upload(upload, max_bytes=MAX_AUDIO_BYTES,
                                           limit_message="Each sound clip must be 20 MB or smaller.")

        try:
            with wave.open(io.BytesIO(upload.read()), "rb") as clip:
                channels = clip.getnchannels()
                sample_width = clip.getsampwidth()
                sample_rate = clip.getframerate()
                frames = clip.getnframes()
        except (wave.Error, EOFError, ValueError) as error:
            raise ValidationError("The uploaded file is not a valid WAV sound clip.") from error
        finally:
            upload.seek(0)

        if sample_rate < MIN_SAMPLE_RATE or sample_rate > MAX_SAMPLE_RATE:
            raise ValidationError(
                f"This sound clip was recorded at {sample_rate:,} Hz; "
                "clips must be between 8,000 and 48,000 Hz."
            )
        if channels not in (1, 2):
            raise ValidationError(
                f"This sound clip has {channels} channels; only mono or stereo clips are supported."
            )
        if sample_width not in SAMPLE_WIDTHS:
            raise ValidationError(
                f"This sound clip uses {sample_width * 8}-bit audio; "
                "only 8, 16, 24 or 32-bit clips are supported."
            )
        duration = frames / sample_rate if sample_rate else 0.0
        if frames <= 0 or duration < MIN_DURATION_SECONDS or duration > MAX_DURATION_SECONDS:
            raise ValidationError(
                f"This sound clip is {duration:.2f} seconds; "
                "clips must be between 0.25 and 60 seconds."
            )

        return {
            "filename": safe_filename(upload.name),
            "sha256": sha256,
            "size_bytes": size_bytes,
            "extension": "wav",
            "content_type": AUDIO_CONTENT_TYPE,
            "duration_seconds": round(duration, 3),
            "sample_rate": sample_rate,
            "channels": channels,
            "bits_per_sample": sample_width * 8,
        }

    def validate_annotation(self, payload: dict[str, Any], project) -> dict[str, Any]:
        if not isinstance(payload, dict) or set(payload) - {"label"}:
            raise ValidationError("A clip label needs exactly one label.")
        label = payload.get("label")
        if not isinstance(label, str) or label not in project.labels:
            raise ValidationError("Choose a label defined in this project.")
        return {"label": label}
