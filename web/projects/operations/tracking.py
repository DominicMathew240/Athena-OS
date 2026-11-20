"""Per-camera, per-session object tracking over the T21 detections (T22).

T21's adapter produced honest per-frame detections: normalized boxes, each tagged
with the timeline frame it came from. This module associates those detections
into **tracks** with explicit lifecycle rules — and it is deliberately honest
about what a track is and is not:

* **Camera/session scope.** A track lives inside one source session (T20's
  maximal no-gap runs — the natural scope of one continuous camera observation).
  Crossing a session boundary ALWAYS ends the track; the next session's
  detections produce a new track with a new ID. Tracks are never compared,
  merged or continued across sessions.
* **Bounded occlusion memory, no identity claims.** While a track's detections
  are missing for less than ``max_gap_seconds`` of presentation time, it may
  resume with the SAME ID when a matching detection reappears. A gap at or
  above the threshold ends the track, and whatever reappears is a NEW track —
  the tracker never claims the same real-world object persisted across the
  gap. A below-threshold resume is recorded as a gap on the track: the ID is a
  frame-level bookkeeping convenience, not a persistence proof.
* **Deterministic, dependency-free.** Association is greedy on a
  motion-compensated overlap score, in a fixed order (detections by descending
  score then input order; tracks by descending score then first-created). The
  same detections always produce the same tracks, so an outcome is
  reproducible. No torch, no external tracker — greedy IoU is a stated
  simplification, not a hidden one.

What this module does NOT do: it does not watch video (it consumes detections),
does not run models, does not persist anything (the worker records the
summaries in the execution outcome), and does not claim re-identification.
T21's sparse sampling means consecutive analysed frames can be far apart in
time; the motion model simply carries the last box forward, so the overlap
threshold — not the sampling rate — defines occlusion tolerance.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

#: How far apart (presentation seconds) a track's detections may be while still
#: resuming the same ID. At or above this, the track ends: a reappearing
#: detection is a new track, never a persistent identity.
DEFAULT_MAX_GAP_SECONDS = 1.0
#: A candidate association needs at least this much overlap score to count.
#: Greedy takes the best-scoring pair first.
DEFAULT_MIN_OVERLAP = 0.1
#: How much of the overlap score comes from the centers' scale-relative
#: proximity rather than the boxes' IoU. Pure IoU is brittle when a mover
#: shifts completely off its last box between two sampled frames; pure center
#: distance is blind to scale. The blend keeps both honest.
_CENTER_WEIGHT = 0.25


class TrackingError(ValueError):
    """The detections could not be tracked, with a plain-language reason."""

    def __init__(self, message, *, unavailable=False):
        super().__init__(message)
        self.unavailable = unavailable


def _finite(value) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
    )


def _valid_box(box) -> bool:
    """A usable detection box: finite normalized [0,1] coordinates, positive size, label, score."""
    if not isinstance(box, dict):
        return False
    try:
        x, y = float(box["x"]), float(box["y"])
        width, height = float(box["width"]), float(box["height"])
        score = float(box["score"])
        label = box["label"]
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    return (
        0.0 <= x <= 1.0
        and 0.0 <= y <= 1.0
        and width > 0.0
        and height > 0.0
        and x + width <= 1.0 + 1e-9
        and y + height <= 1.0 + 1e-9
        and 0.0 <= score <= 1.0
        and isinstance(label, str)
        and label != ""
    )


def _center(box) -> tuple[float, float]:
    return (box["x"] + box["width"] / 2.0, box["y"] + box["height"] / 2.0)


def _overlap_score(first: dict, second: dict) -> float:
    """The association score between two boxes, in [0, 1]: motion-compensated
    IoU blended with a scale-relative center proximity.

    This is a heuristic — the simplification T22 accepts — not a learned
    motion model.
    """
    left = max(first["x"], second["x"])
    right = min(first["x"] + first["width"], second["x"] + second["width"])
    top = max(first["y"], second["y"])
    bottom = min(first["y"] + first["height"], second["y"] + second["height"])
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    union = first["width"] * first["height"] + second["width"] * second["height"] - intersection
    iou = intersection / union if union > 0 else 0.0

    fx, fy = _center(first)
    sx, sy = _center(second)
    scale = (first["width"] + second["width"] + first["height"] + second["height"]) / 4.0
    distance = math.hypot(fx - sx, fy - sy)
    proximity = 1.0 - min(1.0, distance / scale) if scale > 0 else 0.0

    return (1.0 - _CENTER_WEIGHT) * iou + _CENTER_WEIGHT * proximity


@dataclass(frozen=True)
class TrackObservation:
    """One detection on one track, in the order the tracker consumed it."""

    frame_index: int
    pts: float
    detection_index: int
    label: str
    score: float
    box: tuple[float, float, float, float]  # x, y, width, height — normalized


@dataclass
class Track:
    """One per-camera, per-session track: born on first appearance, ended on
    disappearance, a gap at/above the threshold, or a session change.

    ``camera_key``/``session`` give the scope; ``first_pts``/``last_pts`` the
    lifecycle bounds in presentation time; ``detection_indices`` are the global
    (per-run) detection indices the track consumed, in consumption order; the
    ``gaps`` list records every below-threshold occlusion the track bridged.
    A track with gaps is a resumed ID — a bookkeeping convenience, never a
    persistent-identity claim.
    """

    track_id: int
    camera_key: str
    session: int
    first_frame_index: int
    last_frame_index: int
    first_pts: float
    last_pts: float
    detection_count: int = 0
    detection_indices: list[int] = field(default_factory=list)
    gaps: list[dict] = field(default_factory=list)
    observations: list[TrackObservation] = field(default_factory=list)
    # Internal association state, never serialized.
    last_box: dict = field(default_factory=dict)
    last_velocity: tuple[float, float] = (0.0, 0.0)
    missed_frames: int = 0

    @property
    def span_seconds(self) -> float:
        return self.last_pts - self.first_pts

    def summary(self) -> dict:
        """A plain JSON-serializable summary for execution outcomes."""
        return {
            "track_id": self.track_id,
            "camera_key": self.camera_key,
            "session": self.session,
            "first_frame_index": self.first_frame_index,
            "last_frame_index": self.last_frame_index,
            "first_pts": self.first_pts,
            "last_pts": self.last_pts,
            "detection_count": self.detection_count,
            "detection_indices": list(self.detection_indices),
            "gaps": [dict(gap) for gap in self.gaps],
            "labels": sorted({observation.label for observation in self.observations}),
        }


@dataclass(frozen=True)
class TrackingReport:
    """The normalized outcome of one tracking run.

    ``ended_by_gap`` counts the tracks the tracker closed mid-stream because a
    gap at or above the threshold elapsed without a matching detection (their
    successors, if any, are new tracks with new IDs). A track that ran to the
    last analysed frame of its session ended with the stream instead and is
    not in that count. ``resumed_tracks`` counts tracks that bridged at least
    one below-threshold gap; ``total_gap_events`` counts those gaps.
    """

    camera_key: str
    max_gap_seconds: float
    min_overlap: float
    track_count: int
    ended_by_gap: int
    resumed_tracks: int
    total_gap_events: int
    tracks: tuple[Track, ...]

    def summary(self) -> dict:
        """A plain JSON-serializable summary for execution outcomes."""
        return {
            "camera_key": self.camera_key,
            "max_gap_seconds": self.max_gap_seconds,
            "min_overlap": self.min_overlap,
            "track_count": self.track_count,
            "ended_by_gap": self.ended_by_gap,
            "resumed_tracks": self.resumed_tracks,
            "total_gap_events": self.total_gap_events,
            "tracks": [track.summary() for track in self.tracks],
        }


def track_detections(
    frames,
    *,
    camera_key: str = "camera",
    max_gap_seconds: float = DEFAULT_MAX_GAP_SECONDS,
    min_overlap: float = DEFAULT_MIN_OVERLAP,
) -> TrackingReport:
    """Associate ordered per-frame detections into per-session tracks.

    ``frames`` is T21's ordered per-frame detection stream — anything carrying
    ``frame_index``, ``pts``, ``session`` and a ``detections`` iterable of
    normalized box dicts (e.g. :class:`~projects.operations.inference.FrameDetections`
    instances, or plain objects/dicts with those attributes). Frames must
    arrive in ascending frame-index order with non-decreasing PTS and session.
    A failed frame never reaches this function: T21 raises instead of
    returning empty frames, so an empty ``detections`` list here means the
    model genuinely found nothing on that frame.

    Per frame, in this exact order: tracks from another session end; tracks
    whose last detection is at or beyond ``max_gap_seconds`` in the past end
    (``ended_by_gap``); the surviving tracks are matched to the frame's
    detections greedily by descending overlap score; every unmatched detection
    starts a new track. Raises :class:`TrackingError` on malformed input; it
    never silently drops or clamps detections.
    """
    if not _finite(max_gap_seconds) or max_gap_seconds <= 0:
        raise TrackingError("The tracking gap threshold must be a positive number of seconds.")
    if not _finite(min_overlap) or not 0.0 < min_overlap <= 1.0:
        raise TrackingError("The tracking overlap threshold must be in (0, 1].")

    ordered = _validated(frames)

    tracks: list[Track] = []
    active: list[Track] = []
    next_track_id = 1
    ended_by_gap = 0
    detection_index = 0

    for frame in ordered:
        # 1. A session change always ends every open track: sessions are the
        #    natural per-camera scope, and no identity is ever carried across one.
        active = [track for track in active if track.session == frame["session"]]

        # 2. Memory ends where the threshold ends: a track whose last detection
        #    is at or beyond max_gap_seconds in the past is closed NOW, before
        #    any matching, so whatever reappears is a NEW track by construction.
        survivors: list[Track] = []
        for track in active:
            if frame["pts"] - track.last_pts >= max_gap_seconds:
                ended_by_gap += 1
            else:
                survivors.append(track)
        active = survivors

        # 3. Greedy association: detections in descending score then input
        #    order; each takes the best-scoring surviving track (ties go to
        #    the earlier-created track, so the result is deterministic).
        valid = frame["detections"]  # already validated, in input order
        candidates = [
            {"box": box, "detection_index": detection_index + offset}
            for offset, box in enumerate(valid)
        ]
        candidates.sort(key=lambda candidate: (-candidate["box"]["score"], candidate["detection_index"]))
        detection_index += len(valid)

        matched_tracks: set[int] = set()
        matched_detections: set[int] = set()
        for candidate in candidates:
            best_track = None
            best_score = min_overlap
            for track in active:
                if track.track_id in matched_tracks:
                    continue
                score = _overlap_score(_predicted_box(track, frame), candidate["box"])
                if score >= best_score:
                    # >= keeps the FIRST equally-good track: deterministic tie-break.
                    best_track = track
                    best_score = score
            if best_track is not None:
                matched_tracks.add(best_track.track_id)
                matched_detections.add(candidate["detection_index"])
                _attach(best_track, frame, candidate)

        # 4. Every unmatched detection starts a new track, in input order.
        for candidate in candidates:
            if candidate["detection_index"] in matched_detections:
                continue
            track = Track(
                track_id=next_track_id,
                camera_key=camera_key,
                session=frame["session"],
                first_frame_index=frame["frame_index"],
                last_frame_index=frame["frame_index"],
                first_pts=frame["pts"],
                last_pts=frame["pts"],
            )
            _attach(track, frame, candidate)
            tracks.append(track)
            active.append(track)
            matched_tracks.add(track.track_id)  # born this frame: not a miss
            next_track_id += 1

        # 5. A surviving track with no match on this analysed frame has missed
        #    one: the next observation (if any) records the occlusion.
        for track in active:
            if track.track_id not in matched_tracks:
                track.missed_frames += 1

    resumed = sum(1 for track in tracks if track.gaps)
    gap_events = sum(len(track.gaps) for track in tracks)

    return TrackingReport(
        camera_key=camera_key,
        max_gap_seconds=float(max_gap_seconds),
        min_overlap=float(min_overlap),
        track_count=len(tracks),
        ended_by_gap=ended_by_gap,
        resumed_tracks=resumed,
        total_gap_events=gap_events,
        tracks=tuple(tracks),
    )


def _predicted_box(track: Track, frame: dict) -> dict:
    """The track's box, carried forward by its velocity to the frame's PTS.

    A constant-velocity carry — the first-order motion model. With T21's
    sparse sampling the prediction is only as good as the sampling; it smooths
    jitter, it never invents motion. The overlap threshold is the arbiter of
    what matches.
    """
    dt = frame["pts"] - track.last_pts
    if dt <= 0:
        return dict(track.last_box)
    box = dict(track.last_box)
    box["x"] = track.last_box["x"] + track.last_velocity[0] * dt
    box["y"] = track.last_box["y"] + track.last_velocity[1] * dt
    return box


def _attach(track: Track, frame: dict, candidate: dict) -> None:
    """Add one matched detection to a track, updating motion and lifecycle."""
    box = candidate["box"]
    if track.detection_count > 0:
        dt = frame["pts"] - track.last_pts
        if dt > 0:
            fx, fy = _center(track.last_box)
            sx, sy = _center(box)
            track.last_velocity = ((sx - fx) / dt, (sy - fy) / dt)
        # An occlusion the track bridged: at least one analysed frame passed
        # without a match between the two observations. Recorded, honestly, as
        # a gap — the same ID is a bookkeeping convenience, not a proof.
        if track.missed_frames > 0:
            track.gaps.append(
                {
                    "after_frame_index": track.last_frame_index,
                    "before_frame_index": frame["frame_index"],
                    "missed_frames": track.missed_frames,
                    "length_seconds": round(frame["pts"] - track.last_pts, 6),
                }
            )
    track.missed_frames = 0
    track.detection_count += 1
    track.detection_indices.append(candidate["detection_index"])
    track.last_frame_index = max(track.last_frame_index, frame["frame_index"])
    track.first_frame_index = min(track.first_frame_index, frame["frame_index"])
    track.last_pts = frame["pts"]
    track.last_box = dict(box)
    track.observations.append(
        TrackObservation(
            frame_index=frame["frame_index"],
            pts=frame["pts"],
            detection_index=candidate["detection_index"],
            label=box["label"],
            score=box["score"],
            box=(box["x"], box["y"], box["width"], box["height"]),
        )
    )


def _validated(frames) -> list[dict]:
    """Frames as plain ordered dicts, with every hard contract enforced."""
    from .inference import FrameDetections

    ordered: list[dict] = []
    last_frame_index = -1
    last_pts = None
    last_session = None
    for frame in frames:
        if isinstance(frame, FrameDetections):
            record = {
                "frame_index": frame.frame_index,
                "pts": frame.pts,
                "session": frame.session,
                "detections": list(frame.detections),
            }
        elif isinstance(frame, dict):
            try:
                record = {
                    "frame_index": frame["frame_index"],
                    "pts": frame["pts"],
                    "session": frame["session"],
                    "detections": list(frame["detections"]),
                }
            except (KeyError, TypeError) as error:
                raise TrackingError(
                    "A frame record is malformed: it must carry frame_index, pts, session and detections."
                ) from error
        else:
            try:
                record = {
                    "frame_index": frame.frame_index,
                    "pts": frame.pts,
                    "session": frame.session,
                    "detections": list(frame.detections),
                }
            except (AttributeError, TypeError) as error:
                raise TrackingError(
                    "A frame record is malformed: it must carry frame_index, pts, session and detections."
                ) from error
        index, pts, session = record["frame_index"], record["pts"], record["session"]
        if type(index) is not int or type(session) is not int:
            raise TrackingError("Frame indices and sessions must be integers.")
        if not _finite(pts) or pts < 0:
            raise TrackingError("Frame presentation timestamps must be non-negative numbers.")
        if index <= last_frame_index:
            raise TrackingError("Frames must arrive in ascending frame-index order.")
        if last_pts is not None and pts < last_pts:
            raise TrackingError("Frame presentation timestamps must be non-decreasing.")
        if last_session is not None and session < last_session:
            raise TrackingError("Frame sessions must be non-decreasing.")
        for box in record["detections"]:
            if not _valid_box(box):
                raise TrackingError(
                    "A detection is not a valid normalized box: boxes need finite [0,1] coordinates, "
                    "positive size, a label and a score in [0,1]."
                )
        ordered.append(record)
        last_frame_index, last_pts, last_session = index, pts, session
    return ordered


__all__ = [
    "DEFAULT_MAX_GAP_SECONDS",
    "DEFAULT_MIN_OVERLAP",
    "Track",
    "TrackObservation",
    "TrackingError",
    "TrackingReport",
    "track_detections",
]
