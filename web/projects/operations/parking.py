"""Parking bay-to-vehicle matching (T29), temporal bay states (T30), and reference replay metrics (T31).

Contracts:
* **T29 — Bay-to-vehicle matching:**
  - Compares ``footprint_anchor`` (perspective-aware ground-contact anchor + bottom-third
    footprint for oblique camera views, with one-to-one vehicle assignment and occlusion
    detection) against ``polygon_overlap`` (naive full-box intersection ratio).
  - A large vehicle bounding box overlapping several bays in an oblique view does not
    automatically mark every intersected bay occupied: once a vehicle is assigned to its
    foreground footprint bay, any adjacent/background bay overlapped only by the upper
    box is marked ``unknown`` with ``reason="occluded_by_adjacent_vehicle"`` (never
    ``occupied`` and never ``free``).
  - Competing assignments within ``ambiguity_margin`` or a vehicle straddling two bay
    footprints are explicitly marked ``unknown`` (``reason="ambiguous_straddle_or_competition"``).
* **T30 — Temporal bay states:**
  - Supports ``free``, ``occupied``, ``unknown``, and ``disabled`` with configured
    confirmation delays (``occupied_confirm_seconds``, default 2.0 s;
    ``free_confirm_seconds``, default 5.0 s) and stale-feed timeout
    (``stale_feed_timeout_seconds``, default 15.0 s).
  - Stationary cars remain ``occupied`` continuously from spatial evidence (no motion
    is never treated as empty); ``last_observed_pts`` updates on every valid frame while
    ``last_changed_pts`` preserves the transition timestamp.
  - State history is deterministic and hashes to a canonical ``state_history_sha256``.
* **T31 — Parking reference replay and metrics:**
  - Evaluates reference bay-time labels and reference transition timestamps against the
    replayed state history, producing per-bay and overall confusion matrices, occupied/free
    macro F1, ``unknown_rate``, known-state coverage, transition delays, and an explicit
    ``false_free_cases`` list (where an occupied bay was predicted free).
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass

from ..bays import (
    BAY_DISABLED,
    BAY_STATE_DISABLED,
    BAY_STATE_FREE,
    BAY_STATE_OCCUPIED,
    BAY_STATE_UNKNOWN,
    BAY_UNOBSERVABLE,
    bay_effective_observability,
    operation_bays,
    summarize_bays_and_zones,
)

VEHICLE_LABELS = ("car", "truck", "bus", "van", "motorcycle", "vehicle")
STRATEGY_FOOTPRINT_ANCHOR = "footprint_anchor"
STRATEGY_POLYGON_OVERLAP = "polygon_overlap"
SUPPORTED_MATCHING_STRATEGIES = (STRATEGY_FOOTPRINT_ANCHOR, STRATEGY_POLYGON_OVERLAP)


class ParkingError(ValueError):
    """Invalid bay geometry, detection stream, temporal parameter, or reference label."""


def _polygon_area(poly: list[tuple[float, float]]) -> float:
    if len(poly) < 3:
        return 0.0
    area = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        area += x1 * y2 - x2 * y1
    return 0.5 * abs(area)


def _point_in_polygon(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
    """Ray-casting + boundary-inclusive check for a point in a simple polygon."""
    px, py = point
    inside = False
    n = len(polygon)
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        # Check if point sits on the segment Within 1e-9.
        cross = (px - x1) * (y2 - y1) - (py - y1) * (x2 - x1)
        if abs(cross) <= 1e-9:
            if min(x1, x2) - 1e-9 <= px <= max(x1, x2) + 1e-9 and min(y1, y2) - 1e-9 <= py <= max(y1, y2) + 1e-9:
                return True
        if (y1 > py) != (y2 > py):
            x_intersect = (x2 - x1) * (py - y1) / (y2 - y1) + x1
            if px <= x_intersect + 1e-12:
                inside = not inside
    return inside


def _clip_polygon_to_rect(
    poly: list[tuple[float, float]],
    rx0: float,
    ry0: float,
    rx1: float,
    ry1: float,
) -> list[tuple[float, float]]:
    """Sutherland–Hodgman clipping of a polygon against an axis-aligned rectangle."""

    def clip_edge(vertices: list[tuple[float, float]], inside_fn, intersect_fn) -> list[tuple[float, float]]:
        if not vertices:
            return []
        out: list[tuple[float, float]] = []
        prev = vertices[-1]
        prev_in = inside_fn(prev)
        for curr in vertices:
            curr_in = inside_fn(curr)
            if curr_in:
                if not prev_in:
                    out.append(intersect_fn(prev, curr))
                out.append(curr)
            elif prev_in:
                out.append(intersect_fn(prev, curr))
            prev = curr
            prev_in = curr_in
        return out

    pts = list(poly)
    # Left: x >= rx0
    pts = clip_edge(
        pts,
        lambda p: p[0] >= rx0 - 1e-12,
        lambda a, b: (rx0, a[1] + (b[1] - a[1]) * (rx0 - a[0]) / ((b[0] - a[0]) or 1e-12)),
    )
    # Right: x <= rx1
    pts = clip_edge(
        pts,
        lambda p: p[0] <= rx1 + 1e-12,
        lambda a, b: (rx1, a[1] + (b[1] - a[1]) * (rx1 - a[0]) / ((b[0] - a[0]) or 1e-12)),
    )
    # Top: y >= ry0
    pts = clip_edge(
        pts,
        lambda p: p[1] >= ry0 - 1e-12,
        lambda a, b: (a[0] + (b[0] - a[0]) * (ry0 - a[1]) / ((b[1] - a[1]) or 1e-12), ry0),
    )
    # Bottom: y <= ry1
    pts = clip_edge(
        pts,
        lambda p: p[1] <= ry1 + 1e-12,
        lambda a, b: (a[0] + (b[0] - a[0]) * (ry1 - a[1]) / ((b[1] - a[1]) or 1e-12), ry1),
    )
    return pts


def _box_overlap_fraction(bay_poly: list[tuple[float, float]], box: dict) -> float:
    bay_area = _polygon_area(bay_poly)
    if bay_area <= 0:
        return 0.0
    x0 = float(box["x"])
    y0 = float(box["y"])
    x1 = x0 + float(box["width"])
    y1 = y0 + float(box["height"])
    clipped = _clip_polygon_to_rect(bay_poly, x0, y0, x1, y1)
    return min(1.0, _polygon_area(clipped) / bay_area)


def _footprint_overlap_fraction(bay_poly: list[tuple[float, float]], box: dict) -> float:
    """Overlap between the bay polygon and the vehicle's bottom-third ground footprint.

    Returns the larger of (clipped_area / bay_area) and (clipped_area / footprint_area)
    so a vehicle whose tires/footprint sit inside or straddle a bay is detected accurately.
    """
    bay_area = _polygon_area(bay_poly)
    if bay_area <= 0:
        return 0.0
    w = float(box["width"])
    h = float(box["height"])
    fp_area = w * (h / 3.0)
    if fp_area <= 0:
        return 0.0
    x0 = float(box["x"])
    y0 = float(box["y"]) + (2.0 / 3.0) * h
    x1 = x0 + w
    y1 = float(box["y"]) + h
    clipped = _clip_polygon_to_rect(bay_poly, x0, y0, x1, y1)
    inter = _polygon_area(clipped)
    return min(1.0, max(inter / bay_area, inter / fp_area))


def _ground_anchor(box: dict) -> tuple[float, float]:
    """Bottom-center ground contact point slightly inset into the tires/footprint."""
    x = float(box["x"]) + 0.5 * float(box["width"])
    y = float(box["y"]) + 0.88 * float(box["height"])
    return (x, y)


def match_vehicles_to_bays(
    bays: list[dict],
    detections: list[dict] | tuple[dict, ...],
    *,
    strategy: str = STRATEGY_FOOTPRINT_ANCHOR,
    allowed_labels: tuple[str, ...] = VEHICLE_LABELS,
    min_overlap: float = 0.20,
    min_footprint_overlap: float = 0.15,
    occlusion_overlap_threshold: float = 0.25,
    ambiguity_margin: float = 0.05,
) -> dict:
    """Match one frame's vehicle detections to versioned polygon bays (T29).

    Returns a deterministic dict containing ``strategy``, ``bays`` (one match
    record per bay in input order), and summary counts.
    """
    if strategy not in SUPPORTED_MATCHING_STRATEGIES:
        raise ParkingError(
            f"Unsupported bay matching strategy '{strategy}'. Choose one of: {', '.join(SUPPORTED_MATCHING_STRATEGIES)}."
        )
    validated_bays = operation_bays(bays)
    vehicles = [
        (idx, det)
        for idx, det in enumerate(detections or ())
        if isinstance(det, dict) and det.get("label") in allowed_labels
    ]
    bay_polys = {
        bay["id"]: [(float(c[0]), float(c[1])) for c in bay["corners"]]
        for bay in validated_bays
    }

    if strategy == STRATEGY_POLYGON_OVERLAP:
        # Naive overlap strategy: any bay whose polygon is covered >= min_overlap by ANY
        # vehicle box is marked occupied (demonstrating why naive overlap over-marks
        # background bays on oblique views).
        bay_records: list[dict] = []
        for bay in validated_bays:
            obs = bay_effective_observability(bay)
            if obs == BAY_DISABLED:
                bay_records.append(
                    {
                        "bay_id": bay["id"],
                        "bay_name": bay["name"],
                        "zone": bay.get("zone"),
                        "observation": BAY_STATE_DISABLED,
                        "reason": "disabled_bay",
                        "vehicle_index": None,
                        "overlap": 0.0,
                        "ambiguous": False,
                        "occluded": False,
                    }
                )
                continue
            if obs == BAY_UNOBSERVABLE:
                bay_records.append(
                    {
                        "bay_id": bay["id"],
                        "bay_name": bay["name"],
                        "zone": bay.get("zone"),
                        "observation": BAY_STATE_UNKNOWN,
                        "reason": "unobservable_bay",
                        "vehicle_index": None,
                        "overlap": 0.0,
                        "ambiguous": False,
                        "occluded": True,
                    }
                )
                continue
            poly = bay_polys[bay["id"]]
            best_idx = None
            best_ov = 0.0
            for v_idx, det in vehicles:
                ov = _box_overlap_fraction(poly, det)
                if ov > best_ov:
                    best_ov = ov
                    best_idx = v_idx
            if best_ov >= min_overlap and best_idx is not None:
                bay_records.append(
                    {
                        "bay_id": bay["id"],
                        "bay_name": bay["name"],
                        "zone": bay.get("zone"),
                        "observation": BAY_STATE_OCCUPIED,
                        "reason": "polygon_overlap_match",
                        "vehicle_index": best_idx,
                        "overlap": round(best_ov, 6),
                        "ambiguous": False,
                        "occluded": False,
                    }
                )
            else:
                bay_records.append(
                    {
                        "bay_id": bay["id"],
                        "bay_name": bay["name"],
                        "zone": bay.get("zone"),
                        "observation": BAY_STATE_FREE,
                        "reason": "no_vehicle_overlap",
                        "vehicle_index": None,
                        "overlap": round(best_ov, 6),
                        "ambiguous": False,
                        "occluded": False,
                    }
                )
        return {
            "strategy": strategy,
            "bays": bay_records,
        }

    # STRATEGY_FOOTPRINT_ANCHOR: perspective-aware matching for oblique views.
    # 1. For each vehicle, evaluate candidate observable bays by ground-anchor containment
    #    and bottom-third footprint overlap.
    observable_ids = [
        bay["id"]
        for bay in validated_bays
        if bay_effective_observability(bay) == "observable"
    ]
    vehicle_primary_bay: dict[int, str] = {}
    ambiguous_bay_ids: set[str] = set()
    bay_assigned_vehicle: dict[str, tuple[int, float]] = {}

    for v_idx, det in vehicles:
        anchor = _ground_anchor(det)
        candidates: list[tuple[float, bool, str]] = []
        for bay_id in observable_ids:
            poly = bay_polys[bay_id]
            anchor_in = _point_in_polygon(anchor, poly)
            fp_ov = _footprint_overlap_fraction(poly, det)
            full_ov = _box_overlap_fraction(poly, det)
            if anchor_in or fp_ov >= min_footprint_overlap:
                score = (1.0 if anchor_in else 0.0) + fp_ov + 0.25 * full_ov
                candidates.append((score, anchor_in, bay_id))
        candidates.sort(key=lambda item: (-item[0], item[2]))
        if not candidates:
            continue
        # Check if the vehicle straddles two bays almost equally (both have significant footprint overlap).
        if len(candidates) >= 2:
            top_score, top_anchor, top_bay = candidates[0]
            second_score, second_anchor, second_bay = candidates[1]
            second_fp = _footprint_overlap_fraction(bay_polys[second_bay], det)
            if (top_anchor and second_anchor) or (
                second_fp >= min_footprint_overlap and abs(top_score - second_score) <= ambiguity_margin
            ):
                ambiguous_bay_ids.add(top_bay)
                ambiguous_bay_ids.add(second_bay)
                continue
        chosen_score, _chosen_anchor, chosen_bay = candidates[0]
        if chosen_bay in bay_assigned_vehicle:
            # Two distinct vehicles claim the same bay footprint: mark occupied by higher score.
            prev_idx, prev_score = bay_assigned_vehicle[chosen_bay]
            if chosen_score > prev_score:
                vehicle_primary_bay.pop(prev_idx, None)
                bay_assigned_vehicle[chosen_bay] = (v_idx, chosen_score)
                vehicle_primary_bay[v_idx] = chosen_bay
        else:
            bay_assigned_vehicle[chosen_bay] = (v_idx, chosen_score)
            vehicle_primary_bay[v_idx] = chosen_bay

    # 2. Any unassigned observable bay whose polygon is heavily overlapped by a vehicle box
    #    (either assigned to an adjacent foreground bay or unassigned with no clear footprint)
    #    is OCCLUDED -> observation="unknown", never "occupied" and never "free"!
    occluded_bay_ids: dict[str, int] = {}
    for bay_id in observable_ids:
        if bay_id in bay_assigned_vehicle or bay_id in ambiguous_bay_ids:
            continue
        poly = bay_polys[bay_id]
        for v_idx, det in vehicles:
            full_ov = _box_overlap_fraction(poly, det)
            if full_ov >= occlusion_overlap_threshold:
                occluded_bay_ids[bay_id] = v_idx
                break

    bay_records = []
    for bay in validated_bays:
        bay_id = bay["id"]
        obs = bay_effective_observability(bay)
        if obs == BAY_DISABLED:
            bay_records.append(
                {
                    "bay_id": bay_id,
                    "bay_name": bay["name"],
                    "zone": bay.get("zone"),
                    "observation": BAY_STATE_DISABLED,
                    "reason": "disabled_bay",
                    "vehicle_index": None,
                    "overlap": 0.0,
                    "ambiguous": False,
                    "occluded": False,
                }
            )
        elif obs == BAY_UNOBSERVABLE:
            bay_records.append(
                {
                    "bay_id": bay_id,
                    "bay_name": bay["name"],
                    "zone": bay.get("zone"),
                    "observation": BAY_STATE_UNKNOWN,
                    "reason": "unobservable_bay",
                    "vehicle_index": None,
                    "overlap": 0.0,
                    "ambiguous": False,
                    "occluded": True,
                }
            )
        elif bay_id in ambiguous_bay_ids:
            bay_records.append(
                {
                    "bay_id": bay_id,
                    "bay_name": bay["name"],
                    "zone": bay.get("zone"),
                    "observation": BAY_STATE_UNKNOWN,
                    "reason": "ambiguous_straddle_or_competition",
                    "vehicle_index": None,
                    "overlap": 0.0,
                    "ambiguous": True,
                    "occluded": False,
                }
            )
        elif bay_id in bay_assigned_vehicle:
            v_idx, _score = bay_assigned_vehicle[bay_id]
            det = dict(vehicles[v_idx][1])
            ov = _box_overlap_fraction(bay_polys[bay_id], det)
            bay_records.append(
                {
                    "bay_id": bay_id,
                    "bay_name": bay["name"],
                    "zone": bay.get("zone"),
                    "observation": BAY_STATE_OCCUPIED,
                    "reason": "footprint_anchor_match",
                    "vehicle_index": v_idx,
                    "overlap": round(ov, 6),
                    "ambiguous": False,
                    "occluded": False,
                }
            )
        elif bay_id in occluded_bay_ids:
            v_idx = occluded_bay_ids[bay_id]
            det = dict(vehicles[v_idx][1])
            ov = _box_overlap_fraction(bay_polys[bay_id], det)
            bay_records.append(
                {
                    "bay_id": bay_id,
                    "bay_name": bay["name"],
                    "zone": bay.get("zone"),
                    "observation": BAY_STATE_UNKNOWN,
                    "reason": "occluded_by_adjacent_vehicle",
                    "vehicle_index": v_idx,
                    "overlap": round(ov, 6),
                    "ambiguous": False,
                    "occluded": True,
                }
            )
        else:
            bay_records.append(
                {
                    "bay_id": bay_id,
                    "bay_name": bay["name"],
                    "zone": bay.get("zone"),
                    "observation": BAY_STATE_FREE,
                    "reason": "clear_observable_view",
                    "vehicle_index": None,
                    "overlap": 0.0,
                    "ambiguous": False,
                    "occluded": False,
                }
            )

    return {
        "strategy": strategy,
        "bays": bay_records,
    }


def compare_matching_strategies(
    bays: list[dict],
    detections: list[dict] | tuple[dict, ...],
) -> dict:
    """Compare ``footprint_anchor`` and ``polygon_overlap`` on the same frame (T29)."""
    footprint = match_vehicles_to_bays(bays, detections, strategy=STRATEGY_FOOTPRINT_ANCHOR)
    overlap = match_vehicles_to_bays(bays, detections, strategy=STRATEGY_POLYGON_OVERLAP)
    fp_by_id = {b["bay_id"]: b for b in footprint["bays"]}
    ov_by_id = {b["bay_id"]: b for b in overlap["bays"]}
    differences = []
    for bay_id, fp_row in fp_by_id.items():
        ov_row = ov_by_id[bay_id]
        if fp_row["observation"] != ov_row["observation"]:
            differences.append(
                {
                    "bay_id": bay_id,
                    "bay_name": fp_row["bay_name"],
                    "footprint_anchor_observation": fp_row["observation"],
                    "footprint_anchor_reason": fp_row["reason"],
                    "polygon_overlap_observation": ov_row["observation"],
                    "polygon_overlap_reason": ov_row["reason"],
                }
            )
    return {
        "footprint_anchor": footprint,
        "polygon_overlap": overlap,
        "disagreement_count": len(differences),
        "disagreements": differences,
    }


def evaluate_temporal_bay_states(
    bays: list[dict],
    frames,
    *,
    occupied_confirm_seconds: float = 2.0,
    free_confirm_seconds: float = 5.0,
    stale_feed_timeout_seconds: float = 15.0,
    matching_strategy: str = STRATEGY_FOOTPRINT_ANCHOR,
    query_time: float | None = None,
) -> dict:
    """Evaluate reproducible temporal bay states across an ordered frame sequence (T30).

    ``frames`` is an iterable of ``FrameDetections`` (or dicts with ``pts``,
    ``frame_index``, ``session``, ``detections``).
    """
    for name, val in (
        ("occupied_confirm_seconds", occupied_confirm_seconds),
        ("free_confirm_seconds", free_confirm_seconds),
        ("stale_feed_timeout_seconds", stale_feed_timeout_seconds),
    ):
        if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val) or val < 0:
            raise ParkingError(f"{name} must be a finite nonnegative number of seconds.")

    validated_bays = operation_bays(bays)
    ordered_frames = []
    for idx, f in enumerate(frames or ()):
        if isinstance(f, dict):
            pts = float(f["pts"])
            f_idx = int(f.get("frame_index", idx))
            sess = int(f.get("session", 1))
            dets = f.get("detections") or ()
        else:
            pts = float(f.pts)
            f_idx = int(f.frame_index)
            sess = int(f.session)
            dets = f.detections
        ordered_frames.append({"frame_index": f_idx, "pts": pts, "session": sess, "detections": dets})
    ordered_frames.sort(key=lambda item: (item["pts"], item["frame_index"]))

    # Per-bay state machine tracker.
    state_by_bay: dict[str, dict] = {}
    for bay in validated_bays:
        obs = bay_effective_observability(bay)
        init_state = BAY_STATE_DISABLED if obs == BAY_DISABLED else BAY_STATE_UNKNOWN
        init_reason = (
            "disabled_bay"
            if obs == BAY_DISABLED
            else ("unobservable_bay" if obs == BAY_UNOBSERVABLE else "awaiting_initial_evidence")
        )
        state_by_bay[bay["id"]] = {
            "bay_id": bay["id"],
            "bay_name": bay["name"],
            "zone": bay.get("zone"),
            "observability": obs,
            "state": init_state,
            "reason": init_reason,
            "pending_state": None,
            "pending_since_pts": None,
            "last_observed_pts": None,
            "last_changed_pts": None,
        }

    transitions: list[dict] = []
    timeline_snapshots: list[dict] = []
    prev_pts: float | None = None

    for frame in ordered_frames:
        pts = round(frame["pts"], 6)
        # If the stream had a stale-feed outage gap before this frame (> stale_feed_timeout_seconds),
        # transition all observable bays to unknown at prev_pts + stale_feed_timeout_seconds first.
        if prev_pts is not None and (pts - prev_pts) > stale_feed_timeout_seconds:
            stale_pts = round(prev_pts + stale_feed_timeout_seconds, 6)
            for bay in validated_bays:
                tracker = state_by_bay[bay["id"]]
                if tracker["observability"] != "observable":
                    continue
                tracker["pending_state"] = None
                tracker["pending_since_pts"] = None
                if tracker["state"] != BAY_STATE_UNKNOWN:
                    old_state = tracker["state"]
                    tracker["state"] = BAY_STATE_UNKNOWN
                    tracker["reason"] = "stale_feed"
                    tracker["last_changed_pts"] = stale_pts
                    transitions.append(
                        {
                            "bay_id": bay["id"],
                            "bay_name": bay["name"],
                            "zone": bay.get("zone"),
                            "time": stale_pts,
                            "from_state": old_state,
                            "to_state": BAY_STATE_UNKNOWN,
                            "reason": "stale_feed",
                        }
                    )

        match_report = match_vehicles_to_bays(
            validated_bays,
            frame["detections"],
            strategy=matching_strategy,
        )
        match_by_id = {m["bay_id"]: m for m in match_report["bays"]}

        frame_bay_states: list[dict] = []
        for bay in validated_bays:
            bay_id = bay["id"]
            tracker = state_by_bay[bay_id]
            matched = match_by_id[bay_id]
            obs_evidence = matched["observation"]
            obs_reason = matched["reason"]

            if tracker["observability"] == BAY_DISABLED:
                tracker["last_observed_pts"] = pts
            elif tracker["observability"] == BAY_UNOBSERVABLE:
                tracker["last_observed_pts"] = pts
            else:
                tracker["last_observed_pts"] = pts
                if obs_evidence == BAY_STATE_UNKNOWN:
                    # Occluded or ambiguous evidence immediately takes precedence as Unknown.
                    tracker["pending_state"] = None
                    tracker["pending_since_pts"] = None
                    if tracker["state"] != BAY_STATE_UNKNOWN:
                        old_state = tracker["state"]
                        tracker["state"] = BAY_STATE_UNKNOWN
                        tracker["reason"] = obs_reason
                        tracker["last_changed_pts"] = pts
                        transitions.append(
                            {
                                "bay_id": bay_id,
                                "bay_name": bay["name"],
                                "zone": bay.get("zone"),
                                "time": pts,
                                "from_state": old_state,
                                "to_state": BAY_STATE_UNKNOWN,
                                "reason": obs_reason,
                            }
                        )
                    else:
                        tracker["reason"] = obs_reason
                elif obs_evidence in (BAY_STATE_OCCUPIED, BAY_STATE_FREE):
                    if tracker["state"] == obs_evidence:
                        # Evidence continues to support current confirmed state (e.g. stationary car).
                        tracker["pending_state"] = None
                        tracker["pending_since_pts"] = None
                        tracker["reason"] = obs_reason
                    else:
                        required_delay = (
                            occupied_confirm_seconds
                            if obs_evidence == BAY_STATE_OCCUPIED
                            else free_confirm_seconds
                        )
                        if tracker["pending_state"] != obs_evidence:
                            tracker["pending_state"] = obs_evidence
                            tracker["pending_since_pts"] = pts
                        elapsed = pts - float(tracker["pending_since_pts"])
                        if elapsed + 1e-9 >= required_delay:
                            old_state = tracker["state"]
                            tracker["state"] = obs_evidence
                            tracker["reason"] = obs_reason
                            tracker["last_changed_pts"] = pts
                            tracker["pending_state"] = None
                            tracker["pending_since_pts"] = None
                            transitions.append(
                                {
                                    "bay_id": bay_id,
                                    "bay_name": bay["name"],
                                    "zone": bay.get("zone"),
                                    "time": pts,
                                    "from_state": old_state,
                                    "to_state": obs_evidence,
                                    "reason": obs_reason,
                                }
                            )

            frame_bay_states.append(
                {
                    "bay_id": bay_id,
                    "bay_name": bay["name"],
                    "zone": bay.get("zone"),
                    "state": tracker["state"],
                    "pending_state": tracker["pending_state"],
                    "observation": obs_evidence,
                    "reason": tracker["reason"],
                    "last_observed_pts": tracker["last_observed_pts"],
                    "last_changed_pts": tracker["last_changed_pts"],
                }
            )

        timeline_snapshots.append(
            {
                "frame_index": frame["frame_index"],
                "pts": pts,
                "session": frame["session"],
                "bays": frame_bay_states,
            }
        )
        prev_pts = pts

    # Optional query_time check after the last frame to test stale feed at evaluation time.
    if query_time is not None and prev_pts is not None and (float(query_time) - prev_pts) > stale_feed_timeout_seconds:
        stale_pts = round(prev_pts + stale_feed_timeout_seconds, 6)
        for bay in validated_bays:
            tracker = state_by_bay[bay["id"]]
            if tracker["observability"] != "observable":
                continue
            tracker["pending_state"] = None
            tracker["pending_since_pts"] = None
            if tracker["state"] != BAY_STATE_UNKNOWN:
                old_state = tracker["state"]
                tracker["state"] = BAY_STATE_UNKNOWN
                tracker["reason"] = "stale_feed"
                tracker["last_changed_pts"] = stale_pts
                transitions.append(
                    {
                        "bay_id": bay["id"],
                        "bay_name": bay["name"],
                        "zone": bay.get("zone"),
                        "time": stale_pts,
                        "from_state": old_state,
                        "to_state": BAY_STATE_UNKNOWN,
                        "reason": "stale_feed",
                    }
                )

    final_runtime_states = {bay_id: t["state"] for bay_id, t in state_by_bay.items()}
    summary = summarize_bays_and_zones(validated_bays, runtime_states=final_runtime_states)
    canonical_payload = {
        "schema_version": 1,
        "kind": "parking_temporal_state_history",
        "occupied_confirm_seconds": occupied_confirm_seconds,
        "free_confirm_seconds": free_confirm_seconds,
        "stale_feed_timeout_seconds": stale_feed_timeout_seconds,
        "matching_strategy": matching_strategy,
        "transitions": transitions,
        "final_states": [
            {
                "bay_id": bay["id"],
                "state": state_by_bay[bay["id"]]["state"],
                "reason": state_by_bay[bay["id"]]["reason"],
                "last_observed_pts": state_by_bay[bay["id"]]["last_observed_pts"],
                "last_changed_pts": state_by_bay[bay["id"]]["last_changed_pts"],
            }
            for bay in validated_bays
        ],
    }
    encoded = json.dumps(
        canonical_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    history_sha256 = hashlib.sha256(encoded.encode()).hexdigest()

    return {
        "matching_strategy": matching_strategy,
        "occupied_confirm_seconds": occupied_confirm_seconds,
        "free_confirm_seconds": free_confirm_seconds,
        "stale_feed_timeout_seconds": stale_feed_timeout_seconds,
        "state_history_sha256": history_sha256,
        "final_states": [dict(state_by_bay[bay["id"]]) for bay in validated_bays],
        "summary": summary,
        "transitions": transitions,
        "timeline": timeline_snapshots,
    }


@dataclass(frozen=True)
class BayTimeSample:
    """Reference ground-truth bay state at one timestamp (T31)."""

    bay_id: str
    time: float
    expected_state: str


@dataclass(frozen=True)
class BayTransitionReference:
    """Reference ground-truth bay state transition at one timestamp (T31)."""

    bay_id: str
    time: float
    from_state: str
    to_state: str


def _state_at_time(bay_id: str, time_sec: float, state_report: dict) -> dict:
    """Look up a bay's replayed state at ``time_sec`` from ``state_report['timeline']``."""
    chosen = None
    for snap in state_report.get("timeline") or ():
        if snap["pts"] <= time_sec + 1e-9:
            for b in snap["bays"]:
                if b["bay_id"] == bay_id:
                    chosen = b
                    break
        else:
            break
    if chosen is not None:
        return chosen
    for f in state_report.get("final_states") or ():
        if f["bay_id"] == bay_id:
            return {
                "bay_id": bay_id,
                "bay_name": f["bay_name"],
                "zone": f.get("zone"),
                "state": BAY_STATE_UNKNOWN if f["observability"] != BAY_DISABLED else BAY_STATE_DISABLED,
                "pending_state": None,
                "reason": "before_first_frame",
            }
    raise ParkingError(f"Unknown bay_id in reference sample: {bay_id!r}.")


