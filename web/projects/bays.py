"""T17: named four-corner polygon bays with stable identities.

A bay is one named polygon of exactly four corners in the same normalized
0–1 coordinate discipline as counting lines. Two plain rules live here:

* **Identity is the ID, never the name.** A bay carries a stable ``id``
  chosen once at creation; renaming it (or moving its corners) keeps that
  ID, so history recorded against the ID stays meaningful across renames.
  Old geometry versions keep the name they were frozen with — identity is
  resolved by ID, not by string matching.
* **Simple quads only.** The four corners must form a simple (non
  self-intersecting) quadrilateral: the two edge pairs are checked with
  proper segment-crossing math, so a crossed "bowtie" is refused no matter
  how convex its corner positions look. Corners must be finite numbers in
  0–1, and the quad must have a measurable area, mirroring the degenerate
  line refusal (``LINE_MIN_LENGTH``) from T14.

Duplicate names are allowed: parking operators genuinely have two "Bay 1"
in different rows, and the ID — not the name — is the identity. The draft
API's duplicate action still gives the copy a fresh ID and a
"… (copy)" name so a person can tell them apart in the list.
"""

from __future__ import annotations

import math
import uuid

BAY_NAME_MAX = 80
BAY_ZONE_MAX = 80
BAY_REASON_MAX = 160
BAY_COUNT_LIMIT = 200
BAY_CAPACITY_MAX = 10_000

#: Installer/configuration observability states for a bay (T18).
#: - ``observable``: camera has a usable view of the bay; runtime determines Free/Occupied/Unknown.
#: - ``unobservable``: camera view is permanently obstructed; runtime reports ``unknown`` (never ``free`` or ``disabled``).
#: - ``disabled``: bay is out of service (coned off, closed, reserved); excluded from usable capacity.
BAY_OBSERVABLE = "observable"
BAY_UNOBSERVABLE = "unobservable"
BAY_DISABLED = "disabled"
BAY_OBSERVABILITY_STATES = (BAY_OBSERVABLE, BAY_UNOBSERVABLE, BAY_DISABLED)

#: Runtime bay status vocabulary (T18/T30). ``unknown`` and ``disabled`` are distinct
#: and neither is ever counted inside ``free``.
BAY_STATE_FREE = "free"
BAY_STATE_OCCUPIED = "occupied"
BAY_STATE_UNKNOWN = "unknown"
BAY_STATE_DISABLED = "disabled"
BAY_RUNTIME_STATES = (
    BAY_STATE_FREE,
    BAY_STATE_OCCUPIED,
    BAY_STATE_UNKNOWN,
    BAY_STATE_DISABLED,
)

# A bay whose four corners enclose less than this fraction of the picture has
# no usable area — the polygon analogue of the degenerate-line refusal.
BAY_MIN_AREA = 1e-8


def _is_coordinate(value) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and 0 <= value <= 1
    )


def _segments_cross(p1, p2, p3, p4, strict=False) -> bool:
    """Whether the closed segment p1–p2 properly crosses the closed segment p3–p4.

    Proper segment-crossing math (orientation tests with a collinear-bounds
    fallback), not a convexity heuristic: a bowtie whose four corners sit on a
    perfect rectangle is still caught, because the crossing point of the two
    diagonals is found by the orientations, not by the corner positions.

    ``strict=True`` ignores the collinear-touching fallback, so a shared
    corner or a collapsed edge alone does not count as a crossing — used to
    tell a genuinely crossed degenerate quad from a merely collapsed one.
    """
    def orientation(a, b, c):
        value = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        if value > 1e-15:
            return 1
        if value < -1e-15:
            return -1
        return 0

    def on_segment(a, b, c):
        # a–b is the segment, c a collinear point: is c between a and b?
        return (
            min(a[0], b[0]) - 1e-15 <= c[0] <= max(a[0], b[0]) + 1e-15
            and min(a[1], b[1]) - 1e-15 <= c[1] <= max(a[1], b[1]) + 1e-15
        )

    o1, o2 = orientation(p1, p2, p3), orientation(p1, p2, p4)
    o3, o4 = orientation(p3, p4, p1), orientation(p3, p4, p2)
    if o1 != o2 and o3 != o4:
        return True
    if strict:
        return False
    if o1 == 0 and on_segment(p1, p2, p3):
        return True
    if o2 == 0 and on_segment(p1, p2, p4):
        return True
    if o3 == 0 and on_segment(p3, p4, p1):
        return True
    if o4 == 0 and on_segment(p3, p4, p2):
        return True
    return False


