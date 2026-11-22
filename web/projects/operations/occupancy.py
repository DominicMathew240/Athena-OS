"""Guarded estimated occupancy across declared entrances (T28).

Contracts (from ``docs/plans/2026-11-16-vision-metric-and-recipe-contracts.md``
and ``docs/plans/2026-11-15-vision-workflows-build-plan.md``):
* **Net crossings remain separately named.** ``entries``, ``exits`` and
  ``net_crossings`` (``entries - exits``) are always reported under their own
  names and never labelled as current occupancy.
* **Require a verified baseline and complete declared entrance coverage.**
  Estimated occupancy is only computed when ``baseline_occupancy`` (a
  nonnegative integer starting count) is configured AND every declared entrance
  for the space has an active, non-overlapping monitored feed.
* **Missing doors/feeds and impossible values trigger uncertainty.**
  - If any declared entrance is unmonitored, offline/stale, or has unaccounted
    coverage gaps, the result reports ``status="uncertain"`` (or
    ``"unavailable"`` when no baseline exists), ``trusted=False``, and names the
    missing or gapped entrances while keeping valid per-entrance crossings
    visible.
  - Overlapping views of the same entrance are refused as non-additive
    (``overlapping_views_not_additive``): overlapping cameras must never be
    summed as unique people.
  - If ``baseline + entries - exits < 0`` (or exceeds an optional
    ``max_capacity``), the result flags ``impossible_value=True``,
    ``status="uncertain"``, ``trusted=False``, and preserves the raw unclamped
    number in ``raw_unclamped_estimate`` rather than silently clamping `-3` to
    `0` and presenting it as trusted.
"""

from __future__ import annotations

from dataclasses import dataclass


class OccupancyConfigError(ValueError):
    """Malformed entrance or baseline configuration."""


@dataclass(frozen=True)
class EntranceFeedReport:
    """Crossing counts and coverage state for one monitored entrance feed."""

    entrance_id: str
    feed_id: str
    entries: int
    exits: int
    coverage_complete: bool = True
    online: bool = True
    gap_count: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.entrance_id, str) or not self.entrance_id.strip():
            raise OccupancyConfigError("Each entrance feed report needs a non-empty entrance_id.")
        if not isinstance(self.feed_id, str) or not self.feed_id.strip():
            raise OccupancyConfigError("Each entrance feed report needs a non-empty feed_id.")
        for field_name, val in (("entries", self.entries), ("exits", self.exits), ("gap_count", self.gap_count)):
            if isinstance(val, bool) or not isinstance(val, int) or val < 0:
                raise OccupancyConfigError(f"{field_name} must be a nonnegative integer.")
        if not isinstance(self.coverage_complete, bool) or not isinstance(self.online, bool):
            raise OccupancyConfigError("coverage_complete and online must be booleans.")


