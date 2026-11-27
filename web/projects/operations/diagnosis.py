"""T32: route replay comparison failures to a plain diagnosis.

Categories (mutually preferred for the primary recommendation):

* ``source_view`` — the miss/extra sits in a coverage gap or the source never
  delivered frames the detector could analyse.
* ``geometry`` — objects were seen near the expected time but no crossing was
  formed, or no counting line is pinned.
* ``rules`` — the detector produced a same-time opposite-direction event
  (line polarity / recipe direction), so the counting rules need review.
* ``recognition`` — clean coverage and a pinned line, but nothing was detected
  near the reference; only then is training the next step.
* ``none`` — matches look right, or there is nothing yet to diagnose
  (no detector bound). Correct recognition never recommends training.
"""

from __future__ import annotations

from typing import Any, Iterable

CATEGORIES = ("source_view", "geometry", "rules", "recognition", "none")

# How close a detection/extra must be to a reference miss to count as "near".
NEAR_SECONDS = 1.0

NEXT_STEPS = {
    "none": "recognition_looks_correct",
    "source_view": "fix_source_or_recording",
    "geometry": "adjust_geometry",
    "rules": "review_counting_rules",
    "recognition": "improve_recognition",
    "bind_model": "bind_compatible_model",
}

PLAIN = {
    "none": "The replay matches the labeled reference events. Recognition does not need training.",
    "source_view": "The miss lines up with a gap or missing frames in the recording. Fix the source or re-record before changing the model.",
    "geometry": "Objects were found near the expected time, but they did not cross the counting line. Adjust the line or bay geometry.",
    "rules": "A detector event arrived at the right time with the wrong direction. Review the counting direction and recipe settings.",
    "recognition": "Coverage and geometry look fine, but the detector did not see the event. Capture examples and improve recognition.",
    "bind_model": "No compatible model ran on this replay. Bind an approved model before diagnosing recognition.",
}


def _time_in_gap(time: float, gaps: Iterable[dict]) -> bool:
    for gap in gaps or ():
        try:
            start = float(gap.get("start_pts"))
            end = float(gap.get("end_pts"))
        except (TypeError, ValueError):
            continue
        if start <= time <= end:
            return True
    return False


def _near(times: Iterable[float], target: float, window: float = NEAR_SECONDS) -> bool:
    return any(abs(float(t) - target) <= window for t in times)


def _category_priority(counts: dict[str, int]) -> str:
    """Pick the primary category: source before geometry before rules before recognition."""
    order = ("source_view", "geometry", "rules", "recognition")
    for key in order:
        if counts.get(key, 0) > 0:
            return key
    return "none"


