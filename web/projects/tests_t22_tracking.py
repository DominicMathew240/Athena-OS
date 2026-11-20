"""T22: per-camera, per-session object tracking over the T21 detections.

The contracts under test: the tracker is deterministic and dependency-free
(greedy motion-compensated IoU association); tracks are born on first
appearance and ended on disappearance; an occlusion shorter than the max gap
resumes the SAME id, at or past it the track ends and a NEW id begins — and no
persistent identity is ever claimed across a gap or a session boundary (tracks
carry their occlusion records; the outcome's honesty note says the ids are
per-session, not persistent identities). Bad input is refused with a plain
message, never silently clamped.

The tracker unit is pure: synthetic detection lists feed the tracker directly,
no video needed. The worker integration reuses T21's fakes — the fake detector
registered through the training backend registry and synthetic ffmpeg clips —
plus a scripted detector subclass whose boxes vary per predict call, so
occlusion and session-boundary scenarios survive the adapter end to end.
"""

from __future__ import annotations

import json
import shutil
import unittest
from unittest import skipUnless

from .operations import executions, tracking
from .operations.inference import FrameDetections
from .tests_t20_source_time import make_clip as make_gap_clip
from .tests_t21_inference import FakeDetector, InferenceBase, make_clip
from .training import register_backend, unregister_backend
from .training.base import BoxPrediction

REQUIRE_FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")


def box(x, y, size=0.2, label="person", score=0.9):
    """A valid normalized detection box at top-left (x, y)."""
    return {"label": label, "x": x, "y": y, "width": size, "height": size, "score": score}


def frame(index, pts, session, detections):
    """One ordered frame record like the ones T21's adapter produces."""
    return FrameDetections(
        frame_index=index, pts=pts, session=session, wall_clock=None, detections=tuple(detections)
    )


# -- the pure tracker: birth, motion association, determinism --------------------------------------
class TrackerBase(unittest.TestCase):
    def track(self, frames, **kwargs):
        return tracking.track_detections(frames, **kwargs)


class StraightMotionTests(TrackerBase):
    def test_one_moving_detection_is_one_track(self):
        frames = [
            frame(0, 0.0, 1, [box(0.10, 0.10)]),
            frame(1, 0.2, 1, [box(0.12, 0.10)]),
            frame(2, 0.4, 1, [box(0.14, 0.10)]),
            frame(3, 0.6, 1, [box(0.16, 0.10)]),
        ]
        report = self.track(frames)
        self.assertEqual(report.track_count, 1)
        track = report.tracks[0]
        self.assertEqual(track.track_id, 1)
        self.assertEqual(track.camera_key, "camera")
        self.assertEqual(track.session, 1)
        self.assertEqual(track.first_frame_index, 0)
        self.assertEqual(track.last_frame_index, 3)
        self.assertAlmostEqual(track.first_pts, 0.0)
        self.assertAlmostEqual(track.last_pts, 0.6)
        self.assertAlmostEqual(track.span_seconds, 0.6)
        self.assertEqual(track.detection_count, 4)
        self.assertEqual(track.detection_indices, [0, 1, 2, 3])
        self.assertEqual(track.gaps, [])
        self.assertEqual(report.ended_by_gap, 0)
        self.assertEqual(report.resumed_tracks, 0)
        self.assertEqual(report.total_gap_events, 0)

    def test_two_well_separated_detections_are_two_tracks(self):
        frames = [
            frame(0, 0.0, 1, [box(0.10, 0.10), box(0.70, 0.70)]),
            frame(1, 0.2, 1, [box(0.12, 0.10), box(0.68, 0.70)]),
        ]
        report = self.track(frames)
        self.assertEqual(report.track_count, 2)
        # One id per box; each track consumed its own detections, in input order.
        self.assertEqual(sorted(t.detection_indices for t in report.tracks), [[0, 2], [1, 3]])
        for track in report.tracks:
            self.assertEqual(track.session, 1)
            self.assertEqual(track.detection_count, 2)

    def test_a_crossing_pair_keeps_two_ids(self):
        # Two movers whose paths nearly cross: greedy IoU keeps two tracks
        # while the overlap stays unambiguous. (A fully degenerate crossing
        # can swap ids — the greedy limit, stated in the module docstring.)
        frames = [
            frame(0, 0.0, 1, [box(0.05, 0.10), box(0.75, 0.10)]),
            frame(1, 0.2, 1, [box(0.20, 0.10), box(0.60, 0.10)]),
            frame(2, 0.4, 1, [box(0.35, 0.10), box(0.45, 0.10)]),
            frame(3, 0.6, 1, [box(0.50, 0.10), box(0.30, 0.10)]),
        ]
        report = self.track(frames)
        self.assertEqual(report.track_count, 2)
        for track in report.tracks:
            self.assertEqual(track.detection_count, 4)
        # Each track's boxes are a coherent left-to-right / right-to-left run.
        by_first_x = {t.observations[0].box[0]: t for t in report.tracks}
        left, right = by_first_x[0.05], by_first_x[0.75]
        self.assertGreater(left.observations[-1].box[0], left.observations[0].box[0])
        self.assertLess(right.observations[-1].box[0], right.observations[0].box[0])

    def test_empty_frames_produce_no_tracks_and_no_error(self):
        report = self.track([frame(0, 0.0, 1, []), frame(1, 0.2, 1, [])])
        self.assertEqual(report.track_count, 0)
        self.assertEqual(report.tracks, ())
        self.assertEqual(report.ended_by_gap, 0)

    def test_an_empty_input_is_an_empty_report(self):
        report = self.track([])
        self.assertEqual(report.track_count, 0)

    def test_track_ids_are_stable_and_sequential(self):
        frames = [
            frame(0, 0.0, 1, [box(0.10, 0.10)]),
            frame(1, 0.2, 1, [box(0.60, 0.10)]),
            frame(2, 0.4, 1, [box(0.10, 0.10)]),
        ]
        report = self.track(frames)
        self.assertEqual([t.track_id for t in report.tracks], [1, 2])


