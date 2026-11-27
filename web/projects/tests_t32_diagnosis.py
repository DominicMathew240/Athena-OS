"""T32: route replay test failures to a diagnosis.

Users distinguish source/view, geometry, rules and recognition mistakes.
Correct recognition must not push them into unnecessary training.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase, TestCase

from .models import (
    DatasetVersion,
    GeometryVersion,
    ModelVersion,
    OperationDraft,
    OperationExecution,
    OperationModelBinding,
    OperationReleaseBundle,
    Project,
    RecipeVersion,
    SourceViewVersion,
    TrainingRun,
)
from .operations import comparison, diagnosis, executions
from .operations.crossings import Crossing
from .operations.inference import AdapterReport, FrameDetections
from .source_time import Frame, FrameTimeline, Session
from .tests_t19_durable import DurableExecutionBase


class DiagnoseReplayMissesUnitTests(SimpleTestCase):
    """Pure diagnosis of comparison + coverage evidence into plain categories."""

    def test_perfect_match_does_not_recommend_training(self) -> None:
        report = comparison.compare_crossings(
            [Crossing(direction="negative_to_positive", time=1.0)],
            [Crossing(direction="negative_to_positive", time=1.05)],
            tolerance=0.5,
        ).summary(predicted_source="detector_crossing_episodes")
        result = diagnosis.diagnose_replay(
            comparison=report,
            gap_accounting={"full_frame_coverage": True, "gaps": [], "source_gap_count": 0},
            has_geometry=True,
            has_detector=True,
        )
        self.assertEqual(result["primary_category"], "none")
        self.assertFalse(result["recommend_training"])
        self.assertEqual(result["next_step"], "recognition_looks_correct")
        self.assertEqual(result["counts"]["matched"], 1)
        self.assertEqual(result["counts"]["missed"], 0)

    def test_miss_inside_source_gap_is_source_view(self) -> None:
        report = comparison.compare_crossings(
            [Crossing(direction="negative_to_positive", time=5.0)],
            [],
            tolerance=0.5,
        ).summary(predicted_source="detector_crossing_episodes")
        result = diagnosis.diagnose_replay(
            comparison=report,
            gap_accounting={
                "full_frame_coverage": False,
                "source_gap_count": 1,
                "gaps": [
                    {
                        "kind": "source_discontinuity",
                        "start_pts": 4.0,
                        "end_pts": 6.0,
                        "length_seconds": 2.0,
                    }
                ],
            },
            has_geometry=True,
            has_detector=True,
        )
        self.assertEqual(result["primary_category"], "source_view")
        self.assertFalse(result["recommend_training"])
        self.assertEqual(result["next_step"], "fix_source_or_recording")
        self.assertEqual(result["items"][0]["category"], "source_view")
        self.assertEqual(result["items"][0]["kind"], "missed")
        self.assertEqual(result["items"][0]["time"], 5.0)

    def test_miss_with_detections_but_no_crossing_is_geometry(self) -> None:
        report = comparison.compare_crossings(
            [Crossing(direction="negative_to_positive", time=2.0)],
            [],
            tolerance=0.5,
        ).summary(predicted_source="detector_crossing_episodes")
        result = diagnosis.diagnose_replay(
            comparison=report,
            gap_accounting={"full_frame_coverage": True, "gaps": [], "source_gap_count": 0},
            has_geometry=True,
            has_detector=True,
            detection_times=[1.9, 2.05, 2.1],
            track_cross_times=[],
        )
        self.assertEqual(result["primary_category"], "geometry")
        self.assertFalse(result["recommend_training"])
        self.assertEqual(result["next_step"], "adjust_geometry")

    def test_miss_without_detections_on_clean_coverage_is_recognition(self) -> None:
        report = comparison.compare_crossings(
            [Crossing(direction="negative_to_positive", time=2.0)],
            [],
            tolerance=0.5,
        ).summary(predicted_source="detector_crossing_episodes")
        result = diagnosis.diagnose_replay(
            comparison=report,
            gap_accounting={"full_frame_coverage": True, "gaps": [], "source_gap_count": 0},
            has_geometry=True,
            has_detector=True,
            detection_times=[0.1, 8.0],
            track_cross_times=[],
        )
        self.assertEqual(result["primary_category"], "recognition")
        self.assertTrue(result["recommend_training"])
        self.assertEqual(result["next_step"], "improve_recognition")

    def test_extra_near_opposite_direction_reference_is_rules(self) -> None:
        report = comparison.compare_crossings(
            [Crossing(direction="positive_to_negative", time=3.0)],
            [Crossing(direction="negative_to_positive", time=3.05)],
            tolerance=0.5,
        ).summary(predicted_source="detector_crossing_episodes")
        result = diagnosis.diagnose_replay(
            comparison=report,
            gap_accounting={"full_frame_coverage": True, "gaps": [], "source_gap_count": 0},
            has_geometry=True,
            has_detector=True,
            detection_times=[3.0],
        )
        self.assertEqual(result["primary_category"], "rules")
        self.assertFalse(result["recommend_training"])
        self.assertEqual(result["next_step"], "review_counting_rules")

    def test_missing_geometry_pin_is_geometry_not_recognition(self) -> None:
        report = comparison.compare_crossings(
            [Crossing(direction="negative_to_positive", time=1.0)],
            [],
            tolerance=0.5,
        ).summary(predicted_source="reference_placeholder")
        result = diagnosis.diagnose_replay(
            comparison=report,
            gap_accounting={"full_frame_coverage": True, "gaps": [], "source_gap_count": 0},
            has_geometry=False,
            has_detector=True,
            detection_times=[1.0],
        )
        self.assertEqual(result["primary_category"], "geometry")
        self.assertFalse(result["recommend_training"])
        self.assertEqual(result["next_step"], "adjust_geometry")

    def test_no_detector_run_is_not_training(self) -> None:
        report = comparison.compare_crossings(
            [Crossing(direction="negative_to_positive", time=1.0)],
            [Crossing(direction="negative_to_positive", time=1.0)],
            tolerance=0.5,
        ).summary(predicted_source="reference_placeholder")
        result = diagnosis.diagnose_replay(
            comparison=report,
            gap_accounting={"full_frame_coverage": False, "gaps": [], "source_gap_count": 0},
            has_geometry=True,
            has_detector=False,
        )
        self.assertEqual(result["primary_category"], "none")
        self.assertFalse(result["recommend_training"])
        self.assertEqual(result["next_step"], "bind_compatible_model")

    def test_parking_false_free_on_clean_view_is_recognition(self) -> None:
        result = diagnosis.diagnose_parking(
            parking={
                "false_free_count": 1,
                "false_free_cases": [
                    {"bay_id": "B12", "time": 4.0, "predicted": "free", "reference": "occupied"}
                ],
                "transition_metrics": {"missed_count": 0, "missed": []},
            },
            gap_accounting={"full_frame_coverage": True, "gaps": [], "source_gap_count": 0},
        )
        self.assertEqual(result["primary_category"], "recognition")
        self.assertTrue(result["recommend_training"])
        self.assertEqual(result["next_step"], "improve_recognition")

    def test_parking_false_free_inside_gap_is_source_view(self) -> None:
        result = diagnosis.diagnose_parking(
            parking={
                "false_free_count": 1,
                "false_free_cases": [
                    {"bay_id": "B12", "time": 5.0, "predicted": "free", "reference": "occupied"}
                ],
                "transition_metrics": {"missed_count": 0, "missed": []},
            },
            gap_accounting={
                "full_frame_coverage": False,
                "source_gap_count": 1,
                "gaps": [
                    {
                        "kind": "source_discontinuity",
                        "start_pts": 4.5,
                        "end_pts": 5.5,
                        "length_seconds": 1.0,
                    }
                ],
            },
        )
        self.assertEqual(result["primary_category"], "source_view")
        self.assertFalse(result["recommend_training"])


class DiagnoseReplayExecutionIntegrationTests(DurableExecutionBase):
    """Execution outcome carries a diagnosis block derived from comparison + gaps."""

    def test_succeeded_execution_includes_diagnosis_and_skips_training_when_matched(self) -> None:
        # Operations are site/source-scoped; detector models still live on a Project.
        project = Project.objects.create(
            name="T32 Detector", modality="image", task="Detect", labels=["person"]
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
            status=TrainingRun.SUCCEEDED,
            backend="t32-detector",
            config={},
            config_sha256="d" * 64,
            created_by=self.user,
        )
        artifact_rel = f"models/{project.id}/v1/model.bin"
        artifact_path = Path(settings.DATA_HOME) / artifact_rel
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_bytes = b"t32-artifact"
        artifact_path.write_bytes(artifact_bytes)
        self.addCleanup(artifact_path.unlink, missing_ok=True)
        model = ModelVersion.objects.create(
            project=project,
            version=1,
            training_run=run,
            backend="t32-detector",
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
            configuration={
                "dead_band": 0.02,
                "confirmations": 2,
                "max_gap": 1.0,
                "comparison_tolerance_seconds": 0.5,
            },
            configuration_sha256="3" * 64,
        )
        draft = OperationDraft.objects.create(
            created_by=self.user, operation=self.operation, name="T32 diagnosis"
        )
        snapshot = dict(self.bundle.snapshot)
        snapshot["reference_events"] = [{"direction": "negative_to_positive", "time": 0.25}]
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
        execution = self.enqueue_and_claim("worker-t32")

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
            backend="t32-detector",
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
            finished = executions.run_execution(execution, "worker-t32", heartbeat_seconds=60)

        self.assertEqual(finished.status, OperationExecution.SUCCEEDED, finished.error)
        self.assertIn("diagnosis", finished.result)
        diag = finished.result["diagnosis"]["comparison"]
        self.assertEqual(diag["primary_category"], "none")
        self.assertFalse(diag["recommend_training"])
        self.assertEqual(diag["next_step"], "recognition_looks_correct")
        self.assertGreaterEqual(finished.result["comparison"]["matched"], 1)
        self.assertEqual(finished.result["comparison"]["missed"], 0)
