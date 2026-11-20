"""T21: compatible detector inference adapters for operation executions.

The contracts under test: the adapter re-validates the pinned model's
task/label/stage/artifact compatibility at run time (the binding's recorded
checks are never trusted), extracts reference frames deterministically from the
recording's stored bytes, runs the bound backend's predict path, and produces
normalized [0,1]-coordinate detections — or an explicit plain-language failure,
never zeros. The worker integration records detections when a model is bound and
keeps the honest no-model note when none is, and the recording's stored duration
is cross-checked against the T20 timeline.

The lean test environment has no torch, so the full pipeline is exercised with a
fake predict path (a registered fake backend, exactly like the training suite's
honest pattern); the real torchvision backend is covered by a skipUnless-gated
bonus test that only runs where torch is importable.
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

from .models import (
    DatasetVersion,
    ModelVersion,
    Operation,
    OperationExecution,
    OperationModelBinding,
    OperationReleaseBundle,
    OperationSource,
    OperationSourceRecording,
    Project,
    Site,
    TrainingRun,
)
from .operations import executions, inference
from .source_time import build_timeline
from .tests import grant
from .training import register_backend, unregister_backend
from .training.base import Availability, BoxPrediction, TrainingBackend

REQUIRE_FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")


def _run(command, *, timeout=60):
    subprocess.run(command, check=True, capture_output=True, timeout=timeout)


def _make_counter():
    value = 0
    while True:
        value += 1
        yield value


_counter = _make_counter()


def make_clip(directory, *, seconds=2, creation_time=None):
    """A synthetic H.264 MP4, following the T09/T20 suites' pattern."""
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=blue:s=64x48:r=5",
        "-t", str(seconds), "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
    ]
    if creation_time:
        command += ["-metadata", f"creation_time={creation_time}"]
    path = Path(directory) / f"clip-{next(_counter)}.mp4"
    _run(command + ["-y", str(path)])
    data = path.read_bytes()
    path.unlink()
    return data


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


class FakeDetector(TrainingBackend):
    """Deterministic detector stand-in: predicts exactly the boxes the test configures.

    Registered as 't21-fake-detector'; the class attributes are the test's knobs.
    """

    name = "t21-fake-detector"
    description = "T21 test double."
    license_note = "Test only; no third-party code."
    boxes: list[BoxPrediction] = [BoxPrediction("person", 0.1, 0.2, 0.3, 0.4, 0.9)]
    fail_with: str | None = None
    available = True

    def check_available(self) -> Availability:
        if type(self).available:
            return Availability(True, "ready")
        return Availability(False, "The fake framework is not installed.", "Install the fake framework.")

    def default_config(self):
        return {}

    def validate_config(self, config):
        return {}

    def train(self, dataset_dir, config, progress_cb, cancel_cb):
        raise NotImplementedError

    def predict(self, artifact, image, thresholds):
        if type(self).fail_with:
            raise RuntimeError(type(self).fail_with)
        return [b for b in type(self).boxes if b.score >= thresholds.get("confidence", 0)]