def _signed_area(corners) -> float:
    return 0.5 * sum(
        corners[i][0] * corners[(i + 1) % 4][1] - corners[(i + 1) % 4][0] * corners[i][1]
        for i in range(4)
    )


def bay_corners(value) -> list[list[float]]:
    """Validate exactly four normalized corners; refuse anything degenerate or crossed.

    The messages are plain and shaped like the degenerate-line refusals from
    T14 ("The two line endpoints are too close together…"), because they sit
    in exactly the same places in the flows.
    """
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError("A bay has exactly four corners.")
    corners = []
    for corner in value:
        if (
            not isinstance(corner, list)
            or len(corner) != 2
            or not all(_is_coordinate(item) for item in corner)
        ):
            raise ValueError("Bay corners must be finite numbers between 0 and 1.")
        corners.append([float(corner[0]), float(corner[1])])
    # Degenerate first: a near-zero-area quad can trip the crossing test's
    # collinear fallback through floating-point dust. Inside the degenerate
    # branch only the strict orientation crossing counts, so a symmetric
    # bowtie (which has exactly zero signed area but genuinely crossed edges)
    # still gets the crossing message, while a collapsed quad gets the honest
    # too-close message.
    if abs(_signed_area(corners)) < BAY_MIN_AREA:
        if _segments_cross(corners[0], corners[1], corners[2], corners[3], strict=True) or _segments_cross(
            corners[1], corners[2], corners[3], corners[0], strict=True
        ):
            raise ValueError("The four corners cross each other; a bay's outline must not overlap itself.")
        raise ValueError("The four corners are too close together to enclose a bay.")
    # The two edge pairs of the closed quad: (c0–c1 vs c2–c3) and (c1–c2 vs c3–c0).
    if _segments_cross(corners[0], corners[1], corners[2], corners[3]) or _segments_cross(
        corners[1], corners[2], corners[3], corners[0]
    ):
        raise ValueError("The four corners cross each other; a bay's outline must not overlap itself.")
    return corners


def operation_bay(value) -> dict:
    """Validate one named bay: a stable ID, a plain name, four good corners, and optional T18 zone/status/capacity."""
    required = {"id", "name", "corners"}
    optional = {"zone", "observability", "disabled", "unobservable", "disabled_reason", "capacity"}
    if not isinstance(value, dict) or not required.issubset(value.keys()) or (value.keys() - (required | optional)):
        raise ValueError("Provide the bay ID, name and its four corners.")
    bay_id = value["id"]
    if not isinstance(bay_id, str) or not bay_id.strip() or len(bay_id.strip()) > 64:
        raise ValueError("Each bay needs a stable ID of up to 64 characters.")
    try:
        uuid.UUID(bay_id)
    except (ValueError, AttributeError):
        raise ValueError("Each bay needs a stable ID: a universally unique identifier.") from None
    name = value["name"]
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > BAY_NAME_MAX:
        raise ValueError("Name each bay using up to 80 characters.")
    normalized = {"id": bay_id, "name": name.strip(), "corners": bay_corners(value["corners"])}

    if "zone" in value and value["zone"] is not None:
        zone = value["zone"]
        if not isinstance(zone, str) or len(zone.strip()) > BAY_ZONE_MAX:
            raise ValueError(f"Zone names may contain up to {BAY_ZONE_MAX} characters.")
        if zone.strip():
            normalized["zone"] = zone.strip()

    disabled_flag = value.get("disabled")
    if disabled_flag is not None and not isinstance(disabled_flag, bool):
        raise ValueError("The bay disabled flag must be true or false.")
    unobservable_flag = value.get("unobservable")
    if unobservable_flag is not None and not isinstance(unobservable_flag, bool):
        raise ValueError("The bay unobservable flag must be true or false.")
    if disabled_flag is True and unobservable_flag is True:
        raise ValueError(
            "A bay cannot be both disabled (out of service) and unobservable (unknown view); choose one."
        )

    observability = value.get("observability")
    if observability is not None:
        if observability not in BAY_OBSERVABILITY_STATES:
            raise ValueError(
                "Bay observability must be 'observable', 'unobservable', or 'disabled' "
                "('unknown' is a runtime state, not a configuration mode)."
            )
        if disabled_flag is True and observability != BAY_DISABLED:
            raise ValueError("A bay marked disabled=true must use observability 'disabled'.")
        if unobservable_flag is True and observability != BAY_UNOBSERVABLE:
            raise ValueError("A bay marked unobservable=true must use observability 'unobservable'.")
        if observability != BAY_OBSERVABLE or "observability" in value:
            normalized["observability"] = observability
        if observability == BAY_DISABLED:
            normalized["disabled"] = True
        elif observability == BAY_UNOBSERVABLE:
            normalized["unobservable"] = True
    else:
        if disabled_flag is True:
            normalized["disabled"] = True
            normalized["observability"] = BAY_DISABLED
        elif disabled_flag is False:
            normalized["disabled"] = False
        if unobservable_flag is True:
            normalized["unobservable"] = True
            normalized["observability"] = BAY_UNOBSERVABLE
        elif unobservable_flag is False:
            normalized["unobservable"] = False

    if "disabled_reason" in value and value["disabled_reason"] is not None:
        reason = value["disabled_reason"]
        if not isinstance(reason, str) or len(reason.strip()) > BAY_REASON_MAX:
            raise ValueError(f"The bay status reason may contain up to {BAY_REASON_MAX} characters.")
        if reason.strip():
            normalized["disabled_reason"] = reason.strip()

    if "capacity" in value and value["capacity"] is not None:
        cap = value["capacity"]
        if isinstance(cap, bool) or not isinstance(cap, int) or not 1 <= cap <= BAY_CAPACITY_MAX:
            raise ValueError(f"Bay or zone capacity must be a whole number from 1 to {BAY_CAPACITY_MAX}.")
        normalized["capacity"] = cap

    return normalized