class OcclusionTests(TrackerBase):
    def test_a_short_occlusion_resumes_the_same_id(self):
        # Missing from frames 1 and 2 (0.6 s of missing presentation time,
        # under the 1.0 s threshold): the SAME id resumes, gap recorded.
        frames = [
            frame(0, 0.0, 1, [box(0.10, 0.10)]),
            frame(1, 0.2, 1, []),
            frame(2, 0.4, 1, []),
            frame(3, 0.6, 1, [box(0.16, 0.10)]),
        ]
        report = self.track(frames)
        self.assertEqual(report.track_count, 1)
        track = report.tracks[0]
        self.assertEqual(track.detection_count, 2)
        self.assertEqual(track.detection_indices, [0, 1])
        self.assertEqual(len(track.gaps), 1)
        self.assertEqual(track.gaps[0]["after_frame_index"], 0)
        self.assertEqual(track.gaps[0]["before_frame_index"], 3)
        self.assertEqual(track.gaps[0]["missed_frames"], 2)
        self.assertAlmostEqual(track.gaps[0]["length_seconds"], 0.6)
        self.assertEqual(report.resumed_tracks, 1)
        self.assertEqual(report.total_gap_events, 1)
        self.assertEqual(report.ended_by_gap, 0)

    def test_a_gap_at_exactly_the_threshold_ends_the_track(self):
        # The next detection is exactly 1.0 s after the track's last one: at
        # the threshold the track ends, and a NEW id begins.
        frames = [
            frame(0, 0.0, 1, [box(0.10, 0.10)]),
            frame(1, 1.0, 1, [box(0.10, 0.10)]),
        ]
        report = self.track(frames)
        self.assertEqual(report.track_count, 2)
        self.assertNotEqual(report.tracks[0].track_id, report.tracks[1].track_id)
        self.assertEqual(report.ended_by_gap, 1)
        self.assertEqual(report.tracks[0].detection_count, 1)
        self.assertEqual(report.tracks[1].detection_count, 1)

    def test_a_long_occlusion_yields_a_new_id_without_claiming_identity(self):
        # 2.0 s of missing time (the 1.0 s threshold is well passed): the old
        # track ends, a NEW one begins, and neither claims to be the other.
        frames = [
            frame(0, 0.0, 1, [box(0.10, 0.10)]),
            frame(1, 0.2, 1, []),
            frame(2, 0.4, 1, []),
            frame(3, 0.6, 1, []),
            frame(4, 0.8, 1, []),
            frame(5, 2.0, 1, [box(0.30, 0.10)]),
        ]
        report = self.track(frames)
        self.assertEqual(report.track_count, 2)
        first, second = report.tracks
        self.assertEqual(first.track_id, 1)
        self.assertEqual(second.track_id, 2)
        self.assertEqual(first.detection_indices, [0])
        self.assertEqual(second.detection_indices, [1])
        self.assertEqual(first.gaps, [])  # the old track never resumed
        self.assertEqual(report.ended_by_gap, 1)
        self.assertEqual(report.resumed_tracks, 0)
        # The outcome's summary states the new id without identity language.
        summary = report.summary()
        self.assertEqual(summary["track_count"], 2)
        self.assertEqual(summary["ended_by_gap"], 1)

    def test_the_gap_threshold_is_configurable(self):
        frames = [
            frame(0, 0.0, 1, [box(0.10, 0.10)]),
            frame(1, 1.4, 1, [box(0.10, 0.10)]),
        ]
        self.assertEqual(self.track(frames, max_gap_seconds=2.0).track_count, 1)
        self.assertEqual(self.track(frames, max_gap_seconds=1.0).track_count, 2)


