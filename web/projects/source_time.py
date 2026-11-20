"""Source time and ordered processing contracts for recorded sources (T20).

Given one ``OperationSourceRecording`` (T09: the whole file kept byte-for-byte
with its digest, the container's ``creation_time`` when present, and Athena's
ingestion time), this module derives the recording's **ordered frame timeline**
with ffprobe/ffmpeg and applies the time contracts from
``docs/plans/2026-11-19-t20-source-time.md``:

* three distinct clocks — capture time (container tag), frame presentation
  timestamps, ingestion time (already stored by T09) — never conflated;
* wall-clock mapping ``captured_at + pts``, null when the container carried no
  ``creation_time`` (that is normal, not an error);
* a monotonic processing order: PTS order with late (backwards-timestamp)
  frames excluded and reported, never silently dropped;
* gap/discontinuity detection with a configurable threshold (default 1.0 s);
* session segmentation: maximal runs of frames with no gap at or above the
  threshold;
* reproducibility: the timeline is derived only from the stored bytes and
  serializes canonically, so the same recording digest and threshold always
  produce the same ``timeline_sha256``.

Nothing here decodes pictures into detections or claims to have watched the
video; it reads the recording's timestamps only. Live sources (T10) and model
inference (T21) are out of scope.
"""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from django.conf import settings

#: A gap at or above this many seconds of missing presentation time is a
#: discontinuity (and therefore a session boundary). Below it, missing time is
#: ordinary encoder jitter, reported as a count only.
DEFAULT_GAP_THRESHOLD_SECONDS = 1.0
#: Timestamps are rational; a healthy constant-rate stream matches the expected
#: cadence exactly. Anything under half a millisecond is rounding noise.
CADENCE_TOLERANCE_SECONDS = 0.0005
#: PTS values are rounded to microseconds before serialization so that the
#: canonical form never depends on float noise.
_PTS_QUANTUM = 1_000_000
#: Decoding the whole recording must finish within this budget (the import
#: preflight uses the same order of magnitude).
_DECODE_TIMEOUT_SECONDS = 120
_PROBE_TIMEOUT_SECONDS = 30


class SourceTimeError(ValueError):
    """A recording's timeline could not be built, with a plain-language reason."""

    def __init__(self, message, *, unavailable=False):
        super().__init__(message)
        self.unavailable = unavailable


@dataclass(frozen=True)
class Frame:
    """One frame in the monotonic processing order.

    ``pts`` is the container's presentation timestamp in seconds from the start
    of the recording; ``wall_clock`` is the derived capture time
    (``captured_at + pts``) and is None when the container carried no
    ``creation_time``.
    """

    index: int
    pts: float
    duration: float
    wall_clock: str | None
    session: int
    on_time: bool


@dataclass(frozen=True)
class Gap:
    """A discontinuity: presentation time missing between two adjacent frames."""

    after_frame: int
    before_frame: int
    start_pts: float
    end_pts: float
    length_seconds: float


@dataclass(frozen=True)
class Session:
    """A maximal run of frames with no gap at or above the threshold between them."""

    number: int
    first_frame: int
    last_frame: int
    frame_count: int
    first_pts: float
    last_pts: float
    first_wall_clock: str | None
    last_wall_clock: str | None


