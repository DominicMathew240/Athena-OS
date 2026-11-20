"""T16: versioned view calibration — rotation remap, scale reference, moved-view review.

Three plain rules live here, and their limits are stated in every answer:

* **Rotation remap.** Geometry is stored in normalized 0–1 coordinates of the
  picture the source-view version describes. When a new view version differs
  from the old one only by a 90-degree rotation step, normalized geometry can
  be remapped by a pure coordinate transform and the remap round-trips. When
  anything else changed (dimensions, flips, aspect), the normalized
  coordinates' *meaning* changed, so Athena refuses with a plain message and
  the person re-draws — never a silent best-effort transform.
* **Linear scale reference.** Two calibration points and a real-world distance
  give pixels-per-unit. This is an approximation, not a homography: it assumes
  the measured plane sits roughly perpendicular to the camera and is only
  trustworthy near the reference line. A full perspective homography is
  deliberately out of scope.
* **Moved-view review.** When a new recording's dimensions or duration differ
  materially from the ones the view version recorded, the view may have moved;
  the answer says so with the diff shown, and asks for a review of the saved
  geometry. It never silently keeps or drops anything.
"""

from __future__ import annotations

import math

# A dimension or duration difference at or above this fraction of the recorded
# value is "material" for the moved-view rule. A 1080p→720p change (33%)
# crosses it; tiny rounding differences do not.
MATERIAL_DIMENSION_CHANGE = 0.20
MATERIAL_DURATION_CHANGE = 0.25
# An aspect-ratio change at or above this relative fraction is material on its
# own: 4:3→16:9 is a 33% aspect change even when the resolution is similar, and
# it alters what normalized coordinates mean. Sub-5% changes (odd but near
# recording resolutions) do not trip it.
MATERIAL_ASPECT_CHANGE = 0.05

# A scale reference whose two points are closer together than this fraction of
# the picture diagonal carries no usable direction.
SCALE_MIN_SEPARATION = 1e-4

ROTATION_STEPS = (0, 90, 180, 270)


# ---------------------------------------------------------------------------
# Geometry remap


def _rotate_point(point, rotation: int):
    """One normalized point through a 90-degree rotation step.

    Rotating the picture clockwise by ``rotation`` degrees moves content from
    the old frame into the new one; the normalized coordinates of a fixed
    physical spot transform accordingly. ``rotation`` 0 is the identity.
    """
    x, y = point
    if rotation == 0:
        return [x, y]
    if rotation == 90:  # clockwise: old top-left goes to new top-right
        return [1 - y, x]
    if rotation == 180:
        return [1 - x, 1 - y]
    if rotation == 270:  # counter-clockwise from the reader's view
        return [y, 1 - x]
    raise ValueError(f"Unsupported rotation step: {rotation}")


def _clamp01(value):
    return 0.0 if value < 0.0 else (1.0 if value > 1.0 else value)


def _snap(value):
    """Keep tiny floating-point dust (1e-17 instead of 0.0) out of stored geometry."""
    snapped = round(value, 12)
    return snapped if snapped != 0 else 0.0


def _is_exact_step(old_rotation: int, new_rotation: int) -> bool:
    return (new_rotation - old_rotation) % 360 in (90, 180, 270)


def remap_line(line: dict, old_rotation: int, new_rotation: int) -> dict:
    """Remap one counting line between two 90-degree rotation steps.

    A pure coordinate transform on the normalized endpoints: the line's name,
    side labels and — because the line's ordered direction is preserved by
    construction — its meaning all survive untouched. Round-tripping all four
    steps returns the original line (within 1e-9, tested).
    """
    step = (new_rotation - old_rotation) % 360
    if step == 0:
        raise ValueError("The two view versions have the same rotation; there is nothing to remap.")
    remapped = {
        **line,
        "start": [_snap(_clamp01(c)) for c in _rotate_point(line["start"], step)],
        "end": [_snap(_clamp01(c)) for c in _rotate_point(line["end"], step)],
    }
    if (
        math.hypot(
            remapped["end"][0] - remapped["start"][0],
            remapped["end"][1] - remapped["start"][1],
        )
        < 1e-6
    ):
        raise ValueError("Remapping this line to the new rotation leaves it too short to have a direction.")
    return remapped


def remap_change_reason(old_configuration: dict, new_configuration: dict) -> str | None:
    """Why geometry cannot be remapped from the old view version to the new one.

    ``None`` means the only difference is a 90-degree rotation step, so a
    remap is possible. Any other difference returns the plain reason the person
    needs to re-draw.
    """
    old = {key: old_configuration[key] for key in ("width", "height", "rotation", "horizontal_flip", "vertical_flip")}
    new = {key: new_configuration[key] for key in ("width", "height", "rotation", "horizontal_flip", "vertical_flip")}
    if old == new:
        raise ValueError("The two view versions have the same format; there is nothing to remap.")
    if old["horizontal_flip"] != new["horizontal_flip"] or old["vertical_flip"] != new["vertical_flip"]:
        return "The new view flips the picture differently; drawn geometry cannot be carried over automatically."
    if (old["width"], old["height"]) != (new["width"], new["height"]):
        return (
            "The new view has different pixel dimensions "
            f"({old['width']}\u00d7{old['height']} to {new['width']}\u00d7{new['height']}); "
            "redraw the line on the new view."
        )
    if not _is_exact_step(old["rotation"], new["rotation"]):
        return "Only 90-degree rotation steps can be remapped; redraw the line on the new view."
    return None


# ---------------------------------------------------------------------------
# Linear scale reference


