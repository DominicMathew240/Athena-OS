"""T29, T30 & T31: parking bay-to-vehicle matching, temporal bay states, and reference replay metrics.

Contracts under test:
* T29: compares ``footprint_anchor`` and ``polygon_overlap`` on oblique camera views;
  a large vehicle box overlapping several polygons does not automatically mark every
  intersected bay occupied — the foreground footprint bay is marked ``occupied`` while
  the occluded background bay is marked ``unknown`` (``occluded_by_adjacent_vehicle``);
  a vehicle straddling two bay footprints marks both bays ``unknown``
  (``ambiguous_straddle_or_competition``).
* T30: ``free``, ``occupied``, ``unknown``, and ``disabled`` transitions require
  configured confirmation delays (default 2.0 s for occupied, 5.0 s for free); brief
  single-frame flicker does not trigger a false transition; stale feeds transition
  observable bays to ``unknown`` (``stale_feed``); stationary vehicles remain
  continuously ``occupied``; state history is deterministic and hashes to
  ``state_history_sha256``.
* T31: reference bay-time samples and reference transition timestamps produce per-bay
  and overall confusion matrices, occupied/free macro F1, ``unknown_rate``, known-state
  coverage, transition delays, and an explicit ``false_free_cases`` list.
"""

from __future__ import annotations

import uuid as uuid_module

from django.test import SimpleTestCase

from .operations import parking


def _bay(name: str, corners: list[list[float]], *, bay_id: str | None = None, **extra) -> dict:
    return {
        "id": bay_id or str(uuid_module.uuid4()),
        "name": name,
        "corners": corners,
        **extra,
    }


class BayToVehicleMatchingTests(SimpleTestCase):
    """T29: perspective-aware bay matching vs naive overlap on oblique views."""

    def setUp(self) -> None:
        # Oblique camera view:
        # Bay B1 (background row, higher in frame: y in [0.20, 0.45])
        # Bay B2 (foreground row, lower in frame: y in [0.45, 0.75])
        # Bay B3 (adjacent right foreground bay: x in [0.50, 0.80], y in [0.45, 0.75])
        self.b1_back = _bay(
            "B1-Back",
            [[0.15, 0.20], [0.45, 0.20], [0.45, 0.45], [0.15, 0.45]],
            bay_id="00000000-0000-4000-8000-000000000001",
            zone="Row A",
        )
        self.b2_front = _bay(
            "B2-Front",
            [[0.15, 0.45], [0.45, 0.45], [0.45, 0.75], [0.15, 0.75]],
            bay_id="00000000-0000-4000-8000-000000000002",
            zone="Row A",
        )
        self.b3_right = _bay(
            "B3-Right",
            [[0.46, 0.45], [0.76, 0.45], [0.76, 0.75], [0.46, 0.75]],
            bay_id="00000000-0000-4000-8000-000000000003",
            zone="Row A",
        )
        self.b4_disabled = _bay(
            "B4-Disabled",
            [[0.78, 0.45], [0.95, 0.45], [0.95, 0.75], [0.78, 0.75]],
            bay_id="00000000-0000-4000-8000-000000000004",
            zone="Row A",
            observability="disabled",
            disabled_reason="Maintenance",
        )

    def test_large_oblique_vehicle_box_occupies_foreground_bay_and_marks_background_bay_occluded_unknown(
        self,
    ) -> None:
        # A tall van parked in B2-Front: its tires/footprint sit at y=0.68 inside B2-Front,
        # while its tall roof extends up to y=0.22, overlapping 80% of B1-Back!
        tall_van = {
            "label": "van",
            "x": 0.16,
            "y": 0.22,
            "width": 0.28,
            "height": 0.50,
            "score": 0.94,
        }
        comparison = parking.compare_matching_strategies(
            [self.b1_back, self.b2_front, self.b3_right, self.b4_disabled],
            [tall_van],
        )
        # Naive polygon_overlap falsely marks BOTH B1-Back and B2-Front occupied!
        ov_by_name = {b["bay_name"]: b for b in comparison["polygon_overlap"]["bays"]}
        self.assertEqual(ov_by_name["B2-Front"]["observation"], "occupied")
        self.assertEqual(ov_by_name["B1-Back"]["observation"], "occupied")

        # Footprint anchor marks B2-Front occupied, B1-Back unknown (occluded), B3-Right free, B4-Disabled disabled.
        fp_by_name = {b["bay_name"]: b for b in comparison["footprint_anchor"]["bays"]}
        self.assertEqual(fp_by_name["B2-Front"]["observation"], "occupied")
        self.assertEqual(fp_by_name["B2-Front"]["reason"], "footprint_anchor_match")
        self.assertEqual(fp_by_name["B1-Back"]["observation"], "unknown")
        self.assertEqual(fp_by_name["B1-Back"]["reason"], "occluded_by_adjacent_vehicle")
        self.assertTrue(fp_by_name["B1-Back"]["occluded"])
        self.assertEqual(fp_by_name["B3-Right"]["observation"], "free")
        self.assertEqual(fp_by_name["B4-Disabled"]["observation"], "disabled")
        self.assertEqual(comparison["disagreement_count"], 1)
        self.assertEqual(comparison["disagreements"][0]["bay_name"], "B1-Back")

    def test_straddling_vehicle_across_two_bays_marks_both_unknown_ambiguous(self) -> None:
        # A car parked right across the boundary between B2-Front (x: 0.15..0.45) and B3-Right (x: 0.46..0.76)
        straddler = {
            "label": "car",
            "x": 0.305,
            "y": 0.48,
            "width": 0.30,
            "height": 0.24,
            "score": 0.91,
        }
        report = parking.match_vehicles_to_bays(
            [self.b2_front, self.b3_right],
            [straddler],
            strategy=parking.STRATEGY_FOOTPRINT_ANCHOR,
        )
        by_name = {b["bay_name"]: b for b in report["bays"]}
        self.assertEqual(by_name["B2-Front"]["observation"], "unknown")
        self.assertTrue(by_name["B2-Front"]["ambiguous"])
        self.assertEqual(by_name["B2-Front"]["reason"], "ambiguous_straddle_or_competition")
        self.assertEqual(by_name["B3-Right"]["observation"], "unknown")
        self.assertTrue(by_name["B3-Right"]["ambiguous"])