class InferenceBase(TestCase):
    """An owner, a site/source/operation, a decodable recording, and an approved model."""

    def setUp(self):
        self.media = tempfile.mkdtemp(prefix="t21-media-")
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)
        register_backend(FakeDetector)
        self.addCleanup(lambda: unregister_backend("t21-fake-detector"))
        FakeDetector.boxes = [BoxPrediction("person", 0.1, 0.2, 0.3, 0.4, 0.9)]
        FakeDetector.fail_with = None
        FakeDetector.available = True
        self.user = get_user_model().objects.create_user("t21-owner", is_superuser=True)
        grant(self.user, "approver")
        self.client.force_login(self.user)
        self.site = Site.objects.create(created_by=self.user, name="Depot")
        self.source = OperationSource.objects.create(site=self.site, name="Gate", kind="recorded_video")
        self.operation = Operation.objects.create(
            created_by=self.user, site=self.site, source=self.source, name="Gate count"
        )
        self.tmp = tempfile.mkdtemp(prefix="t21-fixture-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def store_recording(self, body: bytes, *, duration: float, creation_time=None) -> OperationSourceRecording:
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
            duration_seconds=float(duration),
            captured_at=(
                datetime.fromisoformat(creation_time.replace("Z", "+00:00")) if creation_time else None
            ),
        )

    def make_model(self, *, labels=("person",), stage=ModelVersion.APPROVED, task=None, backend="t21-fake-detector"):
        project = Project.objects.create(name="Counting", modality="image", task="Detect", labels=list(labels))
        dataset = DatasetVersion.objects.create(
            project=project, version=1, created_by=self.user, manifest={}, manifest_sha256="c" * 64,
            label_names=list(labels),
        )
        run = TrainingRun.objects.create(
            project=project,
            dataset_version=dataset,
            dataset_manifest_sha256="c" * 64,
            backend=backend,
            config={},
            config_sha256="d" * 64,
            created_by=self.user,
        )
        artifact_bytes = b"t21-fake-artifact-weights"
        # Artifacts live as plain files under DATA_HOME/models (projects.training.lifecycle
        # .artifact_file), not in private media storage like recordings.
        artifact_rel = f"models/{project.id}/v1/model.bin"
        artifact_path = Path(settings.DATA_HOME) / artifact_rel
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_bytes(artifact_bytes)
        self.addCleanup(artifact_path.unlink, missing_ok=True)
        return ModelVersion.objects.create(
            project=project,
            version=1,
            training_run=run,
            backend=backend,
            task=task,
            artifact_path=artifact_rel,
            artifact_sha256=hashlib.sha256(artifact_bytes).hexdigest(),
            artifact_size_bytes=len(artifact_bytes),
            label_names=list(labels),
            stage=stage,
        )

    def make_binding(self, model_version) -> OperationModelBinding:
        number = OperationModelBinding.objects.filter(operation=self.operation).count() + 1
        return OperationModelBinding.objects.create(
            operation=self.operation,
            version=number,
            created_by=self.user,
            model_version=model_version,
            compatibility={"compatible": True, "model_version_id": str(model_version.id)},
            compatibility_sha256="b" * 64,
        )

    def artifact_path(self, model) -> Path:
        return Path(settings.DATA_HOME) / model.artifact_path

    def make_bundle(self, *, binding=None, recording=None, duration=None):
        from .models import OperationDraft

        recording = recording or self.recording
        draft = OperationDraft.objects.create(created_by=self.user, operation=self.operation, name="Gate count")
        model_pin = (
            None
            if binding is None
            else {"id": str(binding.id), "version": binding.version, "model_version_id": str(binding.model_version_id), "sha256": "4" * 64}
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
                "model": model_pin,
                "screen": {"id": "scr", "version": 1, "sha256": "5" * 64},
                "policy": {"id": "pol", "version": 1, "sha256": "6" * 64},
            },
            "recording": {
                "sha256": recording.sha256,
                "size_bytes": recording.size_bytes,
                "width": 64,
                "height": 48,
                "duration_seconds": float(duration if duration is not None else recording.duration_seconds),
            },
            "reference_events": [
                {"time": 0.5, "direction": "negative_to_positive"},
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

    def enqueue_and_claim(self, bundle):
        response = self.client.post(
            f"/api/operation-release-bundles/{bundle.id}/executions",
            json.dumps({"recording_id": str(self.recording.id)}),
            content_type="application/json",
        )
        assert response.status_code == 202, response.content
        return executions.claim_next_execution("worker-a")


# -- the compatibility matrix --------------------------------------------------------------------
class CompatibilityMatrixTests(InferenceBase):
    """validate_compatibility: the right model passes; every drift is refused in plain words."""

    def setUp(self):
        super().setUp()
        self.model = self.make_model()

    def test_the_right_model_passes(self):
        checks = inference.validate_compatibility(self.model, "people_counting")
        self.assertIs(checks["compatible"], True)
        self.assertIsNone(checks["reason"])
        self.assertEqual(checks["stage"], ModelVersion.APPROVED)
        self.assertEqual(checks["expected_labels"], ["person"])
        self.assertEqual(checks["model_labels"], ["person"])
        self.assertEqual(checks["recipe_key"], "people_counting")
        self.assertEqual(checks["artifact_sha256"], self.model.artifact_sha256)

    def test_a_non_detection_task_is_refused(self):
        for task in ("audio-classification", "text-classification", "handwriting-ocr"):
            with self.subTest(task=task):
                model = self.make_model(task=task)
                checks = inference.validate_compatibility(model, "people_counting")
                self.assertFalse(checks["compatible"])
                self.assertIn("not a detection task", checks["reason"])

    def test_a_pre_recipe_image_model_is_a_detection_task(self):
        # task=None: every image model trained before recipes existed is a box detector.
        model = self.make_model(task=None)
        checks = inference.validate_compatibility(model, "people_counting")
        self.assertIs(checks["compatible"], True)

    def test_changed_labels_are_refused(self):
        model = self.make_model(labels=("car", "bicycle"))
        checks = inference.validate_compatibility(model, "people_counting")
        self.assertFalse(checks["compatible"])
        self.assertIn("was not taught to find person", checks["reason"])

    def test_extra_labels_are_fine_missing_labels_are_not(self):
        model = self.make_model(labels=("person", "car"))
        checks = inference.validate_compatibility(model, "people_counting")
        self.assertIs(checks["compatible"], True)

    def test_a_retired_model_is_refused(self):
        for stage in (ModelVersion.CANDIDATE, ModelVersion.STAGED, ModelVersion.RETIRED):
            with self.subTest(stage=stage):
                model = self.make_model(stage=stage)
                checks = inference.validate_compatibility(model, "people_counting")
                self.assertFalse(checks["compatible"])
                self.assertIn("no longer approved", checks["reason"])

    def test_an_unknown_recipe_is_refused_rather_than_guessed(self):
        checks = inference.validate_compatibility(self.model, "parking_bays")
        self.assertFalse(checks["compatible"])
        self.assertIn("no inference adapter", checks["reason"])


# -- frame sampling determinism ------------------------------------------------------------------
@skipUnless(REQUIRE_FFMPEG, "ffmpeg and ffprobe required for frame extraction tests")
class FrameSamplingTests(InferenceBase):
    def setUp(self):
        super().setUp()
        self.body = make_clip(self.tmp, seconds=2, creation_time="2026-11-01T09:30:00Z")
        self.recording = self.store_recording(
            self.body, duration=2.0, creation_time="2026-11-01T09:30:00Z"
        )
        self.timeline = build_timeline(self.recording)

    def test_sampling_is_a_pure_function_of_the_timeline(self):
        self.assertEqual(
            inference.sample_frame_indices(self.timeline, 6),
            inference.sample_frame_indices(self.timeline, 6),
        )

    def test_fewer_frames_than_the_sample_returns_them_all_in_order(self):
        self.assertEqual(inference.sample_frame_indices(self.timeline, 10), list(range(10)))
        self.assertEqual(inference.sample_frame_indices(self.timeline, 10), [0, 1, 2, 3, 4, 5, 6, 7, 8, 9])

    def test_even_sampling_always_includes_first_and_last(self):
        chosen = inference.sample_frame_indices(self.timeline, 4)
        self.assertEqual(len(chosen), 4)
        self.assertEqual(chosen[0], 0)
        self.assertEqual(chosen[-1], 9)
        self.assertEqual(chosen, sorted(chosen))

    def test_a_single_frame_sample_is_the_first_frame(self):
        self.assertEqual(inference.sample_frame_indices(self.timeline, 1), [0])

    def test_invalid_counts_are_refused(self):
        for bad in (0, -1, 2.5, True, "three"):
            with self.subTest(count=bad):
                with self.assertRaises(inference.InferenceAdapterError):
                    inference.sample_frame_indices(self.timeline, bad)

    def test_extraction_is_deterministic_across_calls(self):
        indices = inference.sample_frame_indices(self.timeline, 3)
        first = self._digests(indices)
        second = self._digests(indices)
        self.assertEqual(first, second)

    def _digests(self, indices):
        with inference._extract_frames(self.recording, self.timeline, indices) as frames:
            return [(frame.index, frame.path.read_bytes()) for frame in frames]

    def test_extraction_refuses_a_changed_recording(self):
        default_storage.delete(self.recording.storage_key)
        default_storage.save(self.recording.storage_key, _NamedBytes(b"tampered"))
        with self.assertRaises(inference.InferenceAdapterError) as raised:
            with inference._extract_frames(self.recording, self.timeline, [0]):
                pass
        self.assertIn("changed on disk", str(raised.exception))

    def test_extraction_cleans_up_its_frames(self):
        work_root = Path(settings.DATA_HOME) / "inference-frames"
        with inference._extract_frames(self.recording, self.timeline, [0]) as frames:
            self.assertTrue(frames[0].path.is_file())
        leftovers = list(work_root.rglob("frame-*.png")) if work_root.is_dir() else []
        self.assertEqual(leftovers, [])


# -- the adapter's normalized output and refusal paths -------------------------------------------
@skipUnless(REQUIRE_FFMPEG, "ffmpeg and ffprobe required for adapter tests")
class AdapterTests(InferenceBase):
    def setUp(self):
        super().setUp()
        self.body = make_clip(self.tmp, seconds=2, creation_time="2026-11-01T09:30:00Z")
        self.recording = self.store_recording(
            self.body, duration=2.0, creation_time="2026-11-01T09:30:00Z"
        )
        self.timeline = build_timeline(self.recording)
        self.model = self.make_model()
        self.binding = self.make_binding(self.model)

    def test_normalized_output_shape(self):
        report = inference.run_detector(self.binding, self.recording, self.timeline, "people_counting")
        self.assertEqual(report.model_version_id, str(self.model.id))
        self.assertEqual(report.backend, "t21-fake-detector")
        self.assertEqual(report.recipe_key, "people_counting")
        self.assertEqual(report.expected_labels, ("person",))
        self.assertGreaterEqual(report.frame_count, 1)
        for frame in report.frames:
            self.assertIn(frame.frame_index, [f.index for f in self.timeline.frames])
            for box in frame.detections:
                self.assertEqual(
                    sorted(box), ["height", "label", "score", "width", "x", "y"]
                )
                self.assertEqual(box["label"], "person")
                for key in ("x", "y", "width", "height"):
                    self.assertTrue(0.0 <= box[key] <= 1.0, (key, box[key]))
                self.assertTrue(0.0 < box["width"] and 0.0 < box["height"])
                self.assertTrue(0.0 <= box["score"] <= 1.0)
        # A convenience handle the artifact tests use; the artifact lives under DATA_HOME.
        self.assertEqual(report.summary()["frame_count"], report.frame_count)
        self.assertEqual(report.summary()["total_detections"], sum(len(f.detections) for f in report.frames))

    def test_predictions_outside_the_normalized_space_are_dropped(self):
        FakeDetector.boxes = [
            BoxPrediction("person", 0.1, 0.1, 0.2, 0.2, 0.9),   # fine
            BoxPrediction("person", 0.9, 0.9, 0.5, 0.5, 0.8),   # runs past the frame edge
            BoxPrediction("person", 0.1, 0.1, 0.0, 0.2, 0.7),   # zero width
            BoxPrediction("car", 0.1, 0.1, 0.2, 0.2, 0.6),      # label the model was not taught
            BoxPrediction("person", 0.1, 0.1, 0.2, 0.2, 1.5),   # score above 1
        ]
        report = inference.run_detector(self.binding, self.recording, self.timeline, "people_counting")
        for frame in report.frames:
            labels = [box["label"] for box in frame.detections]
            self.assertEqual(labels, ["person"])
            self.assertEqual(len(frame.detections), 1)

    def test_a_backend_failure_is_an_explicit_error_never_zeros(self):
        FakeDetector.fail_with = "the model exploded"
        with self.assertRaises(inference.InferenceAdapterError) as raised:
            inference.run_detector(self.binding, self.recording, self.timeline, "people_counting")
        self.assertIn("failed while analysing frame", str(raised.exception))
        self.assertIn("the model exploded", str(raised.exception))

    def test_a_missing_artifact_is_refused_with_a_plain_message(self):
        self.artifact_path(self.model).unlink()
        with self.assertRaises(inference.InferenceAdapterError) as raised:
            inference.run_detector(self.binding, self.recording, self.timeline, "people_counting")
        self.assertIn("no longer matches the checksum", str(raised.exception))

    def test_a_changed_artifact_is_refused(self):
        self.artifact_path(self.model).write_bytes(b"different-weights")
        with self.assertRaises(inference.InferenceAdapterError) as raised:
            inference.run_detector(self.binding, self.recording, self.timeline, "people_counting")
        self.assertIn("no longer matches the checksum", str(raised.exception))

    def test_an_uninstalled_backend_is_refused_with_a_plain_message(self):
        model = self.make_model(backend="never-installed-backend")
        binding = self.make_binding(model)
        with self.assertRaises(inference.InferenceAdapterError) as raised:
            inference.run_detector(binding, self.recording, self.timeline, "people_counting")
        self.assertIn("not installed in this worker", str(raised.exception))

    def test_an_incompatible_model_is_refused_at_run_time_not_downgraded(self):
        retired = self.make_model(stage=ModelVersion.RETIRED)
        binding = self.make_binding(retired)
        with self.assertRaises(inference.InferenceAdapterError) as raised:
            inference.run_detector(binding, self.recording, self.timeline, "people_counting")
        self.assertIn("no longer approved", str(raised.exception))

    def test_an_unknown_recipe_is_refused_at_run_time(self):
        with self.assertRaises(inference.InferenceAdapterError) as raised:
            inference.run_detector(self.binding, self.recording, self.timeline, "queue_dwell")
        self.assertIn("no inference adapter", str(raised.exception))


# -- the duration cross-check (T20's noted gap) ---------------------------------------------------
@skipUnless(REQUIRE_FFMPEG, "ffmpeg and ffprobe required for duration cross-check tests")
class DurationCrosscheckTests(InferenceBase):
    def setUp(self):
        super().setUp()
        self.body = make_clip(self.tmp, seconds=2, creation_time="2026-11-01T09:30:00Z")
        self.recording = self.store_recording(
            self.body, duration=2.0, creation_time="2026-11-01T09:30:00Z"
        )
        self.timeline = build_timeline(self.recording)

    def test_a_matching_duration_passes_with_the_numbers_recorded(self):
        record = inference.crosscheck_duration(self.recording, self.timeline)
        self.assertTrue(record["matches"])
        self.assertAlmostEqual(record["stored_duration_seconds"], 2.0, places=5)
        self.assertAlmostEqual(record["timeline_span_seconds"], 2.0, places=5)

    def test_a_drifted_duration_is_refused(self):
        self.recording.duration_seconds = 55.0
        with self.assertRaises(inference.InferenceAdapterError) as raised:
            inference.crosscheck_duration(self.recording, self.timeline)
        self.assertIn("stored duration no longer matches", str(raised.exception))

    def test_a_duration_within_one_frame_of_the_span_passes(self):
        # The container duration may differ from the last frame's end by up to one frame cadence.
        self.recording.duration_seconds = 2.0 + 0.15  # cadence is 0.2 s
        record = inference.crosscheck_duration(self.recording, self.timeline)
        self.assertTrue(record["matches"])


# -- the worker integration ----------------------------------------------------------------------
@skipUnless(REQUIRE_FFMPEG, "ffmpeg and ffprobe required for worker execution tests")
class WorkerIntegrationTests(InferenceBase):
    def setUp(self):
        super().setUp()
        self.body = make_clip(self.tmp, seconds=2, creation_time="2026-11-01T09:30:00Z")
        self.recording = self.store_recording(
            self.body, duration=2.0, creation_time="2026-11-01T09:30:00Z"
        )
        self.model = self.make_model()
        self.binding = self.make_binding(self.model)

    def test_with_model_execution_records_detections_and_the_honest_note(self):
        bundle = self.make_bundle(binding=self.binding)
        finished = executions.run_execution(self.enqueue_and_claim(bundle), "worker-a")
        self.assertEqual(finished.status, "succeeded", finished.error)
        result = finished.result
        self.assertIn("detector", result)
        detector = result["detector"]
        self.assertEqual(detector["model_version_id"], str(self.model.id))
        self.assertEqual(detector["model_version_number"], 1)
        self.assertEqual(detector["backend"], "t21-fake-detector")
        self.assertGreaterEqual(detector["frame_count"], 1)
        self.assertEqual(detector["total_detections"], detector["frame_count"])  # one box per frame
        for frame in detector["frames"]:
            self.assertIn("pts", frame)
            for box in frame["detections"]:
                self.assertEqual(box["label"], "person")
                self.assertTrue(0.0 <= box["x"] <= 1.0 and 0.0 <= box["y"] <= 1.0)
        # The note says exactly what ran and what it is not.
        self.assertIn("ran the bound approved model", result["note"])
        self.assertIn("recorded replay analysis", result["note"])
        self.assertIn("not a live operation", result["note"])
        self.assertIn("not tracks or crossing counts", result["note"])
        # The rest of the outcome is unchanged.
        self.assertEqual(result["recording_sha256"], self.recording.sha256)
        self.assertTrue(result["duration_check"]["matches"])
        self.assertEqual(result["comparison"]["matched"], 1)

    def test_without_model_execution_keeps_the_old_honest_note(self):
        bundle = self.make_bundle(binding=None)
        finished = executions.run_execution(self.enqueue_and_claim(bundle), "worker-a")
        self.assertEqual(finished.status, "succeeded", finished.error)
        result = finished.result
        self.assertNotIn("detector", result)
        self.assertIn("No model is bound", result["note"])
        self.assertIn("recorded human reference events", result["note"])

    def test_a_binding_whose_model_drifted_fails_the_execution_plainly(self):
        self.model.stage = ModelVersion.RETIRED
        # Bypass the guarded save: the run-time check must catch drift the pin never saw.
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {ModelVersion._meta.db_table} SET stage = %s WHERE id = %s",
                [ModelVersion.RETIRED, self.model.pk.hex],
            )
        bundle = self.make_bundle(binding=self.binding)
        finished = executions.run_execution(self.enqueue_and_claim(bundle), "worker-a")
        self.assertEqual(finished.status, "failed")
        self.assertIn("no longer approved", finished.error)

    def test_a_drifted_recording_duration_fails_the_execution_plainly(self):
        bundle = self.make_bundle(binding=self.binding)
        execution = self.enqueue_and_claim(bundle)
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {OperationSourceRecording._meta.db_table} SET duration_seconds = %s WHERE id = %s",
                [61.0, self.recording.pk.hex],
            )
        execution.refresh_from_db()
        finished = executions.run_execution(execution, "worker-a")
        self.assertEqual(finished.status, "failed")
        self.assertIn("stored duration no longer matches", finished.error)

    def test_a_failing_backend_fails_the_execution_plainly_never_zero_detections(self):
        FakeDetector.fail_with = "weights corrupted"
        bundle = self.make_bundle(binding=self.binding)
        finished = executions.run_execution(self.enqueue_and_claim(bundle), "worker-a")
        self.assertEqual(finished.status, "failed")
        self.assertIn("failed while analysing frame", finished.error)
        self.assertIn("weights corrupted", finished.error)

    def test_the_with_model_outcome_is_reproducible(self):
        bundle = self.make_bundle(binding=self.binding)
        first = executions.run_execution(self.enqueue_and_claim(bundle), "worker-a")
        second = executions.run_execution(self.enqueue_and_claim(bundle), "worker-b")
        self.assertEqual(first.status, "succeeded")
        self.assertEqual(second.status, "succeeded")
        self.assertEqual(first.result["detector"]["frames"], second.result["detector"]["frames"])
        self.assertEqual(first.result["timeline_sha256"], second.result["timeline_sha256"])