def compute_guarded_occupancy(
    *,
    declared_entrances: list[str] | tuple[str, ...],
    feed_reports: list[EntranceFeedReport] | tuple[EntranceFeedReport, ...],
    baseline_occupancy: int | None = None,
    max_capacity: int | None = None,
) -> dict:
    """Compute per-door net crossings and guarded estimated occupancy (T28)."""
    if not isinstance(declared_entrances, (list, tuple)) or not declared_entrances:
        raise OccupancyConfigError("Declare at least one entrance for the monitored space.")
    cleaned_entrances: list[str] = []
    for ent in declared_entrances:
        if not isinstance(ent, str) or not ent.strip():
            raise OccupancyConfigError("Each declared entrance needs a non-empty identifier.")
        ent_id = ent.strip()
        if ent_id in cleaned_entrances:
            raise OccupancyConfigError(f"Duplicate declared entrance: {ent_id!r}.")
        cleaned_entrances.append(ent_id)

    if baseline_occupancy is not None and (
        isinstance(baseline_occupancy, bool) or not isinstance(baseline_occupancy, int) or baseline_occupancy < 0
    ):
        raise OccupancyConfigError("baseline_occupancy must be a nonnegative whole number or None.")
    if max_capacity is not None and (
        isinstance(max_capacity, bool) or not isinstance(max_capacity, int) or max_capacity < 1
    ):
        raise OccupancyConfigError("max_capacity must be a positive whole number or None.")

    reports = list(feed_reports)
    for rep in reports:
        if not isinstance(rep, EntranceFeedReport):
            raise OccupancyConfigError("Each feed report must be an EntranceFeedReport.")
        if rep.entrance_id not in cleaned_entrances:
            raise OccupancyConfigError(
                f"Feed {rep.feed_id!r} references undeclared entrance {rep.entrance_id!r}."
            )

    # Group feeds by entrance to detect missing entrances and overlapping views.
    by_entrance: dict[str, list[EntranceFeedReport]] = {ent: [] for ent in cleaned_entrances}
    for rep in reports:
        by_entrance[rep.entrance_id].append(rep)

    missing_entrances = [ent for ent, items in by_entrance.items() if not items]
    overlapping_entrances = [ent for ent, items in by_entrance.items() if len(items) > 1]
    offline_entrances = [
        ent for ent, items in by_entrance.items() if any(not item.online for item in items)
    ]
    gapped_entrances = [
        ent
        for ent, items in by_entrance.items()
        if any(not item.coverage_complete or item.gap_count > 0 for item in items)
    ]

    # Per-door crossing counts stay visible for every single-feed monitored entrance.
    per_entrance: list[dict] = []
    total_entries = 0
    total_exits = 0
    for ent in cleaned_entrances:
        items = by_entrance[ent]
        if len(items) == 1:
            rep = items[0]
            ent_entries = rep.entries
            ent_exits = rep.exits
            total_entries += ent_entries
            total_exits += ent_exits
            per_entrance.append(
                {
                    "entrance_id": ent,
                    "feed_id": rep.feed_id,
                    "monitored": True,
                    "online": rep.online,
                    "coverage_complete": rep.coverage_complete and rep.gap_count == 0,
                    "gap_count": rep.gap_count,
                    "entries": ent_entries,
                    "exits": ent_exits,
                    "net_crossings": ent_entries - ent_exits,
                }
            )
        elif len(items) == 0:
            per_entrance.append(
                {
                    "entrance_id": ent,
                    "feed_id": None,
                    "monitored": False,
                    "online": False,
                    "coverage_complete": False,
                    "gap_count": 0,
                    "entries": None,
                    "exits": None,
                    "net_crossings": None,
                }
            )
        else:
            # Overlapping camera views on the same entrance: never sum as unique people.
            per_entrance.append(
                {
                    "entrance_id": ent,
                    "feed_id": [item.feed_id for item in items],
                    "monitored": True,
                    "overlapping_views": True,
                    "online": all(item.online for item in items),
                    "coverage_complete": False,
                    "gap_count": sum(item.gap_count for item in items),
                    "entries": None,
                    "exits": None,
                    "net_crossings": None,
                }
            )

    net_crossings = total_entries - total_exits
    uncertainty_reasons: list[str] = []
    if baseline_occupancy is None:
        uncertainty_reasons.append("missing_baseline")
    if missing_entrances:
        uncertainty_reasons.append("missing_declared_entrances")
    if overlapping_entrances:
        uncertainty_reasons.append("overlapping_views_not_additive")
    if offline_entrances:
        uncertainty_reasons.append("offline_entrance_feeds")
    if gapped_entrances:
        uncertainty_reasons.append("incomplete_entrance_coverage")

    raw_estimate: int | None = None
    impossible_value = False
    if baseline_occupancy is not None and not overlapping_entrances:
        raw_estimate = baseline_occupancy + net_crossings
        if raw_estimate < 0:
            impossible_value = True
            uncertainty_reasons.append("negative_estimate_impossible")
        if max_capacity is not None and raw_estimate > max_capacity:
            impossible_value = True
            uncertainty_reasons.append("exceeds_declared_capacity")

    coverage_complete = (
        not missing_entrances
        and not overlapping_entrances
        and not offline_entrances
        and not gapped_entrances
    )

    if baseline_occupancy is None or overlapping_entrances:
        status = "unavailable"
        trusted = False
        estimated_occupancy = None
    elif uncertainty_reasons or impossible_value:
        status = "uncertain"
        trusted = False
        estimated_occupancy = raw_estimate if not impossible_value else None
    else:
        status = "complete"
        trusted = True
        estimated_occupancy = raw_estimate

    return {
        "entries": total_entries if not overlapping_entrances else None,
        "exits": total_exits if not overlapping_entrances else None,
        "net_crossings": net_crossings if not overlapping_entrances else None,
        "net_crossings_is_occupancy": False,
        "baseline_occupancy": baseline_occupancy,
        "max_capacity": max_capacity,
        "declared_entrance_count": len(cleaned_entrances),
        "monitored_entrance_count": len(cleaned_entrances) - len(missing_entrances),
        "missing_entrances": missing_entrances,
        "overlapping_entrances": overlapping_entrances,
        "offline_entrances": offline_entrances,
        "gapped_entrances": gapped_entrances,
        "coverage": "complete" if coverage_complete else ("unknown" if len(missing_entrances) == len(cleaned_entrances) else "partial"),
        "status": status,
        "trusted": trusted,
        "impossible_value": impossible_value,
        "raw_unclamped_estimate": raw_estimate,
        "estimated_occupancy": estimated_occupancy,
        "uncertainty_reasons": uncertainty_reasons,
        "entrances": per_entrance,
    }