@dataclass(frozen=True)
class FrameTimeline:
    """The reproducible ordered view of one recording's frames.

    The same recording bytes and the same gap threshold always produce an equal
    timeline (see :meth:`timeline_sha256`).
    """

    recording_sha256: str
    gap_threshold_seconds: float
    captured_at: str | None
    frames: list[Frame] = field(default_factory=list)
    late_frames: list[dict] = field(default_factory=list)
    jitter_count: int = 0
    gaps: list[Gap] = field(default_factory=list)
    sessions: list[Session] = field(default_factory=list)

    def summary(self) -> dict:
        """A plain JSON-serializable summary for execution outcomes and tests."""
        return {
            "recording_sha256": self.recording_sha256,
            "gap_threshold_seconds": self.gap_threshold_seconds,
            "captured_at": self.captured_at,
            "frame_count": len(self.frames),
            "late_frame_count": len(self.late_frames),
            "jitter_count": self.jitter_count,
            "first_pts": self.frames[0].pts if self.frames else None,
            "last_pts": self.frames[-1].pts if self.frames else None,
            "gaps": [
                {
                    "after_frame": gap.after_frame,
                    "start_pts": gap.start_pts,
                    "end_pts": gap.end_pts,
                    "length_seconds": gap.length_seconds,
                }
                for gap in self.gaps
            ],
            "sessions": [
                {
                    "number": session.number,
                    "first_frame": session.first_frame,
                    "last_frame": session.last_frame,
                    "frame_count": session.frame_count,
                    "first_pts": session.first_pts,
                    "last_pts": session.last_pts,
                    "first_wall_clock": session.first_wall_clock,
                    "last_wall_clock": session.last_wall_clock,
                }
                for session in self.sessions
            ],
        }

    def canonical(self) -> str:
        """The canonical serialization the timeline hash is taken over."""
        payload = {
            "schema_version": 1,
            "kind": "frame_timeline",
            "recording_sha256": self.recording_sha256,
            "gap_threshold_seconds": self.gap_threshold_seconds,
            "captured_at": self.captured_at,
            "frames": [
                {
                    "index": frame.index,
                    "pts": _round_pts(frame.pts),
                    "duration": _round_pts(frame.duration),
                    "wall_clock": frame.wall_clock,
                    "session": frame.session,
                    "on_time": frame.on_time,
                }
                for frame in self.frames
            ],
            "late_frames": [
                {
                    "container_position": late["container_position"],
                    "pts": _round_pts(late["pts"]),
                    "expected_pts": _round_pts(late["expected_pts"]),
                }
                for late in self.late_frames
            ],
            "jitter_count": self.jitter_count,
            "gaps": [
                {
                    "after_frame": gap.after_frame,
                    "before_frame": gap.before_frame,
                    "start_pts": _round_pts(gap.start_pts),
                    "end_pts": _round_pts(gap.end_pts),
                    "length_seconds": _round_pts(gap.length_seconds),
                }
                for gap in self.gaps
            ],
            "sessions": [
                {
                    "number": session.number,
                    "first_frame": session.first_frame,
                    "last_frame": session.last_frame,
                    "frame_count": session.frame_count,
                    "first_pts": _round_pts(session.first_pts),
                    "last_pts": _round_pts(session.last_pts),
                    "first_wall_clock": session.first_wall_clock,
                    "last_wall_clock": session.last_wall_clock,
                }
                for session in self.sessions
            ],
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

    def timeline_sha256(self) -> str:
        return hashlib.sha256(self.canonical().encode()).hexdigest()


def _round_pts(value: float) -> int:
    """PTS values are quantized to microseconds so hashes never carry float noise."""
    return int(round(value * _PTS_QUANTUM))


# -- reading the recording's timestamps ---------------------------------------------------------
def _probe_packets(path: Path) -> list[dict]:
    """The video stream's packets (PTS + duration) as ffprobe sees them, in probe order.

    Only the forced MP4 demuxer and the local file protocol are enabled, exactly
    like the import preflight; the path is a private temporary copy, never an
    uploaded name.
    """
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-protocol_whitelist", "file", "-f", "mov",
             "-enable_drefs", "0", "-use_absolute_path", "0",
             "-select_streams", "v:0", "-show_entries", "packet=pts_time,duration_time",
             "-of", "json", str(path)],
            capture_output=True, check=True, timeout=_PROBE_TIMEOUT_SECONDS,
        )
        data = json.loads(result.stdout)
    except FileNotFoundError as error:
        raise SourceTimeError(
            "Frame timeline checking is unavailable. Ask an administrator to install ffprobe.",
            unavailable=True,
        ) from error
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        raise SourceTimeError("The recording's frame timestamps could not be read.") from error
    packets = data.get("packets") or []
    frames = []
    for position, packet in enumerate(packets):
        try:
            pts = float(packet["pts_time"])
        except (KeyError, TypeError, ValueError):
            raise SourceTimeError("The recording's frame timestamps could not be read.") from None
        try:
            duration = float(packet.get("duration_time") or 0.0)
        except (TypeError, ValueError):
            duration = 0.0
        if not math.isfinite(pts) or pts < 0 or not math.isfinite(duration) or duration < 0:
            raise SourceTimeError("The recording's frame timestamps could not be read.")
        frames.append({"container_position": position, "pts": pts, "duration": duration})
    if not frames:
        raise SourceTimeError("The recording has no video frames.")
    return frames