def operation_bays(value) -> list[dict]:
    """Validate a whole bay list: each bay, unique IDs, and the count limit."""
    if not isinstance(value, list):
        raise ValueError("Provide the bays as a list.")
    if len(value) > BAY_COUNT_LIMIT:
        raise ValueError(f"A draft keeps at most {BAY_COUNT_LIMIT} bays.")
    bays = [operation_bay(item) for item in value]
    seen = set()
    for bay in bays:
        if bay["id"] in seen:
            raise ValueError("Each bay needs its own ID; two bays share one.")
        seen.add(bay["id"])
    return bays


def new_bay_id() -> str:
    """A fresh stable identity for one bay, chosen once at creation."""
    return str(uuid.uuid4())


def _bounded_copy_name(name: str, suffix: str) -> str:
    """Keep ``name + suffix`` within ``BAY_NAME_MAX`` by truncating the original."""
    if len(suffix) >= BAY_NAME_MAX:
        return suffix[:BAY_NAME_MAX]
    return f"{name[: BAY_NAME_MAX - len(suffix)]}{suffix}"


def duplicate_bay_name(name: str, existing_names) -> str:
    """A name for a duplicate that is plainly a copy and unique in the list."""
    existing = set(existing_names)
    suffix = " (copy)"
    candidate = _bounded_copy_name(name, suffix)
    if candidate not in existing:
        return candidate
    counter = 2
    while True:
        candidate = _bounded_copy_name(name, f"{suffix} {counter}")
        if candidate not in existing:
            return candidate
        counter += 1


def remap_bay(bay: dict, old_rotation: int, new_rotation: int) -> dict:
    """Remap one bay's corners between two 90-degree rotation steps.

    The same pure coordinate transform the counting line uses
    (``calibration.remap_line``), applied to all four corners; the bay's ID
    and name survive untouched, exactly as the line's name and side labels
    do. Round-tripping all four steps returns the original corners.
    """
    from .calibration import _clamp01, _rotate_point, _snap

    step = (new_rotation - old_rotation) % 360
    if step == 0:
        raise ValueError("The two view versions have the same rotation; there is nothing to remap.")
    remapped = [ [_snap(_clamp01(c)) for c in _rotate_point(corner, step)] for corner in bay["corners"] ]
    # The transform is order-preserving on the corner sequence, so a simple quad
    # stays simple; validate anyway rather than trust arithmetic on faith.
    preserved = {
        k: v
        for k, v in bay.items()
        if k in {"zone", "observability", "disabled", "unobservable", "disabled_reason", "capacity"}
    }
    return operation_bay({"id": bay["id"], "name": bay["name"], "corners": bay_corners(remapped), **preserved})


def bay_effective_observability(bay: dict) -> str:
    """Return 'disabled', 'unobservable', or 'observable' for one validated bay dict."""
    if bay.get("disabled") is True or bay.get("observability") == BAY_DISABLED:
        return BAY_DISABLED
    if bay.get("unobservable") is True or bay.get("observability") == BAY_UNOBSERVABLE:
        return BAY_UNOBSERVABLE
    return BAY_OBSERVABLE