class SessionBoundaryTests(TrackerBase):
    def test_a_session_boundary_always_ends_the_track_even_instantly(self):
        # The reappearing detection is spatially identical and only 0.2 s
        # later — but it is in session 2, so the old track ends and a NEW id
        # begins. No identity is ever carried across a session boundary.
        frames = [
            frame(0, 0.0, 1, [box(0.10, 0.10)]),
            frame(1, 0.2, 2, [box(0.10, 0.10)]),
        ]
        report = self.track(frames)
        self.assertEqual(report.track_count, 2)
        first, second = report.tracks
        self.assertEqual(first.session, 1)
        self.assertEqual(second.session, 2)
        self.assertNotEqual(first.track_id, second.track_id)
        self.assertEqual(report.ended_by_gap, 0)  # ended by the boundary, not a gap

    def test_sessions_scope_the_association(self):
        # Two sessions, each with its own mover at the same place: four
        # detections, two tracks — one per session, never merged.
        frames = [
            frame(0, 0.0, 1, [box(0.10, 0.10)]),
            frame(1, 0.2, 1, [box(0.12, 0.10)]),
            frame(2, 10.0, 2, [box(0.14, 0.10)]),
            frame(3, 10.2, 2, [box(0.16, 0.10)]),
        ]
        report = self.track(frames)
        self.assertEqual(report.track_count, 2)
        self.assertEqual([(t.session, t.detection_count) for t in report.tracks], [(1, 2), (2, 2)])

    def test_three_sessions_three_scopes(self):
        frames = [
            frame(0, 0.0, 1, [box(0.10, 0.10)]),
            frame(1, 0.2, 1, [box(0.11, 0.10)]),
            frame(2, 100.0, 2, [box(0.11, 0.10)]),
            frame(3, 100.2, 2, [box(0.12, 0.10)]),
            frame(4, 200.0, 3, [box(0.12, 0.10)]),
            frame(5, 200.2, 3, [box(0.13, 0.10)]),
        ]
        report = self.track(frames)
        self.assertEqual([t.session for t in report.tracks], [1, 2, 3])
        for track in report.tracks:
            self.assertEqual(track.detection_count, 2)
            self.assertEqual(track.camera_key, "camera")


