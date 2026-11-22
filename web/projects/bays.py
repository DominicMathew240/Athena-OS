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
BAY_COUNT_LIMIT = 200

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
    """Validate one named bay: a stable ID, a plain name, four good corners."""
    fields = {"id", "name", "corners"}
    if not isinstance(value, dict) or value.keys() != fields:
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
    return {"id": bay_id, "name": name.strip(), "corners": bay_corners(value["corners"])}


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
    from .calibration import _rotate_point, _clamp01, _snap

    step = (new_rotation - old_rotation) % 360
    if step == 0:
        raise ValueError("The two view versions have the same rotation; there is nothing to remap.")
    remapped = [ [_snap(_clamp01(c)) for c in _rotate_point(corner, step)] for corner in bay["corners"] ]
    # The transform is order-preserving on the corner sequence, so a simple quad
    # stays simple; validate anyway rather than trust arithmetic on faith.
    return {"id": bay["id"], "name": bay["name"], "corners": bay_corners(remapped)}