def bay_initial_runtime_state(bay: dict) -> str:
    """Initial runtime status of a bay before vehicle detections are applied (T18).

    * A ``disabled`` bay has runtime state ``disabled`` (excluded from usable capacity).
    * An ``unobservable`` bay has runtime state ``unknown`` (camera view blocked — never ``free`` and never ``disabled``).
    * An ``observable`` bay starts ``unknown`` until valid frame evidence confirms ``free`` or ``occupied``.
    """
    obs = bay_effective_observability(bay)
    if obs == BAY_DISABLED:
        return BAY_STATE_DISABLED
    return BAY_STATE_UNKNOWN


def summarize_bays_and_zones(
    bays: list[dict] | None,
    *,
    runtime_states: dict[str, str] | None = None,
) -> dict:
    """Summarize zones/areas, observability, usable capacity, and Disabled vs Unknown counts (T18).

    * ``disabled`` bays are excluded from ``usable_capacity``.
    * ``unobservable`` bays remain in ``usable_capacity`` (unless disabled) but their
      observation state is ``unknown`` — never ``free`` and never ``disabled``.
    * ``capacity`` on a bay (or sum of bay capacities in a zone; default 1 space per bay)
      sets the total configured capacity without mutating historical versions.
    """
    bay_list = list(bays or [])
    runtime_states = dict(runtime_states or {})
    total_bays = len(bay_list)
    observable_bays = 0
    unobservable_bays = 0
    disabled_bays = 0
    total_capacity = 0
    disabled_capacity = 0
    unobservable_capacity = 0
    free_count = 0
    occupied_count = 0
    unknown_count = 0
    zones_map: dict[str, dict] = {}

    for bay in bay_list:
        obs = bay_effective_observability(bay)
        cap = int(bay.get("capacity") or 1)
        total_capacity += cap
        zone_name = bay.get("zone") or "Unassigned"
        zone_entry = zones_map.setdefault(
            zone_name,
            {
                "zone": zone_name,
                "bay_ids": [],
                "total_bays": 0,
                "observable_bays": 0,
                "unobservable_bays": 0,
                "disabled_bays": 0,
                "total_capacity": 0,
                "usable_capacity": 0,
                "free_bays": 0,
                "occupied_bays": 0,
                "unknown_bays": 0,
            },
        )
        zone_entry["bay_ids"].append(bay["id"])
        zone_entry["total_bays"] += 1
        zone_entry["total_capacity"] += cap

        if obs == BAY_DISABLED:
            disabled_bays += 1
            disabled_capacity += cap
            zone_entry["disabled_bays"] += 1
            state = BAY_STATE_DISABLED
        elif obs == BAY_UNOBSERVABLE:
            unobservable_bays += 1
            unobservable_capacity += cap
            zone_entry["unobservable_bays"] += 1
            zone_entry["usable_capacity"] += cap
            # Unobservable bays can never be reported as Free or Disabled; they are Unknown.
            state = BAY_STATE_UNKNOWN
        else:
            observable_bays += 1
            zone_entry["observable_bays"] += 1
            zone_entry["usable_capacity"] += cap
            candidate = runtime_states.get(bay["id"], BAY_STATE_UNKNOWN)
            if candidate not in (BAY_STATE_FREE, BAY_STATE_OCCUPIED, BAY_STATE_UNKNOWN):
                candidate = BAY_STATE_UNKNOWN
            state = candidate

        if state == BAY_STATE_FREE:
            free_count += 1
            zone_entry["free_bays"] += 1
        elif state == BAY_STATE_OCCUPIED:
            occupied_count += 1
            zone_entry["occupied_bays"] += 1
        elif state == BAY_STATE_UNKNOWN:
            unknown_count += 1
            zone_entry["unknown_bays"] += 1

    usable_capacity = max(0, total_capacity - disabled_capacity)
    observable_capacity = max(0, usable_capacity - unobservable_capacity)
    return {
        "total_bays": total_bays,
        "observable_bays": observable_bays,
        "unobservable_bays": unobservable_bays,
        "disabled_bays": disabled_bays,
        "total_capacity": total_capacity,
        "disabled_capacity": disabled_capacity,
        "usable_capacity": usable_capacity,
        "observable_capacity": observable_capacity,
        "unobservable_capacity": unobservable_capacity,
        "free_bays": free_count,
        "occupied_bays": occupied_count,
        "unknown_bays": unknown_count,
        "unknown_included_in_free": False,
        "disabled_included_in_usable_capacity": False,
        "zones": list(zones_map.values()),
    }