class JitterAndRobustnessTests(TrackerBase):
    def test_small_positional_jitter_stays_one_track(self):
        frames = [
            frame(0, 0.0, 1, [box(0.100, 0.100)]),
            frame(1, 0.2, 1, [box(0.105, 0.098)]),
            frame(2, 0.4, 1, [box(0.096, 0.103)]),
            frame(3, 0.6, 1, [box(0.101, 0.099)]),
        ]
        report = self.track(frames)
        self.assertEqual(report.track_count, 1)
        self.assertEqual(report.tracks[0].detection_count, 4)

    def test_size_jitter_stays_one_track(self):
        frames = [
            frame(0, 0.0, 1, [box(0.10, 0.10, size=0.20)]),
            frame(1, 0.2, 1, [box(0.10, 0.10, size=0.26)]),
            frame(2, 0.4, 1, [box(0.10, 0.10, size=0.17)]),
        ]
        self.assertEqual(self.track(frames).track_count, 1)

    def test_a_sudden_jump_out_of_memory_starts_a_new_track(self):
        frames = [
            frame(0, 0.0, 1, [box(0.10, 0.10)]),
            frame(1, 0.2, 1, [box(0.70, 0.70)]),  # nothing like the first box
        ]
        report = self.track(frames)
        self.assertEqual(report.track_count, 2)

    def test_ambiguous_overlaps_resolve_greedily_and_deterministically(self):
        # Two detections both overlapping one track: the higher score wins.
        frames = [
            frame(0, 0.0, 1, [box(0.10, 0.10)]),
            frame(1, 0.2, 1, [box(0.11, 0.10, score=0.9), box(0.10, 0.11, score=0.5)]),
        ]
        report = self.track(frames)
        self.assertEqual(report.track_count, 2)
        continued = [t for t in report.tracks if t.detection_count == 2][0]
        self.assertEqual(continued.detection_indices, [0, 1])  # the 0.9-score box

    def test_labels_travel_with_their_tracks(self):
        frames = [
            frame(0, 0.0, 1, [box(0.10, 0.10, label="person"), box(0.70, 0.10, label="car")]),
            frame(1, 0.2, 1, [box(0.12, 0.10, label="person"), box(0.68, 0.10, label="car")]),
        ]
        report = self.track(frames)
        self.assertEqual(report.track_count, 2)
        by_label = {t.observations[0].label: t for t in report.tracks}
        self.assertEqual([o.label for o in by_label["person"].observations], ["person", "person"])
        self.assertEqual([o.label for o in by_label["car"].observations], ["car", "car"])
        self.assertEqual(by_label["person"].summary()["labels"], ["person"])


class DeterminismTests(TrackerBase):
    def _busy_frames(self):
        return [
            frame(0, 0.0, 1, [box(0.10, 0.10, score=0.8), box(0.70, 0.70, score=0.4)]),
            frame(1, 0.2, 1, [box(0.14, 0.12, score=0.6), box(0.66, 0.68, score=0.5)]),
            frame(2, 0.4, 1, []),
            frame(3, 0.6, 1, [box(0.18, 0.14, score=0.9), box(0.62, 0.66, score=0.3)]),
            frame(4, 2.0, 2, [box(0.18, 0.14, score=0.7), box(0.62, 0.66, score=0.2)]),
            frame(5, 2.2, 2, [box(0.22, 0.16, score=0.5)]),
        ]

    def test_the_same_input_always_produces_the_same_tracks(self):
        first = self.track(self._busy_frames())
        second = self.track(self._busy_frames())
        self.assertEqual(first.summary(), second.summary())
        self.assertEqual(
            [(t.track_id, t.detection_indices) for t in first.tracks],
            [(t.track_id, t.detection_indices) for t in second.tracks],
        )

    def test_equal_scores_tie_break_deterministically(self):
        # All scores identical: the tie must still resolve the same way twice.
        frames = [
            frame(0, 0.0, 1, [box(0.10, 0.10, score=0.5), box(0.11, 0.11, score=0.5)]),
            frame(1, 0.2, 1, [box(0.12, 0.10, score=0.5), box(0.13, 0.11, score=0.5)]),
        ]
        first = self.track(frames)
        second = self.track(frames)
        self.assertEqual(first.summary(), second.summary())

    def test_the_summary_is_plain_json(self):
        report = self.track(self._busy_frames())
        encoded = json.dumps(report.summary(), allow_nan=False)
        self.assertIn('"track_count"', encoded)


