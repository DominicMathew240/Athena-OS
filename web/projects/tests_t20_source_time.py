"""T20: source time and ordered processing contracts for recorded sources.

The contracts under test come from ``docs/plans/2026-11-19-t20-source-time.md``:
three distinct clocks (capture time, frame PTS, ingestion time) that are never
conflated; a monotonic processing order derived from PTS with late frames
excluded and reported; gap/discontinuity detection against a configurable
threshold; session segmentation; and reproducibility — the same recording
digest and threshold always produce the same timeline hash. The T19 execution
step now builds this timeline, still without claiming to have watched the
video's pictures or run a model.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings

from .models import OperationSourceRecording
from .operations import executions
from .source_time import (
    SourceTimeError,
    build_timeline,
)
from .tests import grant

REQUIRE_FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")


def _run(command, *, timeout=60):
    subprocess.run(command, check=True, capture_output=True, timeout=timeout)


def make_clip(directory, *, seconds=2, creation_time=None, drop_between=None):
    """A synthetic H.264 MP4; ``drop_between`` (start, end) removes that span of
    source frames from the output while keeping the remaining frames' original
    presentation timestamps — a genuine timestamp gap, not a shorter clip."""
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=blue:s=64x48:r=5",
        "-t", str(seconds), "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
    ]
    if drop_between is not None:
        start, end = drop_between
        command += ["-vf", f"select='not(between(t,{start},{end}))'"]
    if creation_time:
        command += ["-metadata", f"creation_time={creation_time}"]
    path = Path(directory) / f"clip-{next(_counter)}.mp4"
    _run(command + ["-y", str(path)])
    data = path.read_bytes()
    path.unlink()
    return data


def _make_counter():
    value = 0
    while True:
        value += 1
        yield value


_counter = _make_counter()


class _NamedBytes:
    """A minimal file-like object the storage backend can read twice."""

    def __init__(self, data: bytes):
        from io import BytesIO

        self._stream = BytesIO(data)

    def read(self, *args):
        return self._stream.read(*args)

    def seek(self, *args):
        return self._stream.seek(*args)

    def tell(self):
        return self._stream.tell()

    def seekable(self):
        return True

    @property
    def size(self):
        position = self._stream.tell()
        size = self._stream.seek(0, 2)
        self._stream.seek(position)
        return size

    def chunks(self):
        yield self._stream.getvalue()

    def multiple_chunks(self):
        return False


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="t20-media-"))
@skipUnless(REQUIRE_FFMPEG, "ffmpeg and ffprobe required for source-time tests")
class SourceTimeBase(TestCase):
    """A durable source recording in private storage, like T09 leaves behind."""

    def setUp(self):
        # Files survive DB rollback between tests; start every test with empty storage.
        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)
        Path(settings.MEDIA_ROOT).mkdir(parents=True, exist_ok=True)
        self.user = get_user_model().objects.create_user("t20-owner", is_superuser=True)
        grant(self.user, "approver")
        self.client.force_login(self.user)
        from .models import OperationSource, Site

        site = Site.objects.create(created_by=self.user, name="Depot")
        self.source = OperationSource.objects.create(site=site, name="Gate", kind="recorded_video")

    def store_recording(self, body: bytes, *, creation_time=None) -> OperationSourceRecording:
        key = default_storage.save(f"operation-source-recordings/{self.source.id}/rec.mp4", _NamedBytes(body))
        self.addCleanup(default_storage.delete, key)
        return OperationSourceRecording.objects.create(
            source=self.source,
            created_by=self.user,
            storage_key=key,
            filename="rec.mp4",
            sha256=hashlib.sha256(body).hexdigest(),
            size_bytes=len(body),
            width=64,
            height=48,
            duration_seconds=float(len(body)),  # not used by the timeline; honest placeholder
            captured_at=(
                datetime.fromisoformat(creation_time.replace("Z", "+00:00")) if creation_time else None
            ),
        )


# -- the three clocks and the ordered timeline --------------------------------------------------
class ThreeClocksTests(SourceTimeBase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="t20-fixture-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_frame_pts_order_and_cadence(self):
        body = make_clip(self.tmp, seconds=2, creation_time="2026-11-01T09:30:00Z")
        recording = self.store_recording(body, creation_time="2026-11-01T09:30:00Z")
        timeline = build_timeline(recording)
        self.assertEqual(timeline.recording_sha256, recording.sha256)
        self.assertEqual(len(timeline.frames), 10)  # 5 fps for 2 s
        self.assertEqual([frame.pts for frame in timeline.frames], [round(i * 0.2, 6) for i in range(10)])
        self.assertTrue(all(frame.on_time for frame in timeline.frames))
        self.assertEqual(timeline.late_frames, [])
        self.assertEqual(timeline.gaps, [])
        self.assertEqual(timeline.jitter_count, 0)

    def test_wall_clock_mapping_comes_from_capture_time_plus_pts(self):
        body = make_clip(self.tmp, seconds=1, creation_time="2026-11-01T09:30:00Z")
        recording = self.store_recording(body, creation_time="2026-11-01T09:30:00Z")
        timeline = build_timeline(recording)
        self.assertEqual(timeline.captured_at, "2026-11-01T09:30:00+00:00")
        self.assertEqual(timeline.frames[0].wall_clock, "2026-11-01T09:30:00+00:00")
        self.assertEqual(timeline.frames[-1].wall_clock, "2026-11-01T09:30:00.800000+00:00")  # 5 fps

    def test_no_capture_time_means_null_wall_clock_not_a_guess(self):
        body = make_clip(self.tmp, seconds=1)  # no creation_time metadata
        recording = self.store_recording(body)
        timeline = build_timeline(recording)
        self.assertIsNone(timeline.captured_at)
        self.assertTrue(all(frame.wall_clock is None for frame in timeline.frames))
        self.assertEqual(len(timeline.frames), 5)  # PTS still fully usable

    def test_ingestion_time_is_the_row_creation_time_and_is_not_conflated(self):
        body = make_clip(self.tmp, seconds=1, creation_time="2026-11-01T09:30:00Z")
        recording = self.store_recording(body, creation_time="2026-11-01T09:30:00Z")
        timeline = build_timeline(recording)
        # Ingestion time exists independently on the row; the timeline carries
        # the capture clock and PTS only, never the ingestion clock.
        self.assertIsNotNone(recording.created_at)
        self.assertGreater(recording.created_at, datetime.fromisoformat("2026-11-01T09:31:00+00:00"))
        self.assertNotIn(json.dumps(recording.created_at.isoformat()), timeline.canonical())


# -- reproducibility ----------------------------------------------------------------------------
class ReproducibilityTests(SourceTimeBase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="t20-fixture-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_same_digest_same_threshold_identical_timeline(self):
        body = make_clip(self.tmp, seconds=2, creation_time="2026-11-01T09:30:00Z")
        recording = self.store_recording(body, creation_time="2026-11-01T09:30:00Z")
        first = build_timeline(recording)
        second = build_timeline(recording)
        self.assertEqual(first.canonical(), second.canonical())
        self.assertEqual(first.timeline_sha256(), second.timeline_sha256())

    def test_the_timeline_hash_is_stable_across_separate_recordings_of_the_same_bytes(self):
        body = make_clip(self.tmp, seconds=2, creation_time="2026-11-01T09:30:00Z")
        first = build_timeline(self.store_recording(body, creation_time="2026-11-01T09:30:00Z"))
        second = build_timeline(self.store_recording(body, creation_time="2026-11-01T09:30:00Z"))
        self.assertEqual(first.timeline_sha256(), second.timeline_sha256())

    def test_a_different_recording_produces_a_different_timeline(self):
        self.assertNotEqual(
            build_timeline(
                self.store_recording(
                    make_clip(self.tmp, seconds=1, creation_time="2026-11-01T09:30:00Z"),
                    creation_time="2026-11-01T09:30:00Z",
                )
            ).timeline_sha256(),
            build_timeline(
                self.store_recording(
                    make_clip(self.tmp, seconds=2, creation_time="2026-11-01T09:30:00Z"),
                    creation_time="2026-11-01T09:30:00Z",
                )
            ).timeline_sha256(),
        )


# -- gaps, jitter and sessions ------------------------------------------------------------------
class GapAndSessionTests(SourceTimeBase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="t20-fixture-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_a_timestamp_gap_is_detected_with_its_bounds(self):
        # Source frames between 1.0 s and 2.0 s are removed; the remaining
        # frames keep their original timestamps (0.8 s then 2.2 s).
        body = make_clip(self.tmp, seconds=3, drop_between=(1.0, 2.0), creation_time="2026-11-01T09:30:00Z")
        recording = self.store_recording(body, creation_time="2026-11-01T09:30:00Z")
        timeline = build_timeline(recording)
        self.assertEqual(len(timeline.gaps), 1)
        gap = timeline.gaps[0]
        self.assertAlmostEqual(gap.start_pts, 0.8, places=5)
        self.assertAlmostEqual(gap.end_pts, 2.2, places=5)
        self.assertAlmostEqual(gap.length_seconds, 1.2, places=5)

    def test_the_gap_threshold_is_policy_and_is_recorded(self):
        body = make_clip(self.tmp, seconds=3, drop_between=(1.0, 2.0), creation_time="2026-11-01T09:30:00Z")
        recording = self.store_recording(body, creation_time="2026-11-01T09:30:00Z")
        permissive = build_timeline(recording, gap_threshold_seconds=5.0)
        self.assertEqual(permissive.gaps, [])
        self.assertEqual(len(permissive.sessions), 1)
        self.assertEqual(permissive.gap_threshold_seconds, 5.0)
        strict = build_timeline(recording, gap_threshold_seconds=0.5)
        self.assertEqual(len(strict.gaps), 1)
        self.assertNotEqual(permissive.timeline_sha256(), strict.timeline_sha256())

    def test_one_gap_splits_the_recording_into_two_sessions(self):
        body = make_clip(self.tmp, seconds=3, drop_between=(1.0, 2.0), creation_time="2026-11-01T09:30:00Z")
        recording = self.store_recording(body, creation_time="2026-11-01T09:30:00Z")
        timeline = build_timeline(recording)
        self.assertEqual([session.number for session in timeline.sessions], [1, 2])
        first, second = timeline.sessions
        self.assertEqual(first.frame_count + second.frame_count, len(timeline.frames))
        self.assertEqual(first.last_frame + 1, second.first_frame)
        self.assertAlmostEqual(first.last_pts, 0.8, places=5)
        self.assertAlmostEqual(second.first_pts, 2.2, places=5)
        # Every frame belongs to exactly one session, in PTS order.
        self.assertEqual([frame.session for frame in timeline.frames], [1] * 5 + [2] * 4)
        # Wall-clock bounds follow capture time plus PTS.
        self.assertEqual(second.first_wall_clock, "2026-11-01T09:30:02.200000+00:00")

    def test_a_healthy_recording_is_one_session(self):
        body = make_clip(self.tmp, seconds=2, creation_time="2026-11-01T09:30:00Z")
        recording = self.store_recording(body, creation_time="2026-11-01T09:30:00Z")
        timeline = build_timeline(recording)
        self.assertEqual(len(timeline.sessions), 1)
        self.assertEqual(timeline.sessions[0].frame_count, 10)
        self.assertEqual(timeline.sessions[0].first_frame, 0)
        self.assertEqual(timeline.sessions[0].last_frame, 9)

    def test_two_gaps_make_three_sessions(self):
        # Two drop windows: frames 1.0–2.0 s and 3.0–4.0 s are removed while
        # the remaining frames keep their original timestamps.
        path = Path(self.tmp) / "two-gaps.mp4"
        _run([
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=red:s=64x48:r=5", "-t", "5",
            "-vf", "select='not(between(t,1,2)+between(t,3,4))'",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            "-metadata", "creation_time=2026-11-01T09:30:00Z", "-y", str(path),
        ])
        body = path.read_bytes()
        path.unlink()
        recording = self.store_recording(body, creation_time="2026-11-01T09:30:00Z")
        timeline = build_timeline(recording)
        self.assertEqual(len(timeline.gaps), 2)
        self.assertAlmostEqual(timeline.gaps[0].start_pts, 0.8, places=5)
        self.assertAlmostEqual(timeline.gaps[0].end_pts, 2.2, places=5)
        self.assertAlmostEqual(timeline.gaps[1].start_pts, 2.8, places=5)
        self.assertAlmostEqual(timeline.gaps[1].end_pts, 4.2, places=5)
        self.assertEqual([session.number for session in timeline.sessions], [1, 2, 3])
        self.assertEqual(
            [session.frame_count for session in timeline.sessions],
            [5, 4, 4],
        )
        self.assertEqual(sum(session.frame_count for session in timeline.sessions), len(timeline.frames))

    def test_invalid_threshold_is_refused_with_a_plain_message(self):
        body = make_clip(self.tmp, seconds=1, creation_time="2026-11-01T09:30:00Z")
        recording = self.store_recording(body, creation_time="2026-11-01T09:30:00Z")
        for bad in (0, -1, float("nan"), float("inf"), "two"):
            with self.subTest(threshold=bad):
                with self.assertRaises(SourceTimeError) as raised:
                    build_timeline(recording, gap_threshold_seconds=bad)
                self.assertIn("gap threshold", str(raised.exception))


# -- corrupt, missing and changed recordings ----------------------------------------------------
class RefusalTests(SourceTimeBase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="t20-fixture-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.body = make_clip(self.tmp, seconds=1, creation_time="2026-11-01T09:30:00Z")

    def test_missing_recording_is_refused_with_a_plain_message(self):
        recording = self.store_recording(self.body, creation_time="2026-11-01T09:30:00Z")
        default_storage.delete(recording.storage_key)
        with self.assertRaises(SourceTimeError) as raised:
            build_timeline(recording)
        self.assertIn("could not be read from storage", str(raised.exception))

    def test_changed_recording_is_refused(self):
        recording = self.store_recording(self.body, creation_time="2026-11-01T09:30:00Z")
        default_storage.delete(recording.storage_key)
        default_storage.save(recording.storage_key, _NamedBytes(b"tampered"))
        with self.assertRaises(SourceTimeError) as raised:
            build_timeline(recording)
        self.assertIn("changed on disk", str(raised.exception))

    def test_corrupt_recording_is_refused_not_returned_as_an_empty_timeline(self):
        broken = bytearray(self.body)
        for offset in range(len(broken) // 2, len(broken) - 1):
            broken[offset] = broken[offset] ^ 0xFF
        recording = self.store_recording(bytes(broken), creation_time="2026-11-01T09:30:00Z")
        # The stored digest matches the corrupt bytes; the decode check must refuse.
        with self.assertRaises(SourceTimeError) as raised:
            build_timeline(recording)
        self.assertIn("no longer decode", str(raised.exception))

    def test_a_file_with_no_video_frames_is_refused(self):
        recording = self.store_recording(b"t20-not-a-real-video" * 64, creation_time=None)
        with self.assertRaises(SourceTimeError) as raised:
            build_timeline(recording)
        # Refused either at decode or at frame reading — both are plain refusals.
        self.assertIn(
            str(raised.exception),
            ("The recording's pictures no longer decode. The file is damaged; re-import it.",
             "The recording's frame timestamps could not be read.",
             "The recording has no video frames."),
        )


# -- the T19 execution step integration ---------------------------------------------------------
class ExecutionTimelineTests(SourceTimeBase):
    """An enqueued execution now reports the pinned recording's frame timeline."""

    def setUp(self):
        super().setUp()
        from .models import Operation

        self.operation = Operation.objects.create(
            created_by=self.user, site=self.source.site, source=self.source, name="Gate count"
        )
        self.tmp = tempfile.mkdtemp(prefix="t20-fixture-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.body = make_clip(self.tmp, seconds=3, drop_between=(1.0, 2.0), creation_time="2026-11-01T09:30:00Z")
        self.recording = self.store_recording(self.body, creation_time="2026-11-01T09:30:00Z")
        self.bundle = self._bundle()

    def _bundle(self):
        from .models import (
            DatasetVersion,
            ModelVersion,
            OperationDraft,
            OperationReleaseBundle,
            Project,
            TrainingRun,
        )

        draft = OperationDraft.objects.create(created_by=self.user, operation=self.operation, name="Gate count")
        project = Project.objects.create(name="Counting", modality="image", task="Detect", labels=["person"])
        dataset_version = DatasetVersion.objects.create(
            project=project, version=1, manifest={}, manifest_sha256="c" * 64, created_by=self.user
        )
        training_run = TrainingRun.objects.create(
            project=project,
            dataset_version=dataset_version,
            backend="fake",
            config={},
            config_sha256="d" * 64,
            dataset_manifest_sha256="e" * 64,
            created_by=self.user,
        )
        model = ModelVersion.objects.create(
            project=project,
            version=1,
            training_run=training_run,
            backend="fake",
            artifact_path=f"models/{project.id}/v1.onnx",
            artifact_sha256="a" * 64,
            artifact_size_bytes=100,
            label_names=["person"],
            stage=ModelVersion.APPROVED,
        )
        snapshot = {
            "schema_version": 1,
            "kind": "operation_release_bundle",
            "operation_id": str(self.operation.id),
            "draft_id": "0" * 8 + "-0000-0000-0000-" + "0" * 12,
            "draft_revision": 1,
            "component_pins": {
                "source_view": {"id": "sv", "version": 1, "sha256": "1" * 64},
                "geometry": {"id": "geo", "version": 1, "sha256": "2" * 64},
                "recipe": {
                    "id": "rec",
                    "version": 1,
                    "recipe_key": "people_counting",
                    "sha256": "3" * 64,
                    "configuration": {"comparison_tolerance_seconds": 0.5},
                },
                "model": {"id": "mdl", "version": 1, "model_version_id": str(model.id), "sha256": "4" * 64},
                "screen": {"id": "scr", "version": 1, "sha256": "5" * 64},
                "policy": {"id": "pol", "version": 1, "sha256": "6" * 64},
            },
            "recording": {
                "sha256": self.recording.sha256,
                "size_bytes": self.recording.size_bytes,
                "width": 64,
                "height": 48,
                "duration_seconds": 3.0,
            },
            "reference_events": [
                {"time": 0.5, "direction": "negative_to_positive"},
                {"time": 2.5, "direction": "positive_to_negative"},
            ],
            "release_ready": True,
        }
        encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return OperationReleaseBundle.objects.create(
            operation=self.operation,
            draft=draft,
            draft_revision=1,
            number=1,
            created_by=self.user,
            snapshot=snapshot,
            snapshot_sha256=hashlib.sha256(encoded.encode()).hexdigest(),
        )

    def _enqueue_and_claim(self):
        response = self.client.post(
            f"/api/operation-release-bundles/{self.bundle.id}/executions",
            json.dumps({"recording_id": str(self.recording.id)}),
            content_type="application/json",
        )
        assert response.status_code == 202, response.content
        return executions.claim_next_execution("worker-a")

    def test_execution_reports_frames_gaps_sessions_and_stays_honest(self):
        execution = self._enqueue_and_claim()
        finished = executions.run_execution(execution, "worker-a")
        self.assertEqual(finished.status, "succeeded", finished.error)
        result = finished.result
        timeline = result["frame_timeline"]
        self.assertEqual(timeline["recording_sha256"], self.recording.sha256)
        self.assertEqual(timeline["frame_count"], 9)
        self.assertEqual(len(timeline["gaps"]), 1)
        self.assertAlmostEqual(timeline["gaps"][0]["length_seconds"], 1.2, places=5)
        self.assertEqual(len(timeline["sessions"]), 2)
        self.assertEqual(timeline["captured_at"], "2026-11-01T09:30:00+00:00")
        self.assertEqual(result["timeline_sha256"], build_timeline(self.recording).timeline_sha256())
        # The honesty rule is unchanged and explicit.
        self.assertIn("did not watch the video's pictures or run a model", result["note"])
        self.assertNotIn("watched", result["note"].split("did not watch")[0])

    def test_execution_of_a_corrupt_recording_fails_with_a_plain_message(self):
        execution = self._enqueue_and_claim()
        broken = bytearray(self.body)
        for offset in range(len(broken) // 2, len(broken) - 1):
            broken[offset] = broken[offset] ^ 0xFF
        default_storage.delete(self.recording.storage_key)
        default_storage.save(self.recording.storage_key, _NamedBytes(bytes(broken)))
        with self.assertRaises(SourceTimeError):
            build_timeline(self.recording)
        # The worker records the plain refusal on the row instead of raising.
        self.recording.sha256 = hashlib.sha256(bytes(broken)).hexdigest()
        self.recording.save(update_fields=["sha256"])
        execution = self._enqueue_and_claim()
        finished = executions.run_execution(execution, "worker-a")
        self.assertEqual(finished.status, "failed")
        self.assertIn("no longer decode", finished.error)

    def test_execution_outcome_is_reproducible_across_two_runs(self):
        first = executions.run_execution(self._enqueue_and_claim(), "worker-a")
        second = executions.run_execution(self._enqueue_and_claim(), "worker-b")
        self.assertEqual(first.status, "succeeded")
        self.assertEqual(second.status, "succeeded")
        self.assertEqual(first.result["timeline_sha256"], second.result["timeline_sha256"])
        self.assertEqual(first.result["frame_timeline"], second.result["frame_timeline"])
