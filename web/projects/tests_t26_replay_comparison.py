"""T26: labeled reference events and replay comparison.

Contracts under test:
* People can mark expected event time/direction on a recording draft (validated
  against recording duration and direction, cleared when line or recording changes).
* Replay comparison reports matched events, misses, extras, timing offsets
  (signed offset, absolute offset, mean/max timing summary), and per-direction
  breakdowns under a declared matching tolerance.
* When a worker execution runs with a pinned detector and counting line, the
  execution outcome compares committed human reference crossings against the
  actual detector crossing episodes (and ``compare_ledger_events`` reproduces
  the exact comparison directly from the committed ``OperationEvent`` rows).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase

from .models import (
    DatasetVersion,
    GeometryVersion,
    ModelVersion,
    OperationDraft,
    OperationEvent,
    OperationExecution,
    OperationModelBinding,
    OperationRecording,
    OperationReleaseBundle,
    Project,
    RecipeVersion,
    SourceViewVersion,
    TrainingRun,
)
from .operations import comparison, executions
from .operations.crossings import Crossing
from .operations.inference import AdapterReport, FrameDetections
from .source_time import Frame, FrameTimeline, Session
from .tests_t19_durable import DurableExecutionBase


class ReplayComparisonReportTests(TestCase):
    """Comparison summary reports matched pairs, misses, extras, timing offsets, and per-direction totals."""

    def test_comparison_summary_reports_matches_misses_extras_and_timing(self) -> None:
        reference = [
            Crossing(direction="negative_to_positive", time=1.00),
            Crossing(direction="negative_to_positive", time=3.00),
            Crossing(direction="positive_to_negative", time=4.50),
        ]
        predicted = [
            Crossing(direction="negative_to_positive", time=1.20),  # match (+0.20s)
            Crossing(direction="negative_to_positive", time=2.10),  # extra (outside 0.5s of 3.00)
            Crossing(direction="positive_to_negative", time=4.35),  # match (-0.15s)
        ]
        comp = comparison.compare_crossings(reference, predicted, tolerance=0.5)
        report = comp.summary(predicted_source="detector_crossing_episodes")

        self.assertEqual(report["tolerance_seconds"], 0.5)
        self.assertEqual(report["predicted_source"], "detector_crossing_episodes")
        self.assertEqual(report["matched"], 2)
        self.assertEqual(report["missed"], 1)
        self.assertEqual(report["extra"], 1)

        self.assertEqual(
            report["matches"],
            [
                {
                    "direction": "negative_to_positive",
                    "reference_time": 1.0,
                    "predicted_time": 1.2,
                    "offset_seconds": 0.2,
                    "abs_offset_seconds": 0.2,
                },
                {
                    "direction": "positive_to_negative",
                    "reference_time": 4.5,
                    "predicted_time": 4.35,
                    "offset_seconds": -0.15,
                    "abs_offset_seconds": 0.15,
                },
            ],
        )
        self.assertEqual(
            report["missed_events"],
            [{"direction": "negative_to_positive", "time": 3.0}],
        )
        self.assertEqual(
            report["extra_events"],
            [{"direction": "negative_to_positive", "time": 2.1}],
        )
        self.assertEqual(report["timing"]["count"], 2)
        self.assertAlmostEqual(report["timing"]["mean_offset_seconds"], 0.025, places=6)
        self.assertAlmostEqual(report["timing"]["mean_abs_offset_seconds"], 0.175, places=6)
        self.assertAlmostEqual(report["timing"]["max_abs_offset_seconds"], 0.2, places=6)

        pos = report["per_direction"]["negative_to_positive"]
        self.assertEqual(pos["reference_count"], 2)
        self.assertEqual(pos["predicted_count"], 2)
        self.assertEqual(pos["matched"], 1)
        self.assertEqual(pos["missed"], 1)
        self.assertEqual(pos["extra"], 1)
        self.assertAlmostEqual(pos["mean_abs_offset_seconds"], 0.2, places=6)

        neg = report["per_direction"]["positive_to_negative"]
        self.assertEqual(neg["reference_count"], 1)
        self.assertEqual(neg["predicted_count"], 1)
        self.assertEqual(neg["matched"], 1)
        self.assertEqual(neg["missed"], 0)
        self.assertEqual(neg["extra"], 0)
        self.assertAlmostEqual(neg["mean_abs_offset_seconds"], 0.15, places=6)


class ReferenceEventDraftAndWorkerComparisonTests(DurableExecutionBase):
    """Reference marking on drafts and end-to-end worker comparison against detector crossings."""

    def test_draft_api_persists_labeled_reference_events_and_validates_bounds(self) -> None:
        line = {
            "start": [0.2, 0.5],
            "end": [0.8, 0.5],
            "name": "Front door",
            "positive_label": "Entering",
            "negative_label": "Leaving",
        }
        draft_resp = self.client.post(
            "/api/operation-drafts",
            json.dumps({"name": "Entrance test", "line": line}),
            content_type="application/json",
        )
        self.assertEqual(draft_resp.status_code, 201, draft_resp.content)
        draft = draft_resp.json()["draft"]
        OperationRecording.objects.create(
            draft_id=draft["id"],
            storage_key="operation-recordings/t26.mp4",
            filename="t26.mp4",
            sha256="a" * 64,
            size_bytes=128,
            width=320,
            height=240,
            duration_seconds=5.0,
        )
        events = [
            {"time": 0.4, "direction": "negative_to_positive"},
            {"time": 2.8, "direction": "positive_to_negative"},
        ]
        updated = self.client.put(
            f"/api/operation-drafts/{draft['id']}",
            json.dumps(
                {
                    "name": "Entrance test",
                    "line": line,
                    "reference_events": events,
                    "revision": draft["revision"],
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(updated.status_code, 200, updated.content)
        self.assertEqual(updated.json()["draft"]["reference_events"], events)

        # Out-of-duration timestamp is refused.
        bad = self.client.put(
            f"/api/operation-drafts/{draft['id']}",
            json.dumps(
                {
                    "name": "Entrance test",
                    "line": line,
                    "reference_events": [{"time": 6.5, "direction": "negative_to_positive"}],
                    "revision": updated.json()["draft"]["revision"],
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(bad.status_code, 400)

    def test_worker_execution_compares_reference_events_against_detector_crossings_and_reconciles_with_ledger(
        self,
    ) -> None:
        project = Project.objects.create(
            name="T26 Detector", modality="image", task="Detect", labels=["person"]
        )
        dataset = DatasetVersion.objects.create(
            project=project,
            version=1,
            created_by=self.user,
            manifest={},
            manifest_sha256="c" * 64,
            label_names=["person"],
        )
        run = TrainingRun.objects.create(
            project=project,
            dataset_version=dataset,
            dataset_manifest_sha256="c" * 64,
            backend="t26-detector",
            config={},
            config_sha256="d" * 64,
            created_by=self.user,
        )
        artifact_rel = f"models/{project.id}/v1/model.bin"
        artifact_path = Path(settings.DATA_HOME) / artifact_rel
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_bytes = b"t26-artifact"
        artifact_path.write_bytes(artifact_bytes)
        self.addCleanup(artifact_path.unlink, missing_ok=True)
        model = ModelVersion.objects.create(
            project=project,
            version=1,
            training_run=run,
            backend="t26-detector",
            task=None,
            artifact_path=artifact_rel,
            artifact_sha256=hashlib.sha256(artifact_bytes).hexdigest(),
            artifact_size_bytes=len(artifact_bytes),
            label_names=["person"],
            stage=ModelVersion.APPROVED,
        )
        binding = OperationModelBinding.objects.create(
            operation=self.operation,
            version=1,
            created_by=self.user,
            model_version=model,
            compatibility={"compatible": True, "model_version_id": str(model.id)},
            compatibility_sha256="b" * 64,
        )
        source_view = SourceViewVersion.objects.create(
            source=self.source,
            version=1,
            created_by=self.user,
            configuration={},
            configuration_sha256="1" * 64,
        )
        geometry = GeometryVersion.objects.create(
            operation=self.operation,
            source_view_version=source_view,
            version=1,
            created_by=self.user,
            geometry={"line": {"start": [0.2, 0.5], "end": [0.8, 0.5]}},
            geometry_sha256="2" * 64,
        )
        recipe = RecipeVersion.objects.create(
            operation=self.operation,
            recipe_key="people_counting",
            version=1,
            created_by=self.user,
            configuration={"dead_band": 0.02, "confirmations": 2, "max_gap": 1.0, "comparison_tolerance_seconds": 0.2},
            configuration_sha256="3" * 64,
        )
        draft = OperationDraft.objects.create(
            created_by=self.user, operation=self.operation, name="T26 replay comparison"
        )
        snapshot = dict(self.bundle.snapshot)
        # Reference events: one at 0.30s (will match detector crossing at 0.25s within 0.2s tolerance),
        # and one at 0.85s (will be missed).
        snapshot["reference_events"] = [
            {"direction": "negative_to_positive", "time": 0.30},
            {"direction": "positive_to_negative", "time": 0.85},
        ]
        pins = dict(snapshot["component_pins"])
        pins["geometry"] = {"id": str(geometry.id), "version": 1, "sha256": geometry.geometry_sha256}
        pins["recipe"] = {
            "id": str(recipe.id),
            "version": 1,
            "recipe_key": "people_counting",
            "sha256": recipe.configuration_sha256,
            "configuration": recipe.configuration,
        }
        pins["model"] = {
            "id": str(binding.id),
            "version": 1,
            "model_version_id": str(model.id),
            "sha256": binding.compatibility_sha256,
        }
        snapshot["component_pins"] = pins
        encoded = json.dumps(
            snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        self.bundle = OperationReleaseBundle.objects.create(
            operation=self.operation,
            draft=draft,
            draft_revision=1,
            number=OperationReleaseBundle.objects.filter(operation=self.operation).count() + 1,
            created_by=self.user,
            snapshot=snapshot,
            snapshot_sha256=hashlib.sha256(encoded.encode()).hexdigest(),
        )
        execution = self.enqueue_and_claim("worker-a")

        centers_y = (0.35, 0.40, 0.45, 0.55, 0.60, 0.65)
        timeline = FrameTimeline(
            recording_sha256=self.recording.sha256,
            gap_threshold_seconds=1.0,
            captured_at=None,
            frames=[Frame(i, i / 10, 0.1, None, 1, True) for i in range(len(centers_y))],
            sessions=[Session(1, 0, 5, 6, 0.0, 0.5, None, None)],
        )
        detections = tuple(
            FrameDetections(
                frame_index=i,
                pts=i / 10,
                session=1,
                wall_clock=None,
                detections=(
                    {
                        "label": "person",
                        "x": 0.45,
                        "y": cy - 0.05,
                        "width": 0.1,
                        "height": 0.1,
                        "score": 0.9,
                    },
                ),
            )
            for i, cy in enumerate(centers_y)
        )
        report = AdapterReport(
            model_version_id=str(model.id),
            model_version_number=1,
            backend="t26-detector",
            artifact_sha256="4" * 64,
            recipe_key="people_counting",
            expected_labels=("person",),
            model_labels=("person",),
            frame_count=len(detections),
            frames=detections,
        )
        with (
            patch.object(executions, "_recording_digest", return_value=self.recording.sha256),
            patch.object(executions.source_time, "build_timeline", return_value=timeline),
            patch.object(executions.inference, "crosscheck_duration", return_value={"ok": True}),
            patch.object(executions.inference, "run_detector", return_value=report),
        ):
            finished = executions.run_execution(execution, "worker-a", heartbeat_seconds=60)

        self.assertEqual(finished.status, OperationExecution.SUCCEEDED, finished.error)
        comp = finished.result["comparison"]
        self.assertEqual(comp["predicted_source"], "detector_crossing_episodes")
        self.assertEqual(comp["tolerance_seconds"], 0.2)
        self.assertEqual(comp["matched"], 1)
        self.assertEqual(comp["missed"], 1)
        self.assertEqual(comp["extra"], 0)
        self.assertAlmostEqual(comp["matches"][0]["reference_time"], 0.30, places=5)
        self.assertAlmostEqual(comp["matches"][0]["predicted_time"], 0.25, places=5)
        self.assertAlmostEqual(comp["matches"][0]["offset_seconds"], -0.05, places=5)
        self.assertEqual(comp["missed_events"], [{"direction": "positive_to_negative", "time": 0.85}])

        # Direct comparison from the committed OperationEvent ledger rows matches the outcome report.
        ledger_rows = list(OperationEvent.objects.filter(execution=finished))
        from_ledger = comparison.compare_ledger_events(ledger_rows, tolerance=0.2).summary(
            predicted_source="detector_crossing_episodes"
        )
        self.assertEqual(from_ledger, comp)
