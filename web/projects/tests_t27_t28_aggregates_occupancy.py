"""T27 & T28: period/shift aggregates and guarded estimated occupancy.

Contracts under test:
* T27: source-time period totals and site-timezone shift/day totals reconcile
  100% with the committed ``OperationEvent`` ledger rows; overnight shifts
  crossing midnight attribute post-midnight events to the starting shift date;
  daylight-saving spring-forward and fall-back transitions are handled in UTC
  and projected to the site's IANA time zone without dropping or double-counting
  events.
* T28: estimated occupancy requires a verified starting baseline and complete
  declared entrance coverage; missing doors, offline/gapped feeds, overlapping
  camera views, and impossible negative or over-capacity values trigger
  uncertainty/unavailability while ``entries``, ``exits``, and ``net_crossings``
  remain separately named and visible per entrance.
"""

from __future__ import annotations

from datetime import time

from django.test import SimpleTestCase

from .models import OperationEvent
from .operations import aggregates, ledger, occupancy
from .tests_t19_durable import DurableExecutionBase


class PeriodAndShiftAggregateTests(DurableExecutionBase):
    """T27: period and shift aggregates reconcile with the durable event ledger."""

    def _commit_detector_events(self, execution, items: list[tuple[float, str]]):
        drafts = [
            ledger.EventDraft(
                kind=OperationEvent.KIND_DETECTOR,
                source=f"recording:{self.recording.id}",
                session=1,
                timestamp=ts,
                payload={"direction": direction, "track_id": idx + 1, "camera_key": "cam-1"},
            )
            for idx, (ts, direction) in enumerate(items)
        ]
        return ledger.commit_events(execution, drafts)

    def test_source_period_aggregates_reconcile_with_ledger_rows(self) -> None:
        execution = self.enqueue_and_claim("worker-a")
        self._commit_detector_events(
            execution,
            [
                (10.0, "negative_to_positive"),
                (25.0, "negative_to_positive"),
                (40.0, "positive_to_negative"),
                (75.0, "negative_to_positive"),
                (110.0, "positive_to_negative"),
                (130.0, "positive_to_negative"),
            ],
        )
        rows = list(OperationEvent.objects.filter(execution=execution))
        report = aggregates.aggregate_by_source_period(
            rows,
            period_seconds=60.0,
            captured_at="2026-11-22T00:00:00Z",
            timezone_name="Asia/Kuala_Lumpur",
        )
        self.assertTrue(report["reconciled_with_ledger"])
        self.assertEqual(report["ledger_event_count"], 6)
        self.assertEqual(report["bucketed_event_count"], 6)
        self.assertEqual(report["entries"], 3)
        self.assertEqual(report["exits"], 3)
        self.assertEqual(report["net_crossings"], 0)
        self.assertEqual(len(report["periods"]), 3)
        self.assertEqual(
            [(p["period_index"], p["entries"], p["exits"], p["net_crossings"]) for p in report["periods"]],
            [
                (0, 2, 1, 1),
                (1, 1, 1, 0),
                (2, 0, 1, -1),
            ],
        )
        self.assertEqual(report["periods"][0]["utc_offset"], "+0800")

    def test_midnight_and_overnight_shift_boundaries_reconcile_with_ledger(self) -> None:
        execution = self.enqueue_and_claim("worker-a")
        # Site is Asia/Kuala_Lumpur (UTC+8).
        # captured_at = 2026-11-22T13:30:00Z -> 2026-11-22 21:30:00+08:00.
        # Day shift: 06:00-14:00, Swing: 14:00-22:00, Night: 22:00-06:00 (crosses midnight).
        self._commit_detector_events(
            execution,
            [
                (600.0, "negative_to_positive"),     # 21:40 local (Nov 22 Swing)
                (2400.0, "negative_to_positive"),    # 22:10 local (Nov 22 Night)
                (7200.0, "positive_to_negative"),    # 23:30 local (Nov 22 Night)
                (10800.0, "negative_to_positive"),   # 00:30 local Nov 23 (still Nov 22 Night shift!)
                (32400.0, "positive_to_negative"),   # 06:30 local Nov 23 (Nov 23 Day shift)
            ],
        )
        rows = list(OperationEvent.objects.filter(execution=execution))
        shifts = [
            aggregates.ShiftDefinition("Day", time(6, 0), time(14, 0)),
            aggregates.ShiftDefinition("Swing", time(14, 0), time(22, 0)),
            aggregates.ShiftDefinition("Night", time(22, 0), time(6, 0)),
        ]
        report = aggregates.aggregate_by_site_shifts(
            rows,
            captured_at="2026-11-22T13:30:00Z",
            timezone_name="Asia/Kuala_Lumpur",
            shifts=shifts,
        )
        self.assertTrue(report["reconciled_with_ledger"])
        self.assertEqual(report["ledger_event_count"], 5)
        self.assertEqual(report["shift_event_count"], 5)
        self.assertEqual(report["daily_event_count"], 5)

        by_shift = {(s["shift_date"], s["shift_name"]): s for s in report["shifts"]}
        self.assertEqual(by_shift[("2026-11-22", "Swing")]["entries"], 1)
        # Night shift on 2026-11-22 includes the 00:30 Nov 23 event across midnight!
        night = by_shift[("2026-11-22", "Night")]
        self.assertEqual(night["event_count"], 3)
        self.assertEqual(night["entries"], 2)
        self.assertEqual(night["exits"], 1)
        self.assertEqual(night["net_crossings"], 1)
        self.assertEqual(by_shift[("2026-11-23", "Day")]["exits"], 1)

        # Calendar days split at local midnight (3 events on Nov 22, 2 events on Nov 23).
        by_day = {d["local_date"]: d for d in report["calendar_days"]}
        self.assertEqual(by_day["2026-11-22"]["event_count"], 3)
        self.assertEqual(by_day["2026-11-23"]["event_count"], 2)

    def test_dst_fall_back_transition_preserves_every_event_and_records_offset_change(self) -> None:
        execution = self.enqueue_and_claim("worker-a")
        # America/New_York DST fall-back on 2026-11-01:
        # 05:00:00Z = 01:00:00-04:00 (EDT)
        # 06:00:00Z = 01:00:00-05:00 (EST, repeated 01:xx local hour!)
        self._commit_detector_events(
            execution,
            [
                (900.0, "negative_to_positive"),    # 05:15Z -> 01:15-04:00 (EDT)
                (4500.0, "negative_to_positive"),   # 06:15Z -> 01:15-05:00 (EST)
                (5400.0, "positive_to_negative"),   # 06:30Z -> 01:30-05:00 (EST)
            ],
        )
        rows = list(OperationEvent.objects.filter(execution=execution))
        shifts = [
            aggregates.ShiftDefinition("Day", time(8, 0), time(20, 0)),
            aggregates.ShiftDefinition("Night", time(20, 0), time(8, 0)),
        ]
        report = aggregates.aggregate_by_site_shifts(
            rows,
            captured_at="2026-11-01T05:00:00Z",
            timezone_name="America/New_York",
            shifts=shifts,
        )
        self.assertTrue(report["reconciled_with_ledger"])
        self.assertEqual(report["ledger_event_count"], 3)
        night = report["shifts"][0]
        self.assertEqual(night["shift_date"], "2026-10-31")
        self.assertEqual(night["shift_name"], "Night")
        self.assertEqual(night["event_count"], 3)
        self.assertEqual(night["utc_offsets"], ["-0400", "-0500"])
        self.assertTrue(night["dst_transition_observed"])


