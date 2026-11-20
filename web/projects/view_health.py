"""View suitability and source health for recorded sources (T12).

Given one ``OperationSourceRecording`` (T09: the whole file, kept byte-for-byte
with its digest and dimensions) this module measures what can honestly be
measured from the stored bytes and states each limit plainly:

* **Coverage** — resolution against the 1920×1080 bound recordings must meet
  (the import preflight already enforces it; health re-checks the stored row
  so a view never quietly claims more than its pictures carry).
* **Day/night mix** — average luma (0–255) of evenly spaced sampled frames,
  from ffmpeg signalstats. A single daylight clip cannot prove the view works
  at night; the verdict asks for a recording that includes darker hours.
* **Frame age and gaps** — the T20 timeline's real numbers: how much
  presentation time is missing, how old the oldest gapless stretch is.
* **Obstruction (proxy)** — long stretches where sampled frames are both dark
  and nearly identical (low spread of luma over a long window) look like a
  covered lens. This is an honest *proxy*, not a detection: it cannot tell an
  obstruction from an empty night-time scene, and says so in its detail text.
* **Object size** — NOT measured. How tall a person stands in the frame
  depends on scene depth no recording can reveal, so it is a user-entered
  estimate (percent of frame height) stored on the source and shown with
  guidance; Athena only checks it against plain limits.

Every dimension gets one verdict — ``ok`` / ``review`` / ``unsuitable`` — with
the measurement, the limit, and ONE actionable next step (an installer action
or "record a test clip"). ``unsuitable`` never silently blocks anything; it
surfaces on the readiness card for a person to act on.
"""

from __future__ import annotations

import math
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings

from .source_time import build_timeline

#: The import preflight's bound; health reports the same limit in its messages.
MAX_WIDTH, MAX_HEIGHT = 1920, 1080
#: Frames sampled for brightness across the whole recording (one per window).
BRIGHTNESS_SAMPLE_WINDOWS = 10
#: Luma (0-255) at or below which a sampled frame counts as dark (night-like).
DARK_YAVG = 60
#: Sampled frames at or above this count of dark samples count as dark examples.
DARK_SAMPLE_MINIMUM = 2
#: Luma (0-255) by which two consecutive dark samples must differ to count as
#: "changing". Encoders flatten a near-black scene to the same value, so this
#: tolerance is deliberately generous; noise in a real night scene crosses it.
DARK_CHANGE_STEP = 4.0
#: A dark, near-motionless run of sampled frames this long looks like an
#: obstruction (a covered or knocked lens). 2 samples (about a fifth of the
#: recording) is only worth a review; 3 or more is called unsuitable.
OBSTRUCTION_UNSUITABLE_RUN = 3
#: Sampling and obstruction windows must finish inside this budget.
_ANALYZE_TIMEOUT_SECONDS = 120

OK = "ok"
REVIEW = "review"
UNSUITABLE = "unsuitable"

_INSTALLER_NEXT_STEP = (
    "Ask the installer to re-aim or clean this camera, then record a test clip."
)
_RECORD_TEST_CLIP = "Record a test clip from this source and check it here."


class ViewHealthError(ValueError):
    """A recording's view health could not be measured, with a plain-language reason."""

    def __init__(self, message, *, unavailable=False):
        super().__init__(message)
        self.unavailable = unavailable


# -- honest measurements from the stored bytes ----------------------------------------------------


def _verdict(key: str, title: str, verdict: str, detail: str, next_step: str) -> dict:
    """One view-health dimension row, in the readiness-card shape."""
    return {
        "id": key,
        "title": title,
        "verdict": verdict,
        "detail": detail,
        "next_step": next_step,
    }


