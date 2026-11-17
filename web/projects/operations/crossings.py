"""Finite-line crossing episodes for one already-associated track in one source session.

Pure temporal geometry, not detection or tracking. Coordinates use normalized image
space with x to the right and y down. Positive is the left-hand cross-product side
of the directed start-to-end line in that coordinate system. The caller owns track
lifecycle, identity, persistence and gap/coverage reporting.
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
