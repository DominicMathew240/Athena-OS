"""Compare timestamped directional events within a declared tolerance.

This does not produce labels or validate a model. Earliest compatible events are
paired one-to-one per direction, maximizing the number of pairs for a fixed symmetric
time tolerance; this policy does not minimize total timing error among equally sized
matchings. Unmatched references are misses, unmatched predictions are extras.
"""
from __future__ import annotations

from dataclasses import dataclass

from .crossings import Crossing, _number

DIRECTIONS = ("negative_to_positive", "positive_to_negative")


@dataclass(frozen=True)
class Match:
    reference: Crossing
    predicted: Crossing

    @property
    def offset(self):
        return self.predicted.time - self.reference.time


@dataclass(frozen=True)
class Comparison:
    tolerance: float
    matches: tuple[Match, ...]
    missed: tuple[Crossing, ...]
    extra: tuple[Crossing, ...]


def _validate(events):
    events = tuple(events)
    for event in events:
        if not isinstance(event, Crossing) or event.direction not in DIRECTIONS or not _number(event.time) or event.time < 0:
            raise ValueError("Each event needs a supported direction and a finite nonnegative timestamp.")
    return events


def compare_crossings(reference, predicted, *, tolerance):
    if not _number(tolerance) or tolerance < 0:
        raise ValueError("Tolerance must be a finite nonnegative number of seconds.")
    reference, predicted = _validate(reference), _validate(predicted)
    matches, missed, extra = [], [], []
    for direction in DIRECTIONS:
        expected = sorted((e for e in reference if e.direction == direction), key=lambda e: e.time)
        actual = sorted((e for e in predicted if e.direction == direction), key=lambda e: e.time)
        i = j = 0
        while i < len(expected) and j < len(actual):
            difference = actual[j].time - expected[i].time
            if difference < -tolerance:
                extra.append(actual[j])
                j += 1
            elif difference > tolerance:
                missed.append(expected[i])
                i += 1
            else:
                matches.append(Match(reference=expected[i], predicted=actual[j]))
                i += 1
                j += 1
        missed.extend(expected[i:])
        extra.extend(actual[j:])
    return Comparison(tolerance=tolerance, matches=tuple(matches), missed=tuple(missed), extra=tuple(extra))