def sample_frame_brightness(path: Path, duration: float) -> list[float]:
    """Average luma (0-255) of evenly spaced frames, from ffmpeg signalstats.

    ``path`` is a private temporary copy of the recording; roughly
    ``BRIGHTNESS_SAMPLE_WINDOWS`` frames are sampled across the whole duration
    (the sampling rate is derived from ``duration`` so a short clip and a long
    one are both covered evenly). Raises ViewHealthError when ffmpeg is missing
    or the samples cannot be read.
    """
    if not math.isfinite(duration) or duration <= 0:
        raise ViewHealthError("This recording's brightness could not be measured.")
    rate = BRIGHTNESS_SAMPLE_WINDOWS / duration
    try:
        result = subprocess.run(
            ["ffmpeg", "-v", "error", "-nostdin", "-protocol_whitelist", "file",
             "-i", str(path), "-vf", f"fps={rate:.6f},signalstats,metadata=print:file=-",
             "-f", "null", "-"],
            capture_output=True, check=True, timeout=_ANALYZE_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as error:
        raise ViewHealthError(
            "View checking is unavailable. Ask an administrator to install ffmpeg.",
            unavailable=True,
        ) from error
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as error:
        raise ViewHealthError("This recording's brightness could not be measured.") from error
    averages = []
    for line in result.stdout.decode("utf-8", "replace").splitlines():
        if line.startswith("lavfi.signalstats.YAVG="):
            try:
                averages.append(float(line.split("=", 1)[1]))
            except ValueError:
                raise ViewHealthError("This recording's brightness could not be measured.") from None
    if not averages or not all(math.isfinite(value) for value in averages):
        raise ViewHealthError("This recording's brightness could not be measured.")
    return averages


def _write_recording_copy(recording) -> Path:
    """Copy one recording's stored bytes to a private temporary file, digest-checked."""
    import hashlib

    from django.core.files.storage import default_storage

    digest = hashlib.sha256()
    directory = Path(settings.DATA_HOME) / "view-health"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = tempfile.NamedTemporaryFile(prefix="recording-", suffix=".mp4", dir=directory, delete=False)
    try:
        with default_storage.open(recording.storage_key, "rb") as stored:
            for chunk in iter(lambda: stored.read(1024 * 1024), b""):
                digest.update(chunk)
                temp.write(chunk)
        temp.close()
        if digest.hexdigest() != recording.sha256:
            raise ViewHealthError(
                "The recording changed on disk since it was imported. Re-import it before checking this view."
            )
    except BaseException:
        temp.close()
        Path(temp.name).unlink(missing_ok=True)
        raise
    return Path(temp.name)


def _longest_dark_flat_stretch(samples: list[float]) -> int:
    """The longest run of consecutive dark, near-identical sampled frames.

    This is the obstruction proxy's honest limit: consecutive sampled frames
    (roughly ``duration / BRIGHTNESS_SAMPLE_WINDOWS`` apart) that are all dark
    AND differ from the previous dark sample by less than ``DARK_CHANGE_STEP``
    luma — the picture barely changed in the dark. A real night scene still has
    movement and exposure changes across seconds; a covered lens does not.
    The run may start on any dark sample, so a dark-then-bright clip is judged
    by its dark half only.
    """
    longest = current = 0
    previous_dark = None
    for value in samples:
        if value <= DARK_YAVG:
            current = current + 1 if previous_dark is not None and abs(value - previous_dark) < DARK_CHANGE_STEP else 1
            longest = max(longest, current)
            previous_dark = value
        else:
            current = 0
            previous_dark = None
    return longest


# -- the suitability verdicts ---------------------------------------------------------------------


def coverage_dimension(recording) -> dict:
    """Resolution against the ≤1920×1080 bound every recording must meet."""
    width, height = recording.width, recording.height
    if width <= MAX_WIDTH and height <= MAX_HEIGHT:
        return _verdict(
            "coverage",
            "Picture coverage",
            OK,
            f"The recording is {width}×{height} pixels; recordings must be no larger than "
            f"{MAX_WIDTH}×{MAX_HEIGHT}.",
            "",
        )
    return _verdict(
        "coverage",
        "Picture coverage",
        UNSUITABLE,
        f"The recording is {width}×{height} pixels; recordings must be no larger than "
        f"{MAX_WIDTH}×{MAX_HEIGHT}.",
        _RECORD_TEST_CLIP,
    )


def day_night_dimension(samples: list[float], duration: float) -> dict:
    """The day/night mix of sampled frames, with the sample count stated."""
    dark = sum(1 for value in samples if value <= DARK_YAVG)
    average = sum(samples) / len(samples)
    detail = (
        f"{len(samples)} frames evenly spaced across {duration:.0f} s were measured: "
        f"average brightness {average:.0f} of 255, {dark} dark frame{'s' if dark != 1 else ''} "
        f"(night-like). Brightness alone cannot prove a view works at night."
    )
    if dark >= DARK_SAMPLE_MINIMUM:
        return _verdict("day_night", "Day and night", OK, detail, "")
    return _verdict(
        "day_night", "Day and night", REVIEW, detail,
        "Record one test clip that includes darker hours, so the view can be checked at night.",
    )


def timeline_dimension(timeline) -> dict:
    """Frame age and gaps from the T20 timeline's real numbers."""
    summary = timeline.summary()
    span = summary["last_pts"] - summary["first_pts"] if summary["frame_count"] else 0.0
    missing = sum(gap["length_seconds"] for gap in summary["gaps"])
    gaps = summary["gaps"]
    if gaps:
        worst = max(gap["length_seconds"] for gap in gaps)
        return _verdict(
            "timeline",
            "Frame age and gaps",
            UNSUITABLE,
            f"{summary['frame_count']} frames over {span:.0f} s, but {missing:.0f} s of picture time is "
            f"missing ({len(gaps)} gap{'s' if len(gaps) != 1 else ''}, the longest {worst:.0f} s).",
            _INSTALLER_NEXT_STEP,
        )
    return _verdict(
        "timeline",
        "Frame age and gaps",
        OK,
        f"{summary['frame_count']} frames over {span:.0f} s with no missing stretches of picture time; "
        f"{summary['jitter_count']} frame{'s' if summary['jitter_count'] != 1 else ''} arrived off-cadence.",
        "",
    )


def obstruction_dimension(samples: list[float], duration: float) -> dict:
    """The obstruction proxy: long dark, near-motionless stretches of sampled frames."""
    stretch = _longest_dark_flat_stretch(samples)
    window = duration / BRIGHTNESS_SAMPLE_WINDOWS if duration > 0 else 0.0
    detail = (
        f"Longest dark, near-motionless stretch: about {stretch * window:.0f} s of {duration:.0f} s. "
        "This is a proxy, not a detection: it cannot tell a covered lens from an empty night-time scene."
    )
    if stretch >= 3:
        return _verdict("obstruction", "Obstruction check", UNSUITABLE, detail, _INSTALLER_NEXT_STEP)
    if stretch == 2:
        return _verdict(
            "obstruction", "Obstruction check", REVIEW, detail,
            "Record one test clip from this source and watch it, to tell a covered lens from a dark scene.",
        )
    return _verdict("obstruction", "Obstruction check", OK, detail, "")


def object_size_dimension(estimate) -> dict:
    """The user-entered object-size estimate checked against plain limits.

    ``estimate`` is a ``SourceViewEstimate`` row or None. Object size is never
    measured: it depends on scene depth no recording can reveal, so the person
    who knows the view states it, with guidance.
    """
    if estimate is None:
        return _verdict(
            "object_size",
            "Object size",
            REVIEW,
            "No object-size estimate entered yet. Athena cannot measure this: how tall a person stands "
            "in the picture depends on how far they are from the camera.",
            "Estimate how tall a person stands in this view (as a share of the picture height), using the "
            "guidance next to the estimate field.",
        )
    percent = estimate.person_height_percent
    detail = (
        f"You estimated a person stands about {percent:g}% of the picture height tall in this view "
        "(entered by you; Athena cannot measure this from the recording)."
    )
    if percent >= 40:
        return _verdict("object_size", "Object size", OK, detail, "")
    if percent >= 15:
        return _verdict(
            "object_size", "Object size", REVIEW, detail,
            "People are small in this view. Ask the installer to move the camera closer or use a narrower "
            "lens, or accept fewer pixels per person and record a test clip to check what is missed.",
        )
    return _verdict(
        "object_size", "Object size", UNSUITABLE, detail,
        "Ask the installer to move the camera closer or use a narrower lens, then record a test clip; "
        "people are too small here for reliable counting.",
    )


def recording_health(recording, *, estimate=None) -> dict:
    """Measure one recording's view health from its actual stored bytes.

    Raises ViewHealthError (never returns a half-built answer) when the
    recording cannot be read or its timeline cannot be built.
    """
    from .source_time import SourceTimeError

    path = _write_recording_copy(recording)
    try:
        try:
            timeline = build_timeline(recording)
        except SourceTimeError as error:
            raise ViewHealthError(str(error), unavailable=getattr(error, "unavailable", False)) from error
        samples = sample_frame_brightness(path, recording.duration_seconds)
    finally:
        path.unlink(missing_ok=True)
    dimensions = [
        coverage_dimension(recording),
        day_night_dimension(samples, recording.duration_seconds),
        timeline_dimension(timeline),
        obstruction_dimension(samples, recording.duration_seconds),
        object_size_dimension(estimate),
    ]
    return _assemble(
        recording=recording,
        recording_sha256=recording.sha256,
        dimensions=dimensions,
        brightness_samples=samples,
        timeline=timeline,
    )


def _assemble(*, recording, recording_sha256, dimensions, brightness_samples, timeline) -> dict:
    worst = UNSUITABLE if any(d["verdict"] == UNSUITABLE for d in dimensions) else (
        REVIEW if any(d["verdict"] == REVIEW for d in dimensions) else OK
    )
    summary = timeline.summary()
    return {
        "schema_version": 1,
        "recording_id": str(recording.id),
        "recording_sha256": recording_sha256,
        "recorded_at": recording.captured_at.isoformat() if recording.captured_at else None,
        "ingested_at": recording.created_at.isoformat(),
        "verdict": worst,
        "dimensions": dimensions,
        "unsuitable": [d for d in dimensions if d["verdict"] == UNSUITABLE],
        "measurements": {
            "brightness_sample_count": len(brightness_samples),
            "dark_sample_count": sum(1 for value in brightness_samples if value <= DARK_YAVG),
            "average_brightness": round(sum(brightness_samples) / len(brightness_samples), 1),
            "frame_count": summary["frame_count"],
            "gap_count": len(summary["gaps"]),
            "missing_seconds": round(sum(gap["length_seconds"] for gap in summary["gaps"]), 3),
            "longest_gap_seconds": round(
                max((gap["length_seconds"] for gap in summary["gaps"]), default=0.0), 3
            ),
        },
    }


def plain_recording_missing() -> dict:
    """The honest answer when a source has no recording to measure: ask for one."""
    return {
        "schema_version": 1,
        "recording_id": None,
        "recording_sha256": None,
        "recorded_at": None,
        "ingested_at": None,
        "verdict": REVIEW,
        "dimensions": [
            _verdict(
                "recording",
                "Recorded test clip",
                REVIEW,
                "No recording from this source yet, so nothing can be measured.",
                "Record one test clip from this source and import it; its view health is then measured "
                "from the actual stored video.",
            )
        ],
        "unsuitable": [],
        "measurements": None,
    }