def diagnose_replay(
    *,
    comparison: dict,
    gap_accounting: dict | None = None,
    has_geometry: bool = True,
    has_detector: bool = True,
    detection_times: Iterable[float] | None = None,
    track_cross_times: Iterable[float] | None = None,
) -> dict[str, Any]:
    """Diagnose crossing replay misses/extras into source/geometry/rules/recognition."""
    gap_accounting = gap_accounting or {}
    gaps = list(gap_accounting.get("gaps") or [])
    detection_times = list(detection_times or [])
    track_cross_times = list(track_cross_times or [])
    predicted_source = comparison.get("predicted_source") or ""
    tolerance = float(comparison.get("tolerance_seconds") or 0.5)

    if not has_detector or predicted_source == "reference_placeholder":
        # Placeholder "comparison" only proves the recipe wires; it is not a model test.
        matched = int(comparison.get("matched") or 0)
        missed = int(comparison.get("missed") or 0)
        extra = int(comparison.get("extra") or 0)
        if not has_detector:
            return {
                "schema": "t32.replay_diagnosis.v1",
                "primary_category": "none",
                "recommend_training": False,
                "next_step": NEXT_STEPS["bind_model"],
                "plain": PLAIN["bind_model"],
                "counts": {
                    "matched": matched,
                    "missed": missed,
                    "extra": extra,
                    "source_view": 0,
                    "geometry": 0,
                    "rules": 0,
                    "recognition": 0,
                },
                "items": [],
                "category_totals": {"source_view": 0, "geometry": 0, "rules": 0, "recognition": 0},
            }

    missed_events = list(comparison.get("missed_events") or [])
    extra_events = list(comparison.get("extra_events") or [])
    items: list[dict] = []
    cat_counts = {"source_view": 0, "geometry": 0, "rules": 0, "recognition": 0}

    # Opposite-direction extras near a miss → rules (pair them first).
    unpaired_extras = list(extra_events)
    for miss in missed_events:
        m_time = float(miss.get("time"))
        m_dir = miss.get("direction")
        category = None
        reason = ""

        if _time_in_gap(m_time, gaps) or (
            int(gap_accounting.get("source_gap_count") or 0) > 0
            and _time_in_gap(m_time, gaps)
        ):
            category = "source_view"
            reason = "reference_time_falls_in_coverage_gap"
        elif not has_geometry or predicted_source == "reference_placeholder":
            category = "geometry"
            reason = "counting_line_missing_or_unresolved"
        else:
            # Opposite-direction extra near this miss → rules.
            paired_extra = None
            for i, extra in enumerate(unpaired_extras):
                if extra.get("direction") == m_dir:
                    continue
                if abs(float(extra.get("time")) - m_time) <= max(tolerance, NEAR_SECONDS):
                    paired_extra = i
                    break
            if paired_extra is not None:
                category = "rules"
                reason = "opposite_direction_event_at_similar_time"
                unpaired_extras.pop(paired_extra)
            elif _near(detection_times, m_time) and not _near(track_cross_times, m_time, max(tolerance, NEAR_SECONDS)):
                category = "geometry"
                reason = "detections_near_time_without_crossing"
            elif not _near(detection_times, m_time):
                # Prefer source_view when coverage is incomplete even if the gap list
                # did not contain this exact second (sampled policy / unanalysed frames).
                if (
                    not gap_accounting.get("full_frame_coverage", False)
                    and int(gap_accounting.get("unanalysed_frame_count") or 0) > 0
                    and not detection_times
                ):
                    category = "source_view"
                    reason = "frames_not_analysed_near_reference"
                else:
                    category = "recognition"
                    reason = "no_detection_near_reference_on_covered_timeline"
            else:
                category = "geometry"
                reason = "detections_present_but_no_matching_crossing"

        cat_counts[category] += 1
        items.append(
            {
                "kind": "missed",
                "direction": m_dir,
                "time": m_time,
                "category": category,
                "reason": reason,
            }
        )

    for extra in unpaired_extras:
        e_time = float(extra.get("time"))
        if _time_in_gap(e_time, gaps):
            category = "source_view"
            reason = "extra_event_in_coverage_gap"
        else:
            # Lone extras without a paired miss are usually recognition (false positive)
            # unless geometry is missing.
            if not has_geometry:
                category = "geometry"
                reason = "counting_line_missing_or_unresolved"
            else:
                category = "recognition"
                reason = "extra_crossing_without_reference"
        cat_counts[category] += 1
        items.append(
            {
                "kind": "extra",
                "direction": extra.get("direction"),
                "time": e_time,
                "category": category,
                "reason": reason,
            }
        )

    primary = _category_priority(cat_counts)
    if primary == "none" and int(comparison.get("matched") or 0) == 0 and not items:
        # Empty both sides — nothing to fix via training.
        primary = "none"

    recommend_training = primary == "recognition"
    next_step = NEXT_STEPS[primary]
    plain = PLAIN[primary]

    return {
        "schema": "t32.replay_diagnosis.v1",
        "primary_category": primary,
        "recommend_training": recommend_training,
        "next_step": next_step,
        "plain": plain,
        "counts": {
            "matched": int(comparison.get("matched") or 0),
            "missed": int(comparison.get("missed") or 0),
            "extra": int(comparison.get("extra") or 0),
            **cat_counts,
        },
        "items": items,
        "category_totals": dict(cat_counts),
    }