class GuardedEstimatedOccupancyTests(SimpleTestCase):
    """T28: guarded estimated occupancy requires baseline and full entrance coverage."""

    def test_complete_coverage_and_valid_baseline_produce_trusted_occupancy_and_separate_net_crossings(
        self,
    ) -> None:
        result = occupancy.compute_guarded_occupancy(
            declared_entrances=["north_door", "south_door"],
            feed_reports=[
                occupancy.EntranceFeedReport("north_door", "cam-north", entries=25, exits=10),
                occupancy.EntranceFeedReport("south_door", "cam-south", entries=8, exits=15),
            ],
            baseline_occupancy=12,
            max_capacity=100,
        )
        self.assertEqual(result["status"], "complete")
        self.assertTrue(result["trusted"])
        self.assertEqual(result["coverage"], "complete")
        self.assertEqual(result["entries"], 33)
        self.assertEqual(result["exits"], 25)
        self.assertEqual(result["net_crossings"], 8)
        self.assertFalse(result["net_crossings_is_occupancy"])
        self.assertEqual(result["estimated_occupancy"], 20)
        self.assertEqual(result["uncertainty_reasons"], [])

    def test_missing_baseline_withholds_occupancy_while_keeping_net_crossings_visible(self) -> None:
        result = occupancy.compute_guarded_occupancy(
            declared_entrances=["main_door"],
            feed_reports=[
                occupancy.EntranceFeedReport("main_door", "cam-1", entries=14, exits=9),
            ],
            baseline_occupancy=None,
        )
        self.assertEqual(result["status"], "unavailable")
        self.assertFalse(result["trusted"])
        self.assertIsNone(result["estimated_occupancy"])
        self.assertIn("missing_baseline", result["uncertainty_reasons"])
        self.assertEqual(result["entries"], 14)
        self.assertEqual(result["exits"], 9)
        self.assertEqual(result["net_crossings"], 5)

    def test_unmonitored_or_offline_or_gapped_entrance_triggers_uncertainty_and_keeps_valid_door_counts(
        self,
    ) -> None:
        result = occupancy.compute_guarded_occupancy(
            declared_entrances=["front_door", "service_exit", "loading_dock"],
            feed_reports=[
                occupancy.EntranceFeedReport("front_door", "cam-front", entries=30, exits=12),
                occupancy.EntranceFeedReport(
                    "loading_dock", "cam-dock", entries=4, exits=2, online=False, gap_count=2
                ),
            ],
            baseline_occupancy=10,
        )
        self.assertEqual(result["status"], "uncertain")
        self.assertFalse(result["trusted"])
        self.assertEqual(result["coverage"], "partial")
        self.assertEqual(result["missing_entrances"], ["service_exit"])
        self.assertEqual(result["offline_entrances"], ["loading_dock"])
        self.assertEqual(result["gapped_entrances"], ["loading_dock"])
        self.assertIn("missing_declared_entrances", result["uncertainty_reasons"])
        self.assertIn("offline_entrance_feeds", result["uncertainty_reasons"])
        self.assertIn("incomplete_entrance_coverage", result["uncertainty_reasons"])
        # Valid per-door crossings remain visible.
        by_door = {e["entrance_id"]: e for e in result["entrances"]}
        self.assertEqual(by_door["front_door"]["net_crossings"], 18)
        self.assertFalse(by_door["service_exit"]["monitored"])

    def test_impossible_negative_or_over_capacity_values_trigger_uncertainty_without_silent_clamping(
        self,
    ) -> None:
        negative = occupancy.compute_guarded_occupancy(
            declared_entrances=["main_door"],
            feed_reports=[
                occupancy.EntranceFeedReport("main_door", "cam-1", entries=5, exits=12),
            ],
            baseline_occupancy=3,
        )
        self.assertEqual(negative["status"], "uncertain")
        self.assertFalse(negative["trusted"])
        self.assertTrue(negative["impossible_value"])
        self.assertEqual(negative["raw_unclamped_estimate"], -4)
        self.assertIsNone(negative["estimated_occupancy"])
        self.assertIn("negative_estimate_impossible", negative["uncertainty_reasons"])
        self.assertEqual(negative["net_crossings"], -7)

        over_cap = occupancy.compute_guarded_occupancy(
            declared_entrances=["main_door"],
            feed_reports=[
                occupancy.EntranceFeedReport("main_door", "cam-1", entries=60, exits=5),
            ],
            baseline_occupancy=10,
            max_capacity=50,
        )
        self.assertEqual(over_cap["status"], "uncertain")
        self.assertFalse(over_cap["trusted"])
        self.assertTrue(over_cap["impossible_value"])
        self.assertEqual(over_cap["raw_unclamped_estimate"], 65)
        self.assertIsNone(over_cap["estimated_occupancy"])
        self.assertIn("exceeds_declared_capacity", over_cap["uncertainty_reasons"])

    def test_overlapping_views_on_same_entrance_are_never_summed_as_unique_people(self) -> None:
        result = occupancy.compute_guarded_occupancy(
            declared_entrances=["lobby_door"],
            feed_reports=[
                occupancy.EntranceFeedReport("lobby_door", "cam-left", entries=10, exits=4),
                occupancy.EntranceFeedReport("lobby_door", "cam-right", entries=11, exits=4),
            ],
            baseline_occupancy=5,
        )
        self.assertEqual(result["status"], "unavailable")
        self.assertFalse(result["trusted"])
        self.assertEqual(result["overlapping_entrances"], ["lobby_door"])
        self.assertIn("overlapping_views_not_additive", result["uncertainty_reasons"])
        self.assertIsNone(result["estimated_occupancy"])
        self.assertIsNone(result["net_crossings"])