class TemporalBayStatesAndReplayMetricsTests(SimpleTestCase):
    """T30 & T31: temporal confirmation delays, stationary vehicles, stale feeds, and replay metrics."""

    def setUp(self) -> None:
        self.b1 = _bay(
            "B1",
            [[0.10, 0.40], [0.40, 0.40], [0.40, 0.80], [0.10, 0.80]],
            bay_id="00000000-0000-4000-8000-000000000011",
            zone="North",
        )
        self.b2 = _bay(
            "B2",
            [[0.50, 0.40], [0.80, 0.40], [0.80, 0.80], [0.50, 0.80]],
            bay_id="00000000-0000-4000-8000-000000000012",
            zone="North",
        )
        self.b3_disabled = _bay(
            "B3-Disabled",
            [[0.82, 0.40], [0.96, 0.40], [0.96, 0.80], [0.82, 0.80]],
            bay_id="00000000-0000-4000-8000-000000000013",
            zone="North",
            observability="disabled",
        )
        self.car_in_b1 = {
            "label": "car",
            "x": 0.15,
            "y": 0.45,
            "width": 0.20,
            "height": 0.30,
            "score": 0.95,
        }

    def test_confirmation_delays_stationary_car_stale_feed_and_reproducibility(self) -> None:
        # Timeline for B1 (occupied_confirm_seconds=2.0, free_confirm_seconds=5.0, stale_feed_timeout_seconds=15.0):
        # t=0.0..4.0: stationary car in B1 -> at t=0.0, 1.0 still pending 'occupied' (state='unknown');
        #             at t=2.0 confirmed 'occupied'; at t=3.0, 4.0 remains 'occupied' (last_changed_pts=2.0, last_observed_pts=4.0).
        # t=5.0: single-frame detector dropout (empty detections) -> pending 'free', but state stays 'occupied'!
        # t=6.0: car still present -> pending 'free' cleared, state remains 'occupied' with no false transition!
        # t=7.0..12.0: car leaves at t=7.0 -> at t=7.0..11.0 (elapsed < 5.0s) state stays 'occupied' (pending 'free');
        #              at t=12.0 (elapsed = 5.0s) confirmed 'free'!
        # t=30.0: 18s gap (> 15.0s stale_feed_timeout_seconds) -> stale feed transitions B1 & B2 to 'unknown' at t=27.0,
        #         then at t=30.0 new frame starts pending again.
        frames = [
            {"frame_index": 0, "pts": 0.0, "session": 1, "detections": [self.car_in_b1]},
            {"frame_index": 1, "pts": 1.0, "session": 1, "detections": [self.car_in_b1]},
            {"frame_index": 2, "pts": 2.0, "session": 1, "detections": [self.car_in_b1]},
            {"frame_index": 3, "pts": 3.0, "session": 1, "detections": [self.car_in_b1]},
            {"frame_index": 4, "pts": 4.0, "session": 1, "detections": [self.car_in_b1]},
            {"frame_index": 5, "pts": 5.0, "session": 1, "detections": []},  # 1s dropout flicker
            {"frame_index": 6, "pts": 6.0, "session": 1, "detections": [self.car_in_b1]},
            {"frame_index": 7, "pts": 7.0, "session": 1, "detections": []},  # car departs
            {"frame_index": 8, "pts": 9.0, "session": 1, "detections": []},
            {"frame_index": 9, "pts": 11.0, "session": 1, "detections": []},
            {"frame_index": 10, "pts": 12.0, "session": 1, "detections": []},  # 5s since 7.0 -> confirmed free!
            {"frame_index": 11, "pts": 30.0, "session": 2, "detections": []},  # after 18s stale gap
        ]
        bays = [self.b1, self.b2, self.b3_disabled]
        first = parking.evaluate_temporal_bay_states(
            bays,
            frames,
            occupied_confirm_seconds=2.0,
            free_confirm_seconds=5.0,
            stale_feed_timeout_seconds=15.0,
        )
        second = parking.evaluate_temporal_bay_states(
            bays,
            frames,
            occupied_confirm_seconds=2.0,
            free_confirm_seconds=5.0,
            stale_feed_timeout_seconds=15.0,
        )
        self.assertEqual(first["state_history_sha256"], second["state_history_sha256"])

        # Check B1 trajectory across frames:
        b1_by_pts = {
            snap["pts"]: next(b for b in snap["bays"] if b["bay_id"] == self.b1["id"])
            for snap in first["timeline"]
        }
        self.assertEqual(b1_by_pts[0.0]["state"], "unknown")
        self.assertEqual(b1_by_pts[0.0]["pending_state"], "occupied")
        self.assertEqual(b1_by_pts[1.0]["state"], "unknown")
        self.assertEqual(b1_by_pts[2.0]["state"], "occupied")
        self.assertEqual(b1_by_pts[2.0]["last_changed_pts"], 2.0)
        # Stationary car at t=4.0: last_observed_pts advances to 4.0 while last_changed_pts stays 2.0.
        self.assertEqual(b1_by_pts[4.0]["state"], "occupied")
        self.assertEqual(b1_by_pts[4.0]["last_observed_pts"], 4.0)
        self.assertEqual(b1_by_pts[4.0]["last_changed_pts"], 2.0)
        # Single-frame dropout at t=5.0 does not flip state to free.
        self.assertEqual(b1_by_pts[5.0]["state"], "occupied")
        self.assertEqual(b1_by_pts[5.0]["pending_state"], "free")
        self.assertEqual(b1_by_pts[6.0]["state"], "occupied")
        self.assertIsNone(b1_by_pts[6.0]["pending_state"])
        # Departure at t=7.0 confirms free only at t=12.0 (after 5.0s delay).
        self.assertEqual(b1_by_pts[11.0]["state"], "occupied")
        self.assertEqual(b1_by_pts[12.0]["state"], "free")
        self.assertEqual(b1_by_pts[12.0]["last_changed_pts"], 12.0)
        # After 18s outage, stale feed transitions B1 to unknown at t=27.0; at t=30.0 it is unknown (pending free).
        self.assertEqual(b1_by_pts[30.0]["state"], "unknown")
        b1_transitions = [t for t in first["transitions"] if t["bay_id"] == self.b1["id"]]
        self.assertEqual(
            [(t["time"], t["from_state"], t["to_state"], t["reason"]) for t in b1_transitions],
            [
                (2.0, "unknown", "occupied", "footprint_anchor_match"),
                (12.0, "occupied", "free", "clear_observable_view"),
                (27.0, "free", "unknown", "stale_feed"),
            ],
        )

    def test_parking_reference_replay_reports_confusion_unknown_rate_transition_delays_and_false_free_cases(
        self,
    ) -> None:
        # Build a 0..14s timeline where B1 is occupied 2..11s and transitions to free at 12s,
        # while B2 is free from 5..14s.
        frames = [
            {"frame_index": i, "pts": float(i), "session": 1, "detections": [self.car_in_b1] if i < 7 else []}
            for i in range(15)
        ]
        state_report = parking.evaluate_temporal_bay_states(
            [self.b1, self.b2, self.b3_disabled],
            frames,
            occupied_confirm_seconds=2.0,
            free_confirm_seconds=5.0,
        )
        # Reference samples include one intentional false-free probe at t=13.0 on B1
        # (where ground truth says B1 was still occupied, but the model predicted free at t=12.0).
        samples = [
            parking.BayTimeSample(self.b1["id"], 2.0, "occupied"),
            parking.BayTimeSample(self.b1["id"], 6.0, "occupied"),
            parking.BayTimeSample(self.b1["id"], 12.0, "free"),
            parking.BayTimeSample(self.b1["id"], 13.0, "occupied"),  # false-free case!
            parking.BayTimeSample(self.b2["id"], 1.0, "free"),       # predicted unknown at t=1.0 (< 5.0s free confirm)
            parking.BayTimeSample(self.b2["id"], 6.0, "free"),       # predicted free
            parking.BayTimeSample(self.b3_disabled["id"], 6.0, "disabled"),
        ]
        transition_refs = [
            parking.BayTransitionReference(self.b1["id"], 0.0, "unknown", "occupied"),  # confirmed at 2.0s -> delay 2.0s
            parking.BayTransitionReference(self.b1["id"], 7.0, "occupied", "free"),     # confirmed at 12.0s -> delay 5.0s
        ]
        metrics = parking.evaluate_parking_reference_replay(
            state_report,
            samples=samples,
            transition_references=transition_refs,
            transition_tolerance_seconds=6.0,
        )
        self.assertEqual(metrics["sample_count"], 7)
        self.assertEqual(metrics["false_free_count"], 1)
        self.assertEqual(len(metrics["false_free_cases"]), 1)
        ff = metrics["false_free_cases"][0]
        self.assertEqual(ff["bay_id"], self.b1["id"])
        self.assertEqual(ff["bay_name"], "B1")
        self.assertEqual(ff["time"], 13.0)
        self.assertEqual(ff["expected_state"], "occupied")
        self.assertEqual(ff["predicted_state"], "free")

        # Unknown rate and known-state coverage are reported explicitly.
        self.assertAlmostEqual(metrics["unknown_rate"], 1 / 7, places=6)
        self.assertAlmostEqual(metrics["known_state_coverage"], 5 / 6, places=6)

        # Transition delays match the 2.0s occupied and 5.0s free confirmation delays.
        td = metrics["transition_delays"]
        self.assertEqual(td["matched_count"], 2)
        self.assertEqual(td["missed_count"], 0)
        self.assertAlmostEqual(td["mean_delay_seconds"], 3.5, places=6)
        self.assertAlmostEqual(td["max_delay_seconds"], 5.0, places=6)