def _decodes(path: Path) -> bool:
    """The whole file must still decode (the import preflight proved it once)."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-v", "error", "-xerror", "-nostdin", "-protocol_whitelist", "file",
             "-i", str(path), "-f", "null", "-"],
            capture_output=True, timeout=_DECODE_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as error:
        raise SourceTimeError(
            "Frame timeline checking is unavailable. Ask an administrator to install ffmpeg.",
            unavailable=True,
        ) from error
    except subprocess.TimeoutExpired:
        return False
    return result.returncode == 0


def build_timeline(
    recording, *, gap_threshold_seconds: float = DEFAULT_GAP_THRESHOLD_SECONDS
) -> FrameTimeline:
    """Build one recording's ordered frame timeline. Raises SourceTimeError, never guesses.

    ``recording`` is an ``OperationSourceRecording``. The stored bytes are read
    from private storage, re-digested (the timeline records what it actually
    read), probed for per-frame PTS, and checked to still decode end to end — a
    recording that imported healthy but no longer decodes has been corrupted,
    and that is a refusal, not an empty timeline.
    """
    if not (isinstance(gap_threshold_seconds, (int, float)) and not isinstance(gap_threshold_seconds, bool)):
        raise SourceTimeError("The gap threshold must be a number of seconds.")
    if not math.isfinite(gap_threshold_seconds) or gap_threshold_seconds <= 0:
        raise SourceTimeError("The gap threshold must be a positive number of seconds.")
    threshold = float(gap_threshold_seconds)

    from django.core.files.storage import default_storage

    digest = hashlib.sha256()
    directory = Path(settings.DATA_HOME) / "timeline-build"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix="recording-", suffix=".mp4", dir=directory) as temp:
        try:
            with default_storage.open(recording.storage_key, "rb") as stored:
                for chunk in iter(lambda: stored.read(1024 * 1024), b""):
                    digest.update(chunk)
                    temp.write(chunk)
        except (OSError, ValueError) as error:
            raise SourceTimeError(
                "The recording could not be read from storage. Ask an administrator to check it."
            ) from error
        temp.flush()
        if digest.hexdigest() != recording.sha256:
            raise SourceTimeError(
                "The recording changed on disk since it was imported. Re-import it before building a timeline."
            )
        if not _decodes(Path(temp.name)):
            raise SourceTimeError(
                "The recording's pictures no longer decode. The file is damaged; re-import it."
            )
        packets = _probe_packets(Path(temp.name))

    captured_at = recording.captured_at
    captured_iso = captured_at.isoformat() if captured_at is not None else None

    # The monotonic processing order is PTS order (the container stores packets
    # out of order); a stable sort keeps container order for equal timestamps.
    ordered = sorted(packets, key=lambda packet: (packet["pts"], packet["container_position"]))

    durations = [packet["duration"] for packet in ordered if packet["duration"] > 0]
    fallback_duration = _median(durations) if durations else 0.0

    frames: list[Frame] = []
    late_frames: list[dict] = []
    gaps: list[Gap] = []
    jitter_count = 0
    session_number = 1
    session_first_index = 0

    def _close_session(last_index: int) -> Session:
        first = frames[session_first_index]
        last = frames[last_index]
        return Session(
            number=session_number,
            first_frame=first.index,
            last_frame=last.index,
            frame_count=last_index - session_first_index + 1,
            first_pts=first.pts,
            last_pts=last.pts,
            first_wall_clock=first.wall_clock,
            last_wall_clock=last.wall_clock,
        )

    sessions: list[Session] = []
    for packet in ordered:
        position = packet["container_position"]
        pts = packet["pts"]
        duration = packet["duration"] or fallback_duration
        wall_clock = (
            (captured_at + timedelta(seconds=pts)).isoformat() if captured_at is not None else None
        )
        delta = None
        if frames:
            previous = frames[-1]
            expected = previous.pts + previous.duration
            delta = pts - expected
            if delta < -CADENCE_TOLERANCE_SECONDS:
                # Timestamps went backwards: a late frame for a recorded file is
                # malformed container data. It is excluded from the monotonic
                # processing order and reported — never silently dropped.
                late_frames.append(
                    {"container_position": position, "pts": pts, "expected_pts": expected}
                )
                continue
            if delta > CADENCE_TOLERANCE_SECONDS:
                if delta >= threshold:
                    gaps.append(
                        Gap(
                            after_frame=previous.index,
                            before_frame=len(frames),
                            start_pts=previous.pts,
                            end_pts=pts,
                            length_seconds=delta,
                        )
                    )
                    sessions.append(_close_session(len(frames) - 1))
                    session_number += 1
                    session_first_index = len(frames)
                else:
                    jitter_count += 1
        # A frame is on time when its start matches the previous frame's cadence
        # exactly (the first frame of a session after a gap starts a new cadence).
        on_time = delta is None or abs(delta) <= CADENCE_TOLERANCE_SECONDS
        frames.append(
            Frame(
                index=len(frames),
                pts=pts,
                duration=duration,
                wall_clock=wall_clock,
                session=session_number,
                on_time=on_time,
            )
        )
    if frames:
        sessions.append(_close_session(len(frames) - 1))

    return FrameTimeline(
        recording_sha256=digest.hexdigest(),
        gap_threshold_seconds=threshold,
        captured_at=captured_iso,
        frames=frames,
        late_frames=late_frames,
        jitter_count=jitter_count,
        gaps=gaps,
        sessions=sessions,
    )


def _median(values: list[float]) -> float:
    ordered_values = sorted(values)
    middle = len(ordered_values) // 2
    if len(ordered_values) % 2:
        return ordered_values[middle]
    return (ordered_values[middle - 1] + ordered_values[middle]) / 2.0