class CalibrationError(ValueError):
    """A calibration input Athena refuses, with the plain reason."""


def scale_reference_payload(
    *,
    source_view_version_id,
    point_a,
    point_b,
    real_world_distance,
    real_world_unit,
    provenance,
) -> dict:
    """Validate and compute one linear scale reference (pixels per unit).

    ``point_a``/``point_b`` are normalized [x, y] pairs; the pixel distance is
    measured against the view version's recorded dimensions. The result stores
    the inputs and the provenance (who entered it, against what, and how) so
    the approximation's assumptions stay visible with the number.
    """
    if not isinstance(point_a, list) or not isinstance(point_b, list) or len(point_a) != 2 or len(point_b) != 2:
        raise CalibrationError("Provide both calibration points as x, y pairs.")
    for point in (point_a, point_b):
        if any(
            isinstance(c, bool) or not isinstance(c, (int, float)) or not math.isfinite(c) or not 0 <= c <= 1 for c in point
        ):
            raise CalibrationError("Calibration points must be finite numbers between 0 and 1.")
    if not isinstance(real_world_distance, (int, float)) or isinstance(real_world_distance, bool):
        raise CalibrationError("The real-world distance must be a number.")
    real_world_distance = float(real_world_distance)
    if not math.isfinite(real_world_distance) or real_world_distance <= 0 or real_world_distance > 1e6:
        raise CalibrationError("The real-world distance must be a positive number of at most 1,000,000 units.")
    if not isinstance(real_world_unit, str) or real_world_unit.strip() not in ("m", "cm", "ft"):
        raise CalibrationError("Choose metres (m), centimetres (cm) or feet (ft) as the real-world unit.")
    if not isinstance(provenance, str) or not provenance.strip() or len(provenance.strip()) > 500:
        raise CalibrationError("Say in up to 500 characters how the scale reference was measured.")
    return {
        "source_view_version_id": str(source_view_version_id),
        "point_a": [float(point_a[0]), float(point_a[1])],
        "point_b": [float(point_b[0]), float(point_b[1])],
        "real_world_distance": real_world_distance,
        "real_world_unit": real_world_unit.strip(),
        "provenance": provenance.strip(),
    }


def pixels_per_unit(reference: dict, width: int, height: int) -> float:
    """Pixels per real-world unit for one stored scale reference.

    The plain arithmetic behind the approximation: the normalized points are
    mapped into the view version's pixel frame, and the pixel distance is
    divided by the real-world distance. The caller keeps the limit visible:
    this is a *linear* scale, valid near the reference line on a plane roughly
    perpendicular to the camera — not a perspective model.
    """
    pixel_a = (reference["point_a"][0] * width, reference["point_a"][1] * height)
    pixel_b = (reference["point_b"][0] * width, reference["point_b"][1] * height)
    pixel_distance = math.hypot(pixel_b[0] - pixel_a[0], pixel_b[1] - pixel_a[1])
    if pixel_distance < SCALE_MIN_SEPARATION * math.hypot(width, height):
        raise CalibrationError("The two calibration points are too close together to set a scale.")
    return pixel_distance / reference["real_world_distance"]


SCALE_LIMIT_NOTE = (
    "This linear scale is an approximation: it assumes the measured plane sits "
    "roughly perpendicular to the camera, and it is only trustworthy near the "
    "reference line. Real camera views have perspective — objects farther from "
    "the camera or from the reference line measure differently."
)


# ---------------------------------------------------------------------------
# Moved-view review


def moved_view_diff(
    *,
    recorded_width: int,
    recorded_height: int,
    recorded_duration: float,
    new_width: int,
    new_height: int,
    new_duration: float,
) -> dict | None:
    """The material difference between a new recording and the view version's format.

    Returns None when the recording matches the recorded format within the
    material thresholds (small rounding differences are not a move). Otherwise
    returns the review answer: what changed, by how much, and the single next
    step — review the saved line. It never says the geometry is fine and never
    silently keeps or drops it.
    """
    changes = []
    width_ratio = abs(new_width - recorded_width) / recorded_width
    height_ratio = abs(new_height - recorded_height) / recorded_height
    recorded_aspect = recorded_width / recorded_height
    new_aspect = new_width / new_height
    aspect_change = abs(new_aspect - recorded_aspect) / recorded_aspect
    if max(width_ratio, height_ratio) >= MATERIAL_DIMENSION_CHANGE or aspect_change >= MATERIAL_ASPECT_CHANGE:
        changes.append(
            {
                "kind": "dimensions",
                "detail": f"{recorded_width}\u00d7{recorded_height}px \u2192 {new_width}\u00d7{new_height}px",
            }
        )
    duration_ratio = abs(new_duration - recorded_duration) / recorded_duration if recorded_duration else None
    if duration_ratio is None or duration_ratio >= MATERIAL_DURATION_CHANGE:
        changes.append(
            {
                "kind": "duration",
                "detail": (
                    f"{recorded_duration:.1f}s \u2192 {new_duration:.1f}s"
                    if recorded_duration
                    else f"unrecorded \u2192 {new_duration:.1f}s"
                ),
            }
        )
    if not changes:
        return None
    return {
        "status": "review",
        "message": "The view may have moved \u2014 review the saved line on this recording.",
        "changes": changes,
        "next_step": "Open the saved counting line on the new recording, check both endpoints still sit where people cross, and save the line again if it moved.",
    }


def view_format(configuration: dict) -> str:
    """One short plain description of a view version's capture format."""
    return f"{configuration['width']}\u00d7{configuration['height']}px, rotation {configuration['rotation']}\u00b0"
