"""Period and shift aggregates over the durable operation event ledger (T27).

Contracts:
* **Source-time totals reconcile with ledger.** Every period or shift breakdown is
  derived purely from raw ``OperationEvent`` rows (or equivalent event objects)
  and verifies that the sum of bucketed entries, exits and events equals the
  ledger's own totals (``reconciled_with_ledger=True``).
* **UTC storage and site time zones.** Wall-clock timestamps are computed in UTC
  from the recording's ``captured_at`` (UTC) plus each event's quantized source
  timestamp (PTS seconds), then projected into the site's IANA time zone
  (:class:`zoneinfo.ZoneInfo`). When ``captured_at`` is absent, source-time
  buckets work without inventing a wall clock.
* **Midnight, shift boundaries and DST transitions.** Shifts may sit within one
  calendar day (e.g. 08:00–16:00) or cross local midnight (e.g. 22:00–06:00).
  Daylight-saving spring-forward gaps and fall-back repeated hours are handled
  in UTC and tagged with the local offset / fold so repeated local hours never
  double-count or drop events.
* **Net crossings are named separately.** Aggregates report ``entries``,
  ``exits`` and ``net_crossings`` (``entries - exits``), never conflating net
  flow with building occupancy (T28).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..models import OperationEvent
from .comparison import DIRECTIONS
from .ledger import unquantize_timestamp

ENTRY_DIRECTION = "negative_to_positive"
EXIT_DIRECTION = "positive_to_negative"


class AggregateError(ValueError):
    """Invalid time zone, shift definition, or period parameter."""


@dataclass(frozen=True)
class ShiftDefinition:
    """One named operating shift in the site's local wall clock.

    When ``end_time <= start_time`` (for example 22:00 to 06:00), the shift
    crosses local midnight and belongs to the shift date on which it started.
    """

    name: str
    start_time: time
    end_time: time

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise AggregateError("Each shift needs a non-empty name.")
        if not isinstance(self.start_time, time) or not isinstance(self.end_time, time):
            raise AggregateError("Shift start_time and end_time must be datetime.time objects.")
        if self.start_time == self.end_time:
            raise AggregateError("Shift start_time and end_time cannot be identical; use a 24-hour day period instead.")

    @property
    def crosses_midnight(self) -> bool:
        return self.end_time < self.start_time


def _resolve_zone(timezone_name: str) -> ZoneInfo:
    if not isinstance(timezone_name, str) or not timezone_name.strip():
        raise AggregateError("Provide a valid IANA time zone name.")
    try:
        return ZoneInfo(timezone_name.strip())
    except (ZoneInfoNotFoundError, KeyError, ValueError) as error:
        raise AggregateError(f"Unknown IANA time zone: {timezone_name!r}.") from error


def _parse_captured_at_utc(captured_at: datetime | str | None) -> datetime | None:
    if captured_at is None:
        return None
    if isinstance(captured_at, str):
        text = captured_at.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError as error:
            raise AggregateError("captured_at must be an ISO-8601 timestamp or datetime.") from error
    elif isinstance(captured_at, datetime):
        dt = captured_at
    else:
        raise AggregateError("captured_at must be a datetime, ISO-8601 string, or None.")
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _event_pts_and_direction(
    event,
    *,
    entry_direction: str = ENTRY_DIRECTION,
) -> tuple[float, str, str]:
    pts = (
        unquantize_timestamp(event.timestamp)
        if isinstance(event.timestamp, str)
        else float(event.timestamp)
    )
    payload = event.payload if isinstance(event.payload, dict) else {}
    direction = payload.get("direction")
    if direction not in DIRECTIONS:
        raise AggregateError(f"Event {getattr(event, 'identity_sha256', '')} has an unsupported direction.")
    role = "entry" if direction == entry_direction else "exit"
    return pts, direction, role


def aggregate_by_source_period(
    events,
    *,
    period_seconds: float = 3600.0,
    kind: str = OperationEvent.KIND_DETECTOR,
    entry_direction: str = ENTRY_DIRECTION,
    captured_at: datetime | str | None = None,
    timezone_name: str = "UTC",
) -> dict:
    """Bucket ledger events into fixed source-time periods (e.g. 60s, 900s, 3600s) and reconcile with the ledger.

    When ``captured_at`` is provided, each period also carries its UTC and
    site-local start/end timestamps (handling midnight and DST transitions in
    UTC before projecting to ``timezone_name``).
    """
    if isinstance(period_seconds, bool) or not isinstance(period_seconds, (int, float)) or period_seconds <= 0:
        raise AggregateError("period_seconds must be a positive number of seconds.")
    if entry_direction not in DIRECTIONS:
        raise AggregateError("entry_direction must be one of the two supported line directions.")
    zone = _resolve_zone(timezone_name)
    base_utc = _parse_captured_at_utc(captured_at)
    period_sec = float(period_seconds)

    filtered = [e for e in events if getattr(e, "kind", None) == kind]
    buckets: dict[int, dict] = {}
    total_entries = 0
    total_exits = 0

    for event in filtered:
        pts, _direction, role = _event_pts_and_direction(event, entry_direction=entry_direction)
        bucket_idx = int(pts // period_sec)
        start_pts = round(bucket_idx * period_sec, 6)
        end_pts = round((bucket_idx + 1) * period_sec, 6)
        bucket = buckets.get(bucket_idx)
        if bucket is None:
            bucket = {
                "period_index": bucket_idx,
                "start_pts": start_pts,
                "end_pts": end_pts,
                "start_utc": None,
                "end_utc": None,
                "start_local": None,
                "end_local": None,
                "local_date": None,
                "utc_offset": None,
                "entries": 0,
                "exits": 0,
                "net_crossings": 0,
                "event_count": 0,
            }
            if base_utc is not None:
                s_utc = base_utc + timedelta(seconds=start_pts)
                e_utc = base_utc + timedelta(seconds=end_pts)
                s_loc = s_utc.astimezone(zone)
                e_loc = e_utc.astimezone(zone)
                bucket["start_utc"] = s_utc.isoformat()
                bucket["end_utc"] = e_utc.isoformat()
                bucket["start_local"] = s_loc.isoformat()
                bucket["end_local"] = e_loc.isoformat()
                bucket["local_date"] = s_loc.date().isoformat()
                bucket["utc_offset"] = s_loc.strftime("%z")
            buckets[bucket_idx] = bucket

        if role == "entry":
            bucket["entries"] += 1
            total_entries += 1
        else:
            bucket["exits"] += 1
            total_exits += 1
        bucket["net_crossings"] = bucket["entries"] - bucket["exits"]
        bucket["event_count"] += 1

    ordered_periods = [buckets[idx] for idx in sorted(buckets)]
    bucketed_events = sum(b["event_count"] for b in ordered_periods)
    bucketed_entries = sum(b["entries"] for b in ordered_periods)
    bucketed_exits = sum(b["exits"] for b in ordered_periods)
    reconciled = (
        bucketed_events == len(filtered)
        and bucketed_entries == total_entries
        and bucketed_exits == total_exits
        and (total_entries + total_exits) == len(filtered)
    )
    return {
        "kind": kind,
        "timezone_name": timezone_name,
        "wall_clock_available": base_utc is not None,
        "period_seconds": period_sec,
        "entries": total_entries,
        "exits": total_exits,
        "net_crossings": total_entries - total_exits,
        "ledger_event_count": len(filtered),
        "bucketed_event_count": bucketed_events,
        "reconciled_with_ledger": reconciled,
        "periods": ordered_periods,
    }


def _match_shift_for_local_dt(local_dt: datetime, shifts: list[ShiftDefinition]) -> tuple[str, date] | None:
    """Resolve which shift and shift-anchor date a site-local timestamp falls into.

    For an overnight shift (e.g. 22:00–06:00), a timestamp at 01:30 on Tuesday
    belongs to Monday's night shift (anchor date = Monday).
    """
    t = local_dt.timetz().replace(tzinfo=None)
    d = local_dt.date()
    for shift in shifts:
        if not shift.crosses_midnight:
            if shift.start_time <= t < shift.end_time:
                return shift.name, d
        else:
            if t >= shift.start_time:
                return shift.name, d
            if t < shift.end_time:
                return shift.name, d - timedelta(days=1)
    return None


def aggregate_by_site_shifts(
    events,
    *,
    captured_at: datetime | str,
    timezone_name: str,
    shifts: list[ShiftDefinition],
    kind: str = OperationEvent.KIND_DETECTOR,
    entry_direction: str = ENTRY_DIRECTION,
) -> dict:
    """Aggregate ledger events by site-local calendar days and shift boundaries (T27).

    * Converts each event from UTC (``captured_at + pts``) to the site's IANA
      time zone (:class:`ZoneInfo`).
    * Handles overnight shifts that cross local midnight (attributing post-midnight
      hours before ``end_time`` to the shift date on which the shift started).
    * Handles daylight-saving transitions without dropping or double-counting
      events, recording the observed UTC offsets inside each shift bucket.
    * Any event outside declared shifts is placed in an explicit ``unassigned``
      bucket for that local date so the total always reconciles 100% with the
      raw ledger rows.
    """
    if entry_direction not in DIRECTIONS:
        raise AggregateError("entry_direction must be one of the two supported line directions.")
    zone = _resolve_zone(timezone_name)
    base_utc = _parse_captured_at_utc(captured_at)
    if base_utc is None:
        raise AggregateError("Shift aggregation requires the recording's captured_at UTC timestamp.")
    if not isinstance(shifts, (list, tuple)) or not shifts:
        raise AggregateError("Provide at least one ShiftDefinition.")
    for item in shifts:
        if not isinstance(item, ShiftDefinition):
            raise AggregateError("Each shift must be a ShiftDefinition.")

    filtered = [e for e in events if getattr(e, "kind", None) == kind]
    shift_buckets: dict[tuple[str, str], dict] = {}
    daily_buckets: dict[str, dict] = {}
    total_entries = 0
    total_exits = 0

    for event in filtered:
        pts, _direction, role = _event_pts_and_direction(event, entry_direction=entry_direction)
        event_utc = base_utc + timedelta(seconds=pts)
        event_local = event_utc.astimezone(zone)
        offset_str = event_local.strftime("%z")

        # Calendar-day bucket in the site's local time zone.
        cal_day = event_local.date().isoformat()
        day_bucket = daily_buckets.setdefault(
            cal_day,
            {
                "local_date": cal_day,
                "entries": 0,
                "exits": 0,
                "net_crossings": 0,
                "event_count": 0,
                "utc_offsets": [],
            },
        )
        if offset_str not in day_bucket["utc_offsets"]:
            day_bucket["utc_offsets"].append(offset_str)

        matched = _match_shift_for_local_dt(event_local, list(shifts))
        if matched is not None:
            shift_name, anchor_date = matched
        else:
            shift_name, anchor_date = "unassigned", event_local.date()
        anchor_iso = anchor_date.isoformat()
        s_bucket = shift_buckets.setdefault(
            (anchor_iso, shift_name),
            {
                "shift_date": anchor_iso,
                "shift_name": shift_name,
                "entries": 0,
                "exits": 0,
                "net_crossings": 0,
                "event_count": 0,
                "first_utc": event_utc.isoformat(),
                "last_utc": event_utc.isoformat(),
                "first_local": event_local.isoformat(),
                "last_local": event_local.isoformat(),
                "utc_offsets": [],
                "dst_transition_observed": False,
            },
        )
        s_bucket["last_utc"] = event_utc.isoformat()
        s_bucket["last_local"] = event_local.isoformat()
        if offset_str not in s_bucket["utc_offsets"]:
            s_bucket["utc_offsets"].append(offset_str)
        s_bucket["dst_transition_observed"] = len(s_bucket["utc_offsets"]) > 1

        if role == "entry":
            total_entries += 1
            day_bucket["entries"] += 1
            s_bucket["entries"] += 1
        else:
            total_exits += 1
            day_bucket["exits"] += 1
            s_bucket["exits"] += 1
        day_bucket["net_crossings"] = day_bucket["entries"] - day_bucket["exits"]
        day_bucket["event_count"] += 1
        s_bucket["net_crossings"] = s_bucket["entries"] - s_bucket["exits"]
        s_bucket["event_count"] += 1

    ordered_shifts = [shift_buckets[k] for k in sorted(shift_buckets)]
    ordered_days = [daily_buckets[k] for k in sorted(daily_buckets)]
    shift_event_sum = sum(b["event_count"] for b in ordered_shifts)
    day_event_sum = sum(b["event_count"] for b in ordered_days)
    reconciled = (
        shift_event_sum == len(filtered)
        and day_event_sum == len(filtered)
        and (total_entries + total_exits) == len(filtered)
    )
    return {
        "kind": kind,
        "timezone_name": timezone_name,
        "entries": total_entries,
        "exits": total_exits,
        "net_crossings": total_entries - total_exits,
        "ledger_event_count": len(filtered),
        "shift_event_count": shift_event_sum,
        "daily_event_count": day_event_sum,
        "reconciled_with_ledger": reconciled,
        "shifts": ordered_shifts,
        "calendar_days": ordered_days,
    }
