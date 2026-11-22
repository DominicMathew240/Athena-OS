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

    def summary(self, *, predicted_source: str = "detector_crossing_episodes") -> dict:
        """Full T26 replay comparison report: matched pairs, misses, extras, timing offsets, and per-direction totals."""
        match_rows = [
            {
                "direction": m.reference.direction,
                "reference_time": round(float(m.reference.time), 6),
                "predicted_time": round(float(m.predicted.time), 6),
                "offset_seconds": round(float(m.offset), 6),
                "abs_offset_seconds": round(abs(float(m.offset)), 6),
            }
            for m in self.matches
        ]
        missed_rows = [
            {"direction": e.direction, "time": round(float(e.time), 6)}
            for e in self.missed
        ]
        extra_rows = [
            {"direction": e.direction, "time": round(float(e.time), 6)}
            for e in self.extra
        ]
        offsets = [float(m.offset) for m in self.matches]
        abs_offsets = [abs(v) for v in offsets]
        timing = {
            "count": len(offsets),
            "mean_offset_seconds": round(sum(offsets) / len(offsets), 6) if offsets else None,
            "mean_abs_offset_seconds": round(sum(abs_offsets) / len(abs_offsets), 6) if abs_offsets else None,
            "max_abs_offset_seconds": round(max(abs_offsets), 6) if abs_offsets else None,
        }
        per_direction = {}
        for direction in DIRECTIONS:
            dir_matches = [m for m in self.matches if m.reference.direction == direction]
            dir_missed = [e for e in self.missed if e.direction == direction]
            dir_extra = [e for e in self.extra if e.direction == direction]
            dir_abs = [abs(float(m.offset)) for m in dir_matches]
            per_direction[direction] = {
                "reference_count": len(dir_matches) + len(dir_missed),
                "predicted_count": len(dir_matches) + len(dir_extra),
                "matched": len(dir_matches),
                "missed": len(dir_missed),
                "extra": len(dir_extra),
                "mean_abs_offset_seconds": round(sum(dir_abs) / len(dir_abs), 6) if dir_abs else None,
            }
        return {
            "tolerance_seconds": float(self.tolerance),
            "predicted_source": predicted_source,
            "matched": len(self.matches),
            "missed": len(self.missed),
            "extra": len(self.extra),
            "matches": match_rows,
            "missed_events": missed_rows,
            "extra_events": extra_rows,
            "timing": timing,
            "per_direction": per_direction,
        }


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


def compare_ledger_events(events, *, tolerance: float = 0.5) -> Comparison:
    """Compare committed ``reference_crossing`` vs ``detector_crossing`` ledger rows (T26)."""
    from ..models import OperationEvent
    from .ledger import unquantize_timestamp

    reference: list[Crossing] = []
    predicted: list[Crossing] = []
    for row in events:
        payload = row.payload if isinstance(row.payload, dict) else {}
        direction = payload.get("direction")
        if direction not in DIRECTIONS:
            continue
        ts = unquantize_timestamp(row.timestamp) if isinstance(row.timestamp, str) else float(row.timestamp)
        if row.kind == OperationEvent.KIND_REFERENCE:
            reference.append(Crossing(direction=direction, time=ts))
        elif row.kind == OperationEvent.KIND_DETECTOR:
            predicted.append(Crossing(direction=direction, time=ts))
    return compare_crossings(reference, predicted, tolerance=tolerance)