def diagnose_parking(
    *,
    parking: dict,
    gap_accounting: dict | None = None,
) -> dict[str, Any]:
    """Diagnose parking false-free / missed transitions without defaulting to training."""
    gap_accounting = gap_accounting or {}
    gaps = list(gap_accounting.get("gaps") or [])
    items: list[dict] = []
    cat_counts = {"source_view": 0, "geometry": 0, "rules": 0, "recognition": 0}

    for case in parking.get("false_free_cases") or []:
        t = case.get("time")
        try:
            t_f = float(t)
        except (TypeError, ValueError):
            t_f = None
        if t_f is not None and _time_in_gap(t_f, gaps):
            category = "source_view"
            reason = "false_free_during_coverage_gap"
        else:
            category = "recognition"
            reason = "occupied_bay_predicted_free"
        cat_counts[category] += 1
        items.append(
            {
                "kind": "false_free",
                "bay_id": case.get("bay_id"),
                "time": t_f,
                "category": category,
                "reason": reason,
            }
        )

    for miss in (parking.get("transition_metrics") or {}).get("missed") or []:
        t = miss.get("time")
        try:
            t_f = float(t)
        except (TypeError, ValueError):
            t_f = None
        if t_f is not None and _time_in_gap(t_f, gaps):
            category = "source_view"
            reason = "missed_transition_during_coverage_gap"
        else:
            category = "recognition"
            reason = "missed_bay_state_transition"
        cat_counts[category] += 1
        items.append(
            {
                "kind": "missed_transition",
                "bay_id": miss.get("bay_id"),
                "time": t_f,
                "category": category,
                "reason": reason,
            }
        )

    primary = _category_priority(cat_counts)
    return {
        "schema": "t32.parking_diagnosis.v1",
        "primary_category": primary,
        "recommend_training": primary == "recognition",
        "next_step": NEXT_STEPS[primary],
        "plain": PLAIN[primary] if primary != "none" else "No parking mismatches to diagnose.",
        "counts": {
            "false_free": int(parking.get("false_free_count") or 0),
            **cat_counts,
        },
        "items": items,
        "category_totals": dict(cat_counts),
    }


def detection_times_from_detector(detector: dict | None) -> list[float]:
    """PTS values of frames that carried at least one box."""
    if not detector:
        return []
    times: list[float] = []
    for frame in detector.get("frames") or []:
        if frame.get("detections"):
            try:
                times.append(float(frame["pts"]))
            except (KeyError, TypeError, ValueError):
                continue
    return times


def build_execution_diagnosis(
    *,
    comparison_summary: dict,
    gap_accounting: dict | None,
    detector: dict | None,
    has_geometry: bool,
    parking: dict | None = None,
) -> dict[str, Any]:
    """Compose the diagnosis block stored on a succeeded execution result."""
    has_detector = detector is not None
    replay = diagnose_replay(
        comparison=comparison_summary,
        gap_accounting=gap_accounting,
        has_geometry=has_geometry,
        has_detector=has_detector,
        detection_times=detection_times_from_detector(detector),
        track_cross_times=[],  # crossings already folded into comparison predicted stream
    )
    block: dict[str, Any] = {"comparison": replay}
    if parking:
        block["parking"] = diagnose_parking(parking=parking, gap_accounting=gap_accounting)
        # Overall training flag: only if either branch truly needs recognition work
        # and neither is blocked on source/geometry/rules as primary.
        block["recommend_training"] = bool(
            replay["recommend_training"] or block["parking"]["recommend_training"]
        )
        # Prefer the more actionable primary (source > geometry > rules > recognition)
        prefs = {"source_view": 0, "geometry": 1, "rules": 2, "recognition": 3, "none": 4}
        a, b = replay["primary_category"], block["parking"]["primary_category"]
        block["primary_category"] = a if prefs.get(a, 9) <= prefs.get(b, 9) else b
    else:
        block["recommend_training"] = replay["recommend_training"]
        block["primary_category"] = replay["primary_category"]
    return block