def evaluate_parking_reference_replay(
    state_report: dict,
    *,
    samples: list[BayTimeSample] | tuple[BayTimeSample, ...],
    transition_references: list[BayTransitionReference] | tuple[BayTransitionReference, ...] = (),
    transition_tolerance_seconds: float = 10.0,
) -> dict:
    """Compare replayed temporal bay states against reference bay-time samples and transitions (T31).

    Reports:
    * Per-bay and overall confusion matrices across ``free``, ``occupied``, ``unknown``, ``disabled``.
    * ``unknown_rate`` and ``known_state_coverage`` (so Unknown never inflates apparent accuracy).
    * Occupied/free precision, recall and macro F1.
    * ``false_free_cases``: every sample where an actually ``occupied`` bay was predicted ``free``,
      with full context so false-free errors are easy to inspect.
    * ``transition_delays``: matched transition delays (``predicted_time - reference_time``),
      mean/max delay, and missed transitions.
    """
    if not samples:
        raise ParkingError("Provide at least one BayTimeSample for parking reference replay evaluation.")
    states = (BAY_STATE_FREE, BAY_STATE_OCCUPIED, BAY_STATE_UNKNOWN, BAY_STATE_DISABLED)
    overall_confusion: dict[str, dict[str, int]] = {
        exp: {pred: 0 for pred in states} for exp in states
    }
    per_bay: dict[str, dict] = {}
    false_free_cases: list[dict] = []
    unknown_predictions = 0
    known_expected_total = 0
    known_predicted_on_known_expected = 0

    for sample in samples:
        if sample.expected_state not in states:
            raise ParkingError(f"Unsupported expected_state: {sample.expected_state!r}.")
        actual = _state_at_time(sample.bay_id, float(sample.time), state_report)
        pred_state = actual["state"]
        overall_confusion[sample.expected_state][pred_state] += 1

        bay_entry = per_bay.setdefault(
            sample.bay_id,
            {
                "bay_id": sample.bay_id,
                "bay_name": actual.get("bay_name", sample.bay_id),
                "zone": actual.get("zone"),
                "sample_count": 0,
                "correct_count": 0,
                "unknown_count": 0,
                "false_free_count": 0,
                "false_occupied_count": 0,
                "confusion": {exp: {pred: 0 for pred in states} for exp in states},
            },
        )
        bay_entry["sample_count"] += 1
        bay_entry["confusion"][sample.expected_state][pred_state] += 1
        if pred_state == sample.expected_state:
            bay_entry["correct_count"] += 1
        if pred_state == BAY_STATE_UNKNOWN:
            unknown_predictions += 1
            bay_entry["unknown_count"] += 1
        if sample.expected_state in (BAY_STATE_FREE, BAY_STATE_OCCUPIED):
            known_expected_total += 1
            if pred_state in (BAY_STATE_FREE, BAY_STATE_OCCUPIED):
                known_predicted_on_known_expected += 1

        if sample.expected_state == BAY_STATE_OCCUPIED and pred_state == BAY_STATE_FREE:
            bay_entry["false_free_count"] += 1
            false_free_cases.append(
                {
                    "bay_id": sample.bay_id,
                    "bay_name": actual.get("bay_name", sample.bay_id),
                    "zone": actual.get("zone"),
                    "time": round(float(sample.time), 6),
                    "expected_state": BAY_STATE_OCCUPIED,
                    "predicted_state": BAY_STATE_FREE,
                    "pending_state": actual.get("pending_state"),
                    "reason": actual.get("reason"),
                }
            )
        elif sample.expected_state == BAY_STATE_FREE and pred_state == BAY_STATE_OCCUPIED:
            bay_entry["false_occupied_count"] += 1

    for bay_entry in per_bay.values():
        n = bay_entry["sample_count"]
        bay_entry["unknown_rate"] = round(bay_entry["unknown_count"] / n, 6) if n else 0.0
        bay_entry["accuracy"] = round(bay_entry["correct_count"] / n, 6) if n else 0.0

    def _f1_for(target_state: str) -> dict:
        tp = overall_confusion[target_state][target_state]
        fp = sum(overall_confusion[exp][target_state] for exp in states if exp != target_state)
        fn = sum(overall_confusion[target_state][pred] for pred in states if pred != target_state)
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
        return {
            "precision": round(precision, 6),
            "recall": round(recall, 6),
            "f1": round(f1, 6),
            "support": tp + fn,
        }

    occ_metrics = _f1_for(BAY_STATE_OCCUPIED)
    free_metrics = _f1_for(BAY_STATE_FREE)
    macro_f1 = round(0.5 * (occ_metrics["f1"] + free_metrics["f1"]), 6)

    # Transition delay evaluation.
    replayed_transitions = list(state_report.get("transitions") or ())
    used_transition_indices: set[int] = set()
    matched_transitions: list[dict] = []
    missed_transitions: list[dict] = []

    for ref in transition_references:
        best_idx = None
        best_delay = None
        for idx, tr in enumerate(replayed_transitions):
            if idx in used_transition_indices:
                continue
            if tr["bay_id"] != ref.bay_id or tr["to_state"] != ref.to_state:
                continue
            delay = float(tr["time"]) - float(ref.time)
            if -1e-6 <= delay <= transition_tolerance_seconds + 1e-6:
                if best_delay is None or abs(delay) < abs(best_delay):
                    best_delay = delay
                    best_idx = idx
        if best_idx is not None and best_delay is not None:
            used_transition_indices.add(best_idx)
            matched_transitions.append(
                {
                    "bay_id": ref.bay_id,
                    "from_state": ref.from_state,
                    "to_state": ref.to_state,
                    "reference_time": round(float(ref.time), 6),
                    "predicted_time": round(float(replayed_transitions[best_idx]["time"]), 6),
                    "delay_seconds": round(best_delay, 6),
                }
            )
        else:
            missed_transitions.append(
                {
                    "bay_id": ref.bay_id,
                    "from_state": ref.from_state,
                    "to_state": ref.to_state,
                    "reference_time": round(float(ref.time), 6),
                }
            )

    delays = [m["delay_seconds"] for m in matched_transitions]
    return {
        "sample_count": len(samples),
        "unknown_rate": round(unknown_predictions / len(samples), 6),
        "known_state_coverage": (
            round(known_predicted_on_known_expected / known_expected_total, 6)
            if known_expected_total > 0
            else 1.0
        ),
        "occupied_metrics": occ_metrics,
        "free_metrics": free_metrics,
        "occupied_free_macro_f1": macro_f1,
        "false_free_count": len(false_free_cases),
        "false_free_cases": false_free_cases,
        "overall_confusion": overall_confusion,
        "per_bay": [per_bay[k] for k in sorted(per_bay)],
        "transition_delays": {
            "tolerance_seconds": transition_tolerance_seconds,
            "matched_count": len(matched_transitions),
            "missed_count": len(missed_transitions),
            "mean_delay_seconds": round(sum(delays) / len(delays), 6) if delays else None,
            "max_delay_seconds": round(max(delays), 6) if delays else None,
            "matches": matched_transitions,
            "missed": missed_transitions,
        },
    }