class RefusalTests(TrackerBase):
    def test_bad_parameters_are_refused(self):
        for bad_gap in (0, -1, float("nan"), float("inf"), True, "one"):
            with self.subTest(max_gap_seconds=bad_gap):
                with self.assertRaises(tracking.TrackingError):
                    self.track([], max_gap_seconds=bad_gap)
        for bad_overlap in (0, -0.1, 1.5, float("nan"), True):
            with self.subTest(min_overlap=bad_overlap):
                with self.assertRaises(tracking.TrackingError):
                    self.track([], min_overlap=bad_overlap)

    def test_out_of_order_frames_are_refused(self):
        with self.assertRaises(tracking.TrackingError) as raised:
            self.track([frame(1, 0.2, 1, []), frame(0, 0.0, 1, [])])
        self.assertIn("ascending frame-index order", str(raised.exception))

    def test_backwards_pts_is_refused(self):
        with self.assertRaises(tracking.TrackingError) as raised:
            self.track([frame(0, 0.4, 1, []), frame(1, 0.2, 1, [])])
        self.assertIn("non-decreasing", str(raised.exception))

    def test_backwards_sessions_are_refused(self):
        with self.assertRaises(tracking.TrackingError) as raised:
            self.track([frame(0, 0.0, 2, []), frame(1, 0.2, 1, [])])
        self.assertIn("non-decreasing", str(raised.exception))

    def test_malformed_frames_are_refused(self):
        for bad in (None, 7, "frame", {"pts": 0.0}, {"frame_index": 0}):
            with self.subTest(frame=bad):
                with self.assertRaises(tracking.TrackingError):
                    self.track([bad])

    def test_malformed_detections_are_refused_never_clamped(self):
        for bad_box in (
            {"label": "person", "x": -0.1, "y": 0.1, "width": 0.2, "height": 0.2, "score": 0.9},
            {"label": "person", "x": 0.9, "y": 0.1, "width": 0.5, "height": 0.2, "score": 0.9},
            {"label": "person", "x": 0.1, "y": 0.1, "width": 0.0, "height": 0.2, "score": 0.9},
            {"label": "person", "x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2, "score": 1.5},
            {"label": "", "x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2, "score": 0.9},
            {"label": "person", "x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2},
            {
                "label": "person",
                "x": 0.1,
                "y": 0.1,
                "width": 0.2,
                "height": 0.2,
                "score": float("nan"),
            },
            "not-a-box",
        ):
            with self.subTest(box=bad_box):
                with self.assertRaises(tracking.TrackingError) as raised:
                    self.track([frame(0, 0.0, 1, [bad_box])])
                self.assertIn("not a valid normalized box", str(raised.exception))

    def test_plain_dicts_and_objects_are_accepted_like_frame_detections(self):
        dict_frames = [
            {"frame_index": 0, "pts": 0.0, "session": 1, "detections": [box(0.1, 0.1)]},
            {"frame_index": 1, "pts": 0.2, "session": 1, "detections": [box(0.12, 0.1)]},
        ]

        class _Obj:
            def __init__(self, index, pts, detections):
                self.frame_index, self.pts, self.session, self.detections = index, pts, 1, detections

        object_frames = [_Obj(0, 0.0, [box(0.1, 0.1)]), _Obj(1, 0.2, [box(0.12, 0.1)])]
        expected = self.track(
            [frame(0, 0.0, 1, [box(0.1, 0.1)]), frame(1, 0.2, 1, [box(0.12, 0.1)])]
        ).summary()
        self.assertEqual(self.track(dict_frames).summary(), expected)
        self.assertEqual(self.track(object_frames).summary(), expected)


# -- the worker integration (T21's fakes, T22's tracks in the outcome) ------------------------------
class ScriptedDetector(FakeDetector):
    """A detector whose boxes vary per predict call, in call order.

    ``script`` is consumed one entry per predict call (one entry per sampled
    frame); once exhausted it falls back to ``boxes``. The script lets worker
    tests stage occlusions and reappearances that survive the real adapter.
    """

    name = "t22-scripted-detector"
    description = "T22 test double."
    script: list[list[BoxPrediction]] = []

    def predict(self, artifact, image, thresholds):
        if type(self).fail_with:
            raise RuntimeError(type(self).fail_with)
        script = type(self).script
        if script:
            found = script.pop(0)
            return [b for b in found if b.score >= thresholds.get("confidence", 0)]
        return [b for b in type(self).boxes if b.score >= thresholds.get("confidence", 0)]


@skipUnless(REQUIRE_FFMPEG, "ffmpeg and ffprobe required for worker execution tests")
class WorkerTrackingTests(InferenceBase):
    """The execution step records track summaries when the adapter ran."""

    def setUp(self):
        super().setUp()
        register_backend(ScriptedDetector)
        self.addCleanup(lambda: unregister_backend("t22-scripted-detector"))
        ScriptedDetector.script = []
        ScriptedDetector.fail_with = None
        self.body = make_clip(self.tmp, seconds=2, creation_time="2026-11-01T09:30:00Z")
        self.recording = self.store_recording(self.body, duration=2.0, creation_time="2026-11-01T09:30:00Z")
        self.model = self.make_model(backend="t22-scripted-detector")
        self.binding = self.make_binding(self.model)

    def _run_execution(self, bundle):
        execution = self.enqueue_and_claim(bundle)
        return executions.run_execution(execution, "worker-a")

    def test_with_model_the_outcome_records_track_summaries(self):
        # Default fake: one constant box on every sampled frame, one session.
        bundle = self.make_bundle(binding=self.binding)
        finished = self._run_execution(bundle)
        self.assertEqual(finished.status, "succeeded", finished.error)
        result = finished.result
        self.assertIn("detector", result)
        self.assertIn("tracks", result)
        tracks = result["tracks"]
        # One spatially constant box on every sampled frame: exactly one track
        # spanning the sampled frames, no occlusion.
        self.assertEqual(tracks["track_count"], 1)
        self.assertEqual(tracks["camera_key"], f"recording:{self.recording.id}")
        self.assertEqual(tracks["sampled_frame_count"], result["detector"]["frame_count"])
        self.assertEqual(tracks["sessions"], 1)
        self.assertEqual(tracks["max_gap_seconds"], tracking.DEFAULT_MAX_GAP_SECONDS)
        track = tracks["tracks"][0]
        self.assertEqual(track["detection_count"], tracks["sampled_frame_count"])
        self.assertEqual(track["session"], 1)
        self.assertEqual(track["gaps"], [])
        self.assertEqual(track["detection_indices"], list(range(tracks["sampled_frame_count"])))
        self.assertEqual(track["labels"], ["person"])
        # The honesty note: what ran, and that ids are per-session, not identities.
        self.assertIn("associated the detections into per-session tracks", result["note"])
        self.assertIn("not persistent identities", result["note"])
        self.assertIn("reacquisition after a longer gap is a new track id", result["note"])
        # The detector block is unchanged, and the rest of the outcome stands.
        self.assertEqual(result["detector"]["total_detections"], tracks["sampled_frame_count"])
        self.assertEqual(result["recording_sha256"], self.recording.sha256)
        self.assertTrue(result["duration_check"]["matches"])

    def test_a_short_occlusion_resumes_the_same_id_through_the_worker(self):
        # The 2 s clip at 5 fps samples frames [0, 2, 4, 5, 7, 9]; the script
        # hides the box on frame 4 only — 0.2 s of missing time, under the
        # 1.0 s threshold, so the SAME id resumes with the gap recorded.
        ScriptedDetector.script = [
            [BoxPrediction("person", 0.1, 0.2, 0.3, 0.4, 0.9)],
            [BoxPrediction("person", 0.1, 0.2, 0.3, 0.4, 0.9)],
            [],
            [BoxPrediction("person", 0.1, 0.2, 0.3, 0.4, 0.9)],
            [BoxPrediction("person", 0.1, 0.2, 0.3, 0.4, 0.9)],
            [BoxPrediction("person", 0.1, 0.2, 0.3, 0.4, 0.9)],
        ]
        bundle = self.make_bundle(binding=self.binding)
        finished = self._run_execution(bundle)
        self.assertEqual(finished.status, "succeeded", finished.error)
        tracks = finished.result["tracks"]
        self.assertEqual(tracks["track_count"], 1)
        track = tracks["tracks"][0]
        self.assertEqual(track["detection_count"], 5)
        self.assertEqual(len(track["gaps"]), 1)
        self.assertEqual(track["gaps"][0]["after_frame_index"], 2)
        self.assertEqual(track["gaps"][0]["before_frame_index"], 5)
        self.assertAlmostEqual(track["gaps"][0]["length_seconds"], 0.6)
        self.assertEqual(tracks["resumed_tracks"], 1)
        self.assertEqual(tracks["ended_by_gap"], 0)

    def test_a_long_occlusion_yields_a_new_id_through_the_worker(self):
        # The box vanishes after frame 2 (pts 0.4) and reappears on frame 9
        # (pts 1.8) — 1.4 s, past the 1.0 s threshold: the old track ends, a
        # NEW id begins, and no identity is claimed across the gap.
        ScriptedDetector.script = [
            [BoxPrediction("person", 0.1, 0.2, 0.3, 0.4, 0.9)],
            [BoxPrediction("person", 0.1, 0.2, 0.3, 0.4, 0.9)],
            [],
            [],
            [],
            [BoxPrediction("person", 0.5, 0.2, 0.3, 0.4, 0.9)],
        ]
        bundle = self.make_bundle(binding=self.binding)
        finished = self._run_execution(bundle)
        self.assertEqual(finished.status, "succeeded", finished.error)
        tracks = finished.result["tracks"]
        self.assertEqual(tracks["track_count"], 2)
        first, second = tracks["tracks"]
        self.assertNotEqual(first["track_id"], second["track_id"])
        self.assertEqual(first["detection_count"], 2)
        self.assertEqual(second["detection_count"], 1)
        self.assertEqual(first["gaps"], [])
        self.assertEqual(tracks["ended_by_gap"], 1)
        self.assertEqual(tracks["resumed_tracks"], 0)

    def test_a_session_boundary_always_yields_a_new_id_through_the_worker(self):
        # A clip with a 1.2 s timestamp gap: two sessions (frames 0-4 and
        # 5-8; frame 5 is off-cadence after the gap so 8 on-time frames
        # remain). Sampling 6 of them gives [0, 1, 3, 4, 7, 8] — the same
        # constant box in both sessions is TWO tracks, one per session.
        body = make_gap_clip(self.tmp, seconds=3, drop_between=(1.0, 2.0), creation_time="2026-11-01T09:30:00Z")
        self.recording = self.store_recording(body, duration=3.0, creation_time="2026-11-01T09:30:00Z")
        bundle = self.make_bundle(binding=self.binding)
        finished = self._run_execution(bundle)
        self.assertEqual(finished.status, "succeeded", finished.error)
        tracks = finished.result["tracks"]
        self.assertEqual(tracks["track_count"], 2)
        first, second = tracks["tracks"]
        self.assertEqual(first["session"], 1)
        self.assertEqual(second["session"], 2)
        self.assertNotEqual(first["track_id"], second["track_id"])
        self.assertEqual(first["detection_count"], 4)
        self.assertEqual(second["detection_count"], 2)
        self.assertEqual(tracks["sessions"], 2)
        self.assertEqual(tracks["ended_by_gap"], 0)  # the boundary ended them, not a gap
        self.assertEqual(tracks["resumed_tracks"], 0)

    def test_the_tracks_outcome_is_reproducible(self):
        # Two executions of the same bundle (the T21 pattern): the same
        # recording, timeline and adapter decisions give the same tracks.
        bundle = self.make_bundle(binding=self.binding)
        first = self._run_execution(bundle)
        second = self._run_execution(bundle)
        self.assertEqual(first.status, "succeeded", first.error)
        self.assertEqual(second.status, "succeeded", second.error)
        self.assertEqual(first.result["tracks"], second.result["tracks"])
        self.assertEqual(first.result["timeline_sha256"], second.result["timeline_sha256"])

    def test_without_model_the_outcome_has_no_tracks_and_the_old_note(self):
        bundle = self.make_bundle(binding=None)
        finished = self._run_execution(bundle)
        self.assertEqual(finished.status, "succeeded", finished.error)
        result = finished.result
        self.assertNotIn("detector", result)
        self.assertNotIn("tracks", result)
        self.assertIn("No model is bound", result["note"])

    def test_a_failing_backend_still_fails_the_execution_plainly(self):
        ScriptedDetector.fail_with = "weights corrupted"
        bundle = self.make_bundle(binding=self.binding)
        finished = self._run_execution(bundle)
        self.assertEqual(finished.status, "failed")
        self.assertIn("failed while analysing frame", finished.error)
