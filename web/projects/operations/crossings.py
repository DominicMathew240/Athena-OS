"""Finite-line crossing episodes for one already-associated track in one source session.

Pure temporal geometry, not detection or tracking. Coordinates use normalized image
space with x to the right and y down. Positive is the left-hand cross-product side
of the directed start-to-end line in that coordinate system. The caller owns track
lifecycle, identity, persistence and gap/coverage reporting.

T25 consumes tracks (T22) through ``episodes_from_tracks``: each track's box-center
anchor feeds one ``CrossingCounter``. Valid side-to-side episodes emit direction
and time once; reverse returns count; jitter, touches, line-parallel motion and
long ambiguous gaps never fabricate events. Worker commits of the resulting
``detector_crossing`` drafts must use the live-lease fence (see
``executions.commit_events_owned``).
"""
from __future__ import annotations

import math
from dataclasses import dataclass


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _point(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2 or any(
        not _number(v) or not 0 <= v <= 1 for v in value
    ):
        raise ValueError("Points must contain two finite normalized coordinates.")
    return tuple(value)


@dataclass(frozen=True)
class Crossing:
    direction: str
    time: float


@dataclass(frozen=True)
class CrossingEpisode:
    """One valid directional crossing derived from a single session-scoped track."""

    direction: str
    time: float
    track_id: int
    session: int
    camera_key: str


def _observation_anchor(observation) -> tuple[float, float]:
    """The configured object anchor for one track observation: the box center.

    Track observations carry ``box`` as ``(x, y, width, height)`` in normalized
    image space. The center is the same anchor ``CrossingCounter`` already tests
    against; footprint/bottom anchors are out of scope for this bounded slice.
    """
    try:
        x, y, width, height = observation.box
    except (AttributeError, TypeError, ValueError):
        raise ValueError("Each track observation needs a normalized box.") from None
    if any(not _number(v) for v in (x, y, width, height)):
        raise ValueError("Each track observation needs a normalized box.")
    if width <= 0 or height <= 0:
        raise ValueError("Each track observation needs a positive-size box.")
    cx, cy = x + width / 2.0, y + height / 2.0
    if not (0 <= cx <= 1 and 0 <= cy <= 1):
        raise ValueError("Each track observation anchor must stay in normalized image space.")
    return (cx, cy)


def episodes_from_tracks(tracks, start, end, *, dead_band=0.01, confirmations=2, max_gap=1.0):
    """Emit valid crossing episodes from session-scoped tracks over one finite line.

    Each track gets its own ``CrossingCounter`` — track ids are never used as a
    permanent "already counted" set, so a real reverse return still counts.
    Observations are consumed in track order by presentation time; a gap longer
    than ``max_gap`` resets that track's episode state and never invents a
    crossing across it.
    """
    if tracks is None:
        raise ValueError("Provide the tracks to evaluate, or an empty list.")
    episodes: list[CrossingEpisode] = []
    for track in tracks:
        try:
            track_id = track.track_id
            session = track.session
            camera_key = track.camera_key
            observations = track.observations
        except AttributeError as error:
            raise ValueError("Each track needs track_id, session, camera_key and observations.") from error
        if type(track_id) is not int or track_id < 1:
            raise ValueError("Each track id must be a positive integer.")
        if type(session) is not int or session < 0:
            raise ValueError("Each track session must be a nonnegative integer.")
        if not isinstance(camera_key, str) or not camera_key:
            raise ValueError("Each track must name its camera.")
        counter = CrossingCounter(
            start, end, dead_band=dead_band, confirmations=confirmations, max_gap=max_gap
        )
        for observation in observations:
            try:
                pts = observation.pts
            except AttributeError as error:
                raise ValueError("Each track observation needs a presentation timestamp.") from error
            event = counter.observe(pts, _observation_anchor(observation))
            if event is not None:
                episodes.append(
                    CrossingEpisode(
                        direction=event.direction,
                        time=event.time,
                        track_id=track_id,
                        session=session,
                        camera_key=camera_key,
                    )
                )
    return episodes


def episode_event_drafts(episodes, *, source: str):
    """Ledger drafts for detector crossing episodes (T23 kind ``detector_crossing``).

    Session comes from the track's T20 session; payload carries direction plus
    the session-scoped track bookkeeping the episode came from. Worker commits
    must go through ``executions.commit_events_owned``.
    """
    from ..models import OperationEvent
    from .ledger import EventDraft

    if not isinstance(source, str) or not source:
        raise ValueError("Detector crossing drafts need their source stream name.")
    if episodes is None:
        raise ValueError("Provide the episodes to draft, or an empty list.")
    drafts = []
    for episode in episodes:
        drafts.append(
            EventDraft(
                kind=OperationEvent.KIND_DETECTOR,
                source=source,
                session=episode.session,
                timestamp=episode.time,
                payload={
                    "direction": episode.direction,
                    "track_id": episode.track_id,
                    "camera_key": episode.camera_key,
                },
            )
        )
    return drafts


class CrossingCounter:
    """Confirm stable sides and emit valid crossings, including reverse returns.

    A side must be observed `confirmations` times outside the dead band before it
    becomes stable. A candidate is emitted only after the opposite side stabilizes
    and the actual observed path crossed strictly inside the finite segment. A gap
    longer than max_gap forgets the episode, never inventing a crossing over it.
    Timestamp interpolation is an estimate between the adjacent observations that
    straddled the line; it is not proof of the real-world crossing time.
    """

    def __init__(self, start, end, *, dead_band=0.01, confirmations=2, max_gap=1.0):
        self.start, self.end = _point(start), _point(end)
        self.dx, self.dy = self.end[0] - self.start[0], self.end[1] - self.start[1]
        self.length = math.hypot(self.dx, self.dy)
        if not self.length:
            raise ValueError("The line must have distinct endpoints.")
        if not _number(dead_band) or not 0 <= dead_band < 1:
            raise ValueError("Dead band must be between 0 and 1.")
        if type(confirmations) is not int or confirmations < 1:
            raise ValueError("Confirmations must be a positive integer.")
        if not _number(max_gap) or max_gap <= 0:
            raise ValueError("Maximum gap must be positive.")
        self.dead_band, self.confirmations, self.max_gap = dead_band, confirmations, max_gap
        self.last_time = None
        self._reset_episode()

    def _reset_episode(self):
        self.stable_side = 0
        self.pending_side = 0
        self.pending_count = 0
        self.previous_nonzero = None
        self.candidate = None

    def _distance(self, point):
        return (self.dx * (point[1] - self.start[1]) - self.dy * (point[0] - self.start[0])) / self.length

    def _candidate(self, previous, now, distance):
        old_time, old_point, old_distance = previous
        if self.last_time - old_time > self.max_gap:
            return None
        fraction = abs(old_distance) / (abs(old_distance) + abs(distance))
        x = old_point[0] + fraction * (now[0] - old_point[0])
        y = old_point[1] + fraction * (now[1] - old_point[1])
        along = ((x - self.start[0]) * self.dx + (y - self.start[1]) * self.dy) / (self.length * self.length)
        if not 0 < along < 1:
            return None
        direction = "negative_to_positive" if old_distance < 0 else "positive_to_negative"
        return Crossing(direction=direction, time=old_time + fraction * (self.last_time - old_time))

    def observe(self, time, point):
        if not _number(time) or time < 0 or (self.last_time is not None and time <= self.last_time):
            raise ValueError("Source timestamps must be finite, nonnegative and strictly increasing.")
        point = _point(point)
        if self.last_time is not None and time - self.last_time > self.max_gap:
            self._reset_episode()
        self.last_time = time
        distance = self._distance(point)
        raw_side = 1 if distance > 0 else -1 if distance < 0 else 0
        if raw_side:
            previous = self.previous_nonzero
            if previous and (previous[2] > 0) != (distance > 0):
                old_side = 1 if previous[2] > 0 else -1
                if self.stable_side and old_side == self.stable_side:
                    self.candidate = self._candidate(previous, point, distance)
                else:
                    self.candidate = None
            self.previous_nonzero = (time, point, distance)
        side = raw_side if abs(distance) > self.dead_band else 0
        if not side:
            self.pending_side, self.pending_count = 0, 0
            return None
        if side == self.pending_side:
            self.pending_count += 1
        else:
            self.pending_side, self.pending_count = side, 1
        if self.pending_count < self.confirmations or side == self.stable_side:
            return None
        event = self.candidate if self.stable_side else None
        if event and time - event.time > self.max_gap:
            event = None
        self.stable_side = side
        self.candidate = None
        return event
