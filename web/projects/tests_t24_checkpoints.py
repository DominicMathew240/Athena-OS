"""T24 (bounded slice): durable stage progress checkpoints for recorded executions.

This is the foundation for T24 — not full T24, and not claimed safe or complete.
It proves a worker can persist a bounded progress checkpoint that survives
process death, only while it still owns a RUNNING non-expired lease on the
existing execution row. Checkpoints name stages that match *actual completed
effects*, and they explicitly refuse to claim full frame coverage or resumable
inference recovery. Stale/expired/wrong-owner leases cannot write checkpoints;
cancellation and failure retain whatever completed stages were already committed
without asserting unfinished work; legacy rows with an empty checkpoint remain
compatible. Per-run checkpoint eligibility is irreversible: AND-ed with every
live ``_beat`` and every checkpoint write result. Under strict T19/T23 lease
fencing, a lost lease aborts without SUCCEEDED (and without skipping required
ledger evidence); checkpoints alone never widen ownership.
This slice also covers an owner-scoped queued+running enqueue cap at the HTTP
boundary (including overlapping same-owner requests at the cap) and a bounded
abandoned-worker interruption summary on the detail API, including accounted
unprocessed stages after a successor ``process_queue`` reaps a worker-path
interrupt. That successor path is an in-process simulated interruption, not an
OS/SIGKILL or T56 redeployment drill.
No live source, no invented endpoint, no fake full-coverage result.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import subprocess
import sys
import tempfile
import threading
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import DEFAULT_DB_ALIAS, close_old_connections, connections
from django.db.models.query import QuerySet
from django.test import Client, TransactionTestCase, override_settings
from django.utils import timezone

from . import execution_views
from .models import (
    DatasetVersion,
    ModelVersion,
    OperationEvent,
    OperationExecution,
    OperationModelBinding,
    OperationReleaseBundle,
    Project,
    TrainingRun,
)
from .operations import executions
from .tests_t19_durable import REQUIRE_FFMPEG, DurableExecutionBase
from .training import register_backend, unregister_backend
from .training.base import Availability, BoxPrediction, TrainingBackend


class _T24FakeDetector(TrainingBackend):
    """Minimal detector stand-in so the real worker path can reach STAGE_DETECTOR_SAMPLED."""

    name = "t24-fake-detector"
    description = "T24 test double."
    license_note = "Test only."
    boxes: list[BoxPrediction] = [BoxPrediction("person", 0.1, 0.2, 0.3, 0.4, 0.9)]

    def check_available(self) -> Availability:
        return Availability(True, "ready")

    def default_config(self):
        return {}

    def validate_config(self, config):
        return {}

    def train(self, dataset_dir, config, progress_cb, cancel_cb):
        raise NotImplementedError

    def predict(self, artifact, image, thresholds):
        return [b for b in type(self).boxes if b.score >= thresholds.get("confidence", 0)]


class CheckpointLeaseGuardTests(DurableExecutionBase):
    """A checkpoint is only writable while this worker owns a live RUNNING lease."""

    def test_stale_or_expired_lease_cannot_checkpoint(self) -> None:
        execution = self.enqueue_and_claim("worker-a")
        # Wrong owner.
        self.assertFalse(
            executions.checkpoint_progress(
                execution, "worker-b", executions.STAGE_RECORDING_VERIFIED
            )
        )
        execution.refresh_from_db()
        self.assertEqual(execution.progress_checkpoint, {})

        # Expired lease (owner still named, but the lease window is past).
        past = timezone.now() - timedelta(seconds=1)
        OperationExecution.objects.filter(pk=execution.pk).update(lease_expires_at=past)
        execution.refresh_from_db()
        self.assertFalse(
            executions.checkpoint_progress(
                execution, "worker-a", executions.STAGE_RECORDING_VERIFIED
            )
        )
        execution.refresh_from_db()
        self.assertEqual(execution.progress_checkpoint, {})

        # Terminal status: never a checkpoint write after the finish guard.
        OperationExecution.objects.filter(pk=execution.pk).update(
            status=OperationExecution.FAILED,
            lease_owner="",
            lease_expires_at=None,
        )
        execution.refresh_from_db()
        self.assertFalse(
            executions.checkpoint_progress(
                execution, "worker-a", executions.STAGE_RECORDING_VERIFIED
            )
        )


class CheckpointPersistenceTests(DurableExecutionBase):
    """Checkpoints survive death/failure and never overclaim coverage or recovery."""

    def test_live_owner_writes_bounded_checkpoint_atomically(self) -> None:
        execution = self.enqueue_and_claim("worker-a")
        self.assertTrue(
            executions.checkpoint_progress(
                execution,
                "worker-a",
                executions.STAGE_RECORDING_VERIFIED,
                effects={"recording_sha256": self.recording.sha256},
            )
        )
        execution.refresh_from_db()
        checkpoint = execution.progress_checkpoint
        self.assertEqual(checkpoint["stage"], executions.STAGE_RECORDING_VERIFIED)
        self.assertEqual(checkpoint["completed_stages"], [executions.STAGE_RECORDING_VERIFIED])
        # No detector has run yet — sampled_analysis stays false.
        self.assertFalse(checkpoint["sampled_analysis"])
        self.assertFalse(checkpoint["full_frame_coverage"])
        self.assertEqual(checkpoint["recovery"], executions.RECOVERY_NOT_RESUMABLE)
        self.assertEqual(checkpoint["effects"]["recording_sha256"], self.recording.sha256)
        self.assertIn("updated_at", checkpoint)

    def test_restart_or_failure_retains_checkpoint_and_reports_incomplete(self) -> None:
        execution = self.enqueue_and_claim("worker-a")
        self.assertTrue(
            executions.checkpoint_progress(
                execution, "worker-a", executions.STAGE_TIMELINE_BUILT
            )
        )
        # Simulate worker death: lease expires far enough that recovery fails the row.
        past = timezone.now() - timedelta(seconds=3 * executions.LEASE_SECONDS)
        OperationExecution.objects.filter(pk=execution.pk).update(
            lease_expires_at=past, heartbeat_at=past
        )
        self.assertEqual(executions.fail_abandoned_executions(), 1)
        execution.refresh_from_db()
        self.assertEqual(execution.status, OperationExecution.FAILED)
        # The checkpoint survives the failure; it does not claim a complete run.
        checkpoint = execution.progress_checkpoint
        self.assertEqual(checkpoint["stage"], executions.STAGE_TIMELINE_BUILT)
        self.assertIn(executions.STAGE_TIMELINE_BUILT, checkpoint["completed_stages"])
        self.assertNotIn(executions.STAGE_REFERENCE_EVENTS_COMMITTED, checkpoint["completed_stages"])
        self.assertFalse(checkpoint["full_frame_coverage"])
        self.assertEqual(checkpoint["recovery"], executions.RECOVERY_NOT_RESUMABLE)
        self.assertNotEqual(execution.result.get("kind"), "reference_replay")

    def test_cancellation_does_not_assert_completed_work(self) -> None:
        execution = self.enqueue_and_claim("worker-a")
        self.assertTrue(
            executions.checkpoint_progress(
                execution, "worker-a", executions.STAGE_RECORDING_VERIFIED
            )
        )
        OperationExecution.objects.filter(pk=execution.pk).update(cancel_requested=True)
        finished = executions.run_execution(execution, "worker-a")
        self.assertEqual(finished.status, OperationExecution.CANCELLED)
        checkpoint = finished.progress_checkpoint
        # At most the stages that had already completed before cancel are present;
        # cancellation never upgrades the checkpoint to a finished-work claim.
        self.assertEqual(checkpoint.get("stage"), executions.STAGE_RECORDING_VERIFIED)
        self.assertNotIn(
            executions.STAGE_REFERENCE_EVENTS_COMMITTED,
            checkpoint.get("completed_stages", []),
        )
        self.assertFalse(checkpoint.get("full_frame_coverage", True))
        self.assertEqual(finished.result, {})

    def test_checkpoint_survives_process_death_reload(self) -> None:
        execution = self.enqueue_and_claim("worker-a")
        self.assertTrue(
            executions.checkpoint_progress(
                execution, "worker-a", executions.STAGE_RECORDING_VERIFIED
            )
        )
        # A new process would re-load the row from the database.
        reloaded = OperationExecution.objects.get(pk=execution.pk)
        self.assertEqual(
            reloaded.progress_checkpoint["stage"], executions.STAGE_RECORDING_VERIFIED
        )
        self.assertEqual(
            reloaded.progress_checkpoint["recovery"], executions.RECOVERY_NOT_RESUMABLE
        )

    def test_rejects_unknown_stage_and_oversized_effects(self) -> None:
        execution = self.enqueue_and_claim("worker-a")
        with self.assertRaises(executions.CheckpointError):
            executions.checkpoint_progress(execution, "worker-a", "invented_stage")
        huge = {"blob": "x" * (executions.MAX_CHECKPOINT_BYTES + 64)}
        with self.assertRaises(executions.CheckpointError):
            executions.checkpoint_progress(
                execution,
                "worker-a",
                executions.STAGE_RECORDING_VERIFIED,
                effects=huge,
            )
        execution.refresh_from_db()
        self.assertEqual(execution.progress_checkpoint, {})


class CheckpointTimelineCoverageValidationTests(DurableExecutionBase):
    """checkpoint_progress accepts only the bounded scalar coverage record."""

    @staticmethod
    def _scalar_coverage(**overrides) -> dict:
        record = {
            "gap_threshold_seconds": 1.0,
            "gap_count": 1,
            "jitter_count": 0,
            "late_frame_count": 0,
            "session_count": 2,
            "frame_count": 9,
            "timeline_sha256": "ab" * 32,
        }
        record.update(overrides)
        return record

    def test_rejects_leaked_gaps_array_and_writes_nothing(self) -> None:
        execution = self.enqueue_and_claim("worker-a")
        valid = self._scalar_coverage()
        self.assertTrue(
            executions.checkpoint_progress(
                execution,
                "worker-a",
                executions.STAGE_TIMELINE_BUILT,
                timeline_coverage=valid,
            )
        )
        execution.refresh_from_db()
        before = json.dumps(
            execution.progress_checkpoint, sort_keys=True, separators=(",", ":")
        )
        leaked = self._scalar_coverage(
            gaps=[{"after_frame": 4, "start_pts": 1.0, "end_pts": 2.0, "length_seconds": 1.0}],
            sessions=[{"number": 1, "frame_count": 4}],
            missing_frame_count=3,
        )
        with self.assertRaises(executions.CheckpointError):
            executions.checkpoint_progress(
                execution,
                "worker-a",
                executions.STAGE_TIMELINE_BUILT,
                timeline_coverage=leaked,
            )
        execution.refresh_from_db()
        self.assertEqual(
            json.dumps(execution.progress_checkpoint, sort_keys=True, separators=(",", ":")),
            before,
        )
        stored = execution.progress_checkpoint["timeline_coverage"]
        self.assertNotIn("gaps", stored)
        self.assertNotIn("sessions", stored)
        self.assertNotIn("missing_frame_count", stored)
        with self.assertRaises(executions.CheckpointError):
            executions.checkpoint_progress(
                execution,
                "worker-a",
                executions.STAGE_REFERENCE_EVENTS_COMMITTED,
                timeline_coverage={},
            )
        execution.refresh_from_db()
        self.assertEqual(
            json.dumps(execution.progress_checkpoint, sort_keys=True, separators=(",", ":")),
            before,
        )

    def test_non_finite_or_non_json_coverage_raises_checkpoint_error_not_raw_value_error(
        self,
    ) -> None:
        execution = self.enqueue_and_claim("worker-a")
        records = (
            self._scalar_coverage(gap_threshold_seconds=float("nan")),
            self._scalar_coverage(gap_threshold_seconds=float("inf")),
            self._scalar_coverage(gap_threshold_seconds=10**1000),
            self._scalar_coverage(frame_count={1, 2}),
        )
        for record in records:
            try:
                executions.checkpoint_progress(
                    execution,
                    "worker-a",
                    executions.STAGE_TIMELINE_BUILT,
                    timeline_coverage=record,
                )
            except executions.CheckpointError:
                pass
            except ValueError as error:
                self.fail(f"raw ValueError, not CheckpointError: {error!r}")
            except OverflowError as error:
                self.fail(f"raw OverflowError, not CheckpointError: {error!r}")
            else:
                self.fail("expected CheckpointError")
        execution.refresh_from_db()
        self.assertEqual(execution.progress_checkpoint, {})

    def test_rejects_invalid_prior_coverage_instead_of_copying_on_later_stage(self) -> None:
        execution = self.enqueue_and_claim("worker-a")
        self.assertTrue(
            executions.checkpoint_progress(
                execution, "worker-a", executions.STAGE_TIMELINE_BUILT
            )
        )
        execution.refresh_from_db()
        poisoned = dict(execution.progress_checkpoint)
        poisoned["timeline_coverage"] = self._scalar_coverage(
            gaps=[{"after_frame": 4, "start_pts": 1.0, "end_pts": 2.0, "length_seconds": 1.0}],
        )
        OperationExecution.objects.filter(pk=execution.pk).update(progress_checkpoint=poisoned)
        execution.refresh_from_db()
        before = json.dumps(
            execution.progress_checkpoint, sort_keys=True, separators=(",", ":")
        )
        self.assertIn("gaps", execution.progress_checkpoint["timeline_coverage"])
        with self.assertRaises(executions.CheckpointError):
            executions.checkpoint_progress(
                execution, "worker-a", executions.STAGE_REFERENCE_EVENTS_COMMITTED
            )
        execution.refresh_from_db()
        self.assertEqual(
            json.dumps(execution.progress_checkpoint, sort_keys=True, separators=(",", ":")),
            before,
        )
        self.assertEqual(execution.progress_checkpoint["stage"], executions.STAGE_TIMELINE_BUILT)
        self.assertIn("gaps", execution.progress_checkpoint["timeline_coverage"])


class CheckpointCompatibilityAndApiTests(DurableExecutionBase):
    """Legacy empty rows stay safe; the existing detail endpoint serializes truth."""

    def test_legacy_rows_default_safely(self) -> None:
        data = self.enqueue()
        row = OperationExecution.objects.get(pk=data["id"])
        self.assertEqual(row.progress_checkpoint, {})
        serialized = executions.serialize_progress_checkpoint(row)
        self.assertIsNone(serialized["stage"])
        self.assertEqual(serialized["completed_stages"], [])
        self.assertFalse(serialized["sampled_analysis"])
        self.assertFalse(serialized["full_frame_coverage"])
        self.assertEqual(serialized["recovery"], executions.RECOVERY_UNKNOWN)

    def test_endpoint_serializes_actual_checkpoint_state(self) -> None:
        execution = self.enqueue_and_claim("worker-a")
        self.assertTrue(
            executions.checkpoint_progress(
                execution,
                "worker-a",
                executions.STAGE_TIMELINE_BUILT,
                effects={"frame_count": 5},
            )
        )
        response = self.client.get(f"/api/operation-executions/{execution.pk}")
        self.assertEqual(response.status_code, 200)
        payload = response.json()["execution"]["progress_checkpoint"]
        self.assertEqual(payload["stage"], executions.STAGE_TIMELINE_BUILT)
        self.assertEqual(payload["completed_stages"], [executions.STAGE_TIMELINE_BUILT])
        self.assertFalse(payload["sampled_analysis"])
        self.assertFalse(payload["full_frame_coverage"])
        self.assertEqual(payload["recovery"], executions.RECOVERY_NOT_RESUMABLE)
        self.assertEqual(payload["effects"]["frame_count"], 5)

    def test_queued_list_serializes_empty_checkpoint_defaults(self) -> None:
        self.enqueue()
        listing = self.client.get(f"/api/operation-release-bundles/{self.bundle.id}/executions")
        self.assertEqual(listing.status_code, 200)
        checkpoint = listing.json()["executions"][0]["progress_checkpoint"]
        self.assertIsNone(checkpoint["stage"])
        self.assertEqual(checkpoint["completed_stages"], [])
        self.assertFalse(checkpoint["sampled_analysis"])
        self.assertFalse(checkpoint["full_frame_coverage"])
        self.assertEqual(checkpoint["recovery"], executions.RECOVERY_UNKNOWN)


class CheckpointWorkerIntegrationTests(DurableExecutionBase):
    """The recorded worker path checkpoints stages that match completed effects."""

    @skipUnless(REQUIRE_FFMPEG, "running an execution needs a decodable recording (ffmpeg)")
    def test_successful_run_checkpoints_actual_stages_without_full_coverage(self) -> None:
        execution = self.enqueue_and_claim("worker-a")
        finished = executions.run_execution(execution, "worker-a")
        self.assertEqual(finished.status, OperationExecution.SUCCEEDED, finished.error)
        checkpoint = finished.progress_checkpoint
        for stage in (
            executions.STAGE_RECORDING_VERIFIED,
            executions.STAGE_TIMELINE_BUILT,
            executions.STAGE_REFERENCE_EVENTS_COMMITTED,
        ):
            self.assertIn(stage, checkpoint["completed_stages"])
        self.assertEqual(checkpoint["stage"], executions.STAGE_REFERENCE_EVENTS_COMMITTED)
        self.assertNotIn(executions.STAGE_DETECTOR_SAMPLED, checkpoint["completed_stages"])
        self.assertFalse(checkpoint["sampled_analysis"])
        self.assertFalse(checkpoint["full_frame_coverage"])
        self.assertEqual(checkpoint["recovery"], executions.RECOVERY_NOT_RESUMABLE)
        # The durable outcome still says what the run was; the checkpoint is not a
        # substitute for the result and never pretends to be full footage analysis.
        self.assertEqual(finished.result["kind"], "reference_replay")
        self.assertIn("recorded human reference events", finished.result["note"].lower())
        self.assertNotIn("live operation", finished.result["note"].lower())
        # Later-stage write (reference_events_committed) must keep the timeline_built
        # coverage record; it is counts only, never missing-frame invention.
        coverage = checkpoint["timeline_coverage"]
        self.assertIn("gap_count", coverage)
        self.assertIn("session_count", coverage)
        self.assertIn("frame_count", coverage)
        self.assertIn("timeline_sha256", coverage)
        self.assertNotIn("gaps", coverage)
        self.assertNotIn("sessions", coverage)
        self.assertNotIn("missing_frame_count", coverage)
        self.assertNotIn("missing_frame_count", checkpoint)
        self.assertEqual(coverage["timeline_sha256"], finished.result["timeline_sha256"])

    @skipUnless(REQUIRE_FFMPEG, "running an execution needs a decodable recording (ffmpeg)")
    def test_no_binding_worker_keeps_sampled_analysis_false(self) -> None:
        """Real worker path with no model pin: stages complete, detector never ran."""
        execution = self.enqueue_and_claim("worker-a")
        finished = executions.run_execution(execution, "worker-a")
        self.assertEqual(finished.status, OperationExecution.SUCCEEDED, finished.error)
        checkpoint = finished.progress_checkpoint
        self.assertEqual(checkpoint["stage"], executions.STAGE_REFERENCE_EVENTS_COMMITTED)
        self.assertNotIn(executions.STAGE_DETECTOR_SAMPLED, checkpoint["completed_stages"])
        self.assertFalse(checkpoint["sampled_analysis"])
        self.assertFalse(checkpoint["full_frame_coverage"])
        self.assertEqual(checkpoint["recovery"], executions.RECOVERY_NOT_RESUMABLE)
        self.assertNotIn("detector", finished.result)

    @skipUnless(REQUIRE_FFMPEG, "running an execution needs a decodable recording (ffmpeg)")
    def test_detector_stage_sets_sampled_analysis_only_after_detector_completes(self) -> None:
        """Real worker path with a bound detector: true only once STAGE_DETECTOR_SAMPLED lands."""
        register_backend(_T24FakeDetector)
        self.addCleanup(lambda: unregister_backend("t24-fake-detector"))
        binding = self._pin_fake_detector()
        execution = self.enqueue_and_claim("worker-a")
        seen: list[dict] = []

        real_checkpoint = executions.checkpoint_progress

        def watch(execution_row, owner_key, stage, *, effects=None, **kwargs):
            ok = real_checkpoint(execution_row, owner_key, stage, effects=effects, **kwargs)
            execution_row.refresh_from_db()
            cp = execution_row.progress_checkpoint or {}
            seen.append(
                {
                    "stage": stage,
                    "ok": ok,
                    "sampled_analysis": cp.get("sampled_analysis"),
                    "completed_stages": list(cp.get("completed_stages") or []),
                }
            )
            return ok

        with patch.object(executions, "checkpoint_progress", side_effect=watch):
            finished = executions.run_execution(execution, "worker-a")
        self.assertEqual(finished.status, OperationExecution.SUCCEEDED, finished.error)
        self.assertIsNotNone(binding)
        # Before the detector stage, sampled_analysis must stay false.
        pre = [row for row in seen if row["stage"] != executions.STAGE_DETECTOR_SAMPLED]
        for row in pre:
            if executions.STAGE_DETECTOR_SAMPLED not in row["completed_stages"]:
                self.assertFalse(row["sampled_analysis"], row)
        detector_rows = [row for row in seen if row["stage"] == executions.STAGE_DETECTOR_SAMPLED]
        self.assertEqual(len(detector_rows), 1)
        self.assertTrue(detector_rows[0]["ok"])
        self.assertTrue(detector_rows[0]["sampled_analysis"])
        checkpoint = finished.progress_checkpoint
        self.assertIn(executions.STAGE_DETECTOR_SAMPLED, checkpoint["completed_stages"])
        self.assertTrue(checkpoint["sampled_analysis"])
        self.assertFalse(checkpoint["full_frame_coverage"])
        self.assertEqual(checkpoint["recovery"], executions.RECOVERY_NOT_RESUMABLE)
        self.assertIn("detector", finished.result)

    def _pin_fake_detector(self) -> OperationModelBinding:
        """Create a new release bundle that pins an approved fake detector (append-only)."""
        from .models import OperationDraft

        project = Project.objects.create(name="T24 Counting", modality="image", task="Detect", labels=["person"])
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
            backend="t24-fake-detector",
            config={},
            config_sha256="d" * 64,
            created_by=self.user,
        )
        artifact_rel = f"models/{project.id}/v1/model.bin"
        artifact_path = Path(settings.DATA_HOME) / artifact_rel
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_bytes = b"t24-fake-artifact"
        artifact_path.write_bytes(artifact_bytes)
        self.addCleanup(artifact_path.unlink, missing_ok=True)
        model = ModelVersion.objects.create(
            project=project,
            version=1,
            training_run=run,
            backend="t24-fake-detector",
            task=None,  # pre-recipe image detector (DETECTION_TASKS)
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
        draft = OperationDraft.objects.create(
            created_by=self.user, operation=self.operation, name="Gate count detector"
        )
        snapshot = dict(self.bundle.snapshot)
        pins = dict(snapshot.get("component_pins") or {})
        pins["model"] = {
            "id": str(binding.id),
            "version": binding.version,
            "model_version_id": str(model.id),
            "sha256": "4" * 64,
        }
        snapshot["component_pins"] = pins
        encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        next_number = (
            OperationReleaseBundle.objects.filter(operation=self.operation).count() + 1
        )
        self.bundle = OperationReleaseBundle.objects.create(
            operation=self.operation,
            draft=draft,
            draft_revision=1,
            number=next_number,
            created_by=self.user,
            snapshot=snapshot,
            snapshot_sha256=hashlib.sha256(encoded.encode()).hexdigest(),
        )
        return binding

    def test_mid_run_failure_keeps_last_honest_checkpoint(self) -> None:
        execution = self.enqueue_and_claim("worker-a")

        def boom(*_args, **_kwargs):
            # The recording was already verified and checkpointed before this call.
            raise RuntimeError("forced mid-run failure")

        with patch.object(executions.source_time, "build_timeline", side_effect=boom):
            finished = executions.run_execution(execution, "worker-a")
        self.assertEqual(finished.status, OperationExecution.FAILED)
        self.assertEqual(finished.error, "forced mid-run failure")
        checkpoint = finished.progress_checkpoint
        self.assertEqual(checkpoint["stage"], executions.STAGE_RECORDING_VERIFIED)
        self.assertEqual(checkpoint["completed_stages"], [executions.STAGE_RECORDING_VERIFIED])
        self.assertFalse(checkpoint["sampled_analysis"])
        self.assertFalse(checkpoint["full_frame_coverage"])
        self.assertEqual(checkpoint["recovery"], executions.RECOVERY_NOT_RESUMABLE)

    @skipUnless(REQUIRE_FFMPEG, "running an execution needs a decodable recording (ffmpeg)")
    def test_mid_run_expiry_then_renewal_blocks_subsequent_checkpoints(self) -> None:
        """Expire after a live checkpoint; strict policy refuses resurrection and aborts.

        Opening lease is fresh so early stages may commit. Expiring before a mid-run
        ``_beat`` must not renew (strict fencing). The worker aborts without SUCCEEDED
        and without inserting reference ledger events; early checkpoints may remain.
        """
        execution = self.enqueue_and_claim("worker-a")
        real_beat = executions._beat
        beat_calls = {"n": 0}

        def expire_before_mid_run_beat(execution_row, owner_key, lease_seconds=executions.LEASE_SECONDS):
            beat_calls["n"] += 1
            if beat_calls["n"] >= 2:
                past = timezone.now() - timedelta(seconds=1)
                OperationExecution.objects.filter(pk=execution_row.pk).update(lease_expires_at=past)
            return real_beat(execution_row, owner_key, lease_seconds)

        with patch.object(executions, "_beat", side_effect=expire_before_mid_run_beat):
            finished = executions.run_execution(execution, "worker-a")
        # Strict: lost lease aborts without a terminal success result.
        self.assertEqual(finished.status, OperationExecution.RUNNING)
        self.assertEqual(finished.result, {})
        self.assertEqual(OperationEvent.objects.filter(execution=finished).count(), 0)
        checkpoint = finished.progress_checkpoint
        completed = checkpoint.get("completed_stages") or []
        # Stages committed while the lease was still live may remain.
        self.assertIn(executions.STAGE_RECORDING_VERIFIED, completed)
        self.assertIn(executions.STAGE_TIMELINE_BUILT, completed)
        # After expiry, later stages must not appear.
        self.assertNotIn(executions.STAGE_REFERENCE_EVENTS_COMMITTED, completed)
        self.assertNotIn(executions.STAGE_DETECTOR_SAMPLED, completed)
        self.assertNotIn(executions.STAGE_TRACKS_ASSOCIATED, completed)
        self.assertEqual(checkpoint.get("stage"), executions.STAGE_TIMELINE_BUILT)
        self.assertFalse(checkpoint.get("full_frame_coverage", True))
        self.assertEqual(checkpoint.get("recovery"), executions.RECOVERY_NOT_RESUMABLE)

    @skipUnless(REQUIRE_FFMPEG, "running an execution needs a decodable recording (ffmpeg)")
    def test_failed_intermediate_checkpoint_blocks_later_stages_no_gaps(self) -> None:
        """A False intermediate checkpoint write must freeze eligibility; no stage gaps.

        Lease stays valid so mid-run ``_beat`` keeps returning True — only the failed
        write clears eligibility. A later stage must not land without the refused one.
        """
        execution = self.enqueue_and_claim("worker-a")
        real_checkpoint = executions.checkpoint_progress

        def refuse_timeline(execution_row, owner_key, stage, *, effects=None, **kwargs):
            if stage == executions.STAGE_TIMELINE_BUILT:
                return False
            return real_checkpoint(execution_row, owner_key, stage, effects=effects, **kwargs)

        with patch.object(executions, "checkpoint_progress", side_effect=refuse_timeline):
            finished = executions.run_execution(execution, "worker-a")
        self.assertEqual(finished.status, OperationExecution.SUCCEEDED, finished.error)
        checkpoint = finished.progress_checkpoint
        completed = checkpoint.get("completed_stages") or []
        # Honest prefix only: refused timeline stage is absent, and nothing after it.
        self.assertEqual(completed, [executions.STAGE_RECORDING_VERIFIED])
        self.assertEqual(checkpoint.get("stage"), executions.STAGE_RECORDING_VERIFIED)
        self.assertNotIn(executions.STAGE_TIMELINE_BUILT, completed)
        self.assertNotIn(executions.STAGE_REFERENCE_EVENTS_COMMITTED, completed)
        self.assertFalse(checkpoint.get("sampled_analysis", True))
        self.assertFalse(checkpoint.get("full_frame_coverage", True))
        self.assertEqual(checkpoint.get("recovery"), executions.RECOVERY_NOT_RESUMABLE)

    def test_run_execution_with_already_expired_lease_writes_no_checkpoint(self) -> None:
        """Lease already expired before run_execution: strict beat refuses resurrection.

        Direct checkpoint_progress refuses an expired lease. run_execution must not
        launder that refusal by beating first and writing stage evidence. Under
        strict fencing the worker aborts without a terminal result.
        """
        execution = self.enqueue_and_claim("worker-a")
        past = timezone.now() - timedelta(seconds=1)
        OperationExecution.objects.filter(pk=execution.pk).update(lease_expires_at=past)
        execution.refresh_from_db()
        self.assertLess(execution.lease_expires_at, timezone.now())
        self.assertFalse(
            executions.checkpoint_progress(
                execution, "worker-a", executions.STAGE_RECORDING_VERIFIED
            )
        )

        def boom(*_args, **_kwargs):
            raise RuntimeError("forced after digest")

        with patch.object(executions.source_time, "build_timeline", side_effect=boom):
            finished = executions.run_execution(execution, "worker-a")
        # Strict: opening beat fails; never reaches timeline; no terminal overwrite.
        self.assertEqual(finished.status, OperationExecution.RUNNING)
        self.assertEqual(finished.error, "")
        self.assertEqual(finished.result, {})
        self.assertEqual(finished.progress_checkpoint, {})

    @skipUnless(REQUIRE_FFMPEG, "running an execution needs a decodable recording (ffmpeg)")
    def test_run_execution_expired_lease_full_run_still_has_no_checkpoint(self) -> None:
        """Full worker path with a pre-expired lease: abort without SUCCEEDED or ledger."""
        execution = self.enqueue_and_claim("worker-a")
        past = timezone.now() - timedelta(seconds=1)
        OperationExecution.objects.filter(pk=execution.pk).update(lease_expires_at=past)
        finished = executions.run_execution(execution, "worker-a")
        self.assertEqual(finished.status, OperationExecution.RUNNING)
        self.assertEqual(finished.progress_checkpoint, {})
        self.assertEqual(finished.result, {})
        self.assertEqual(OperationEvent.objects.filter(execution=finished).count(), 0)

    def test_run_execution_competing_ownership_writes_no_checkpoint(self) -> None:
        """Wrong owner: no checkpoint, no ledger, no terminal steal — real owner stays RUNNING."""
        execution = self.enqueue_and_claim("worker-a")
        future = timezone.now() + timedelta(seconds=executions.LEASE_SECONDS)
        OperationExecution.objects.filter(pk=execution.pk).update(
            lease_owner="worker-b",
            lease_expires_at=future,
            heartbeat_at=timezone.now(),
        )
        with patch.object(
            executions, "_recording_digest", side_effect=RuntimeError("forced wrong-owner path")
        ):
            finished = executions.run_execution(execution, "worker-a")
        # Checkpoint ownership holds: nothing was written under the wrong key.
        self.assertEqual(finished.progress_checkpoint, {})
        # Fencing: wrong owner cannot finish or clear the real owner's lease.
        self.assertEqual(finished.status, OperationExecution.RUNNING)
        self.assertEqual(finished.lease_owner, "worker-b")
        self.assertEqual(finished.error, "")
        self.assertEqual(finished.result, {})
        self.assertEqual(OperationEvent.objects.filter(execution=finished).count(), 0)

    def test_run_execution_after_abandonment_no_checkpoint_no_stale_terminal_overwrite(self) -> None:
        """Recovery already failed the row: a late worker must not checkpoint or overwrite."""
        execution = self.enqueue_and_claim("worker-a")
        past = timezone.now() - timedelta(seconds=3 * executions.LEASE_SECONDS)
        OperationExecution.objects.filter(pk=execution.pk).update(
            lease_expires_at=past, heartbeat_at=past
        )
        self.assertEqual(executions.fail_abandoned_executions(), 1)
        execution.refresh_from_db()
        self.assertEqual(execution.status, OperationExecution.FAILED)
        abandoned_error = execution.error
        with patch.object(executions, "_recording_digest", return_value=self.recording.sha256):
            finished = executions.run_execution(execution, "worker-a")
        self.assertEqual(finished.status, OperationExecution.FAILED)
        self.assertEqual(finished.error, abandoned_error)
        self.assertEqual(finished.progress_checkpoint, {})
        self.assertEqual(finished.result, {})


class ExecutionQueueBoundTests(DurableExecutionBase):
    """Owner-scoped queued+running cap at the real bundle enqueue HTTP boundary."""

    def test_enqueue_at_cap_returns_429_and_creates_no_row(self) -> None:
        url = f"/api/operation-release-bundles/{self.bundle.id}/executions"
        body = json.dumps({"recording_id": str(self.recording.id)})
        with override_settings(TRAINING={"OPERATION_EXECUTION_QUEUE_LIMIT": 1}):
            first = self.client.post(url, body, content_type="application/json")
            self.assertEqual(first.status_code, 202, first.content)
            second = self.client.post(url, body, content_type="application/json")
        self.assertEqual(second.status_code, 429, second.content)
        self.assertIn("queued or running", second.json()["error"].lower())
        self.assertNotIn("full coverage", second.json()["error"].lower())
        self.assertEqual(
            OperationExecution.objects.filter(created_by=self.user).count(), 1
        )
        self.assertFalse(
            OperationExecution.objects.filter(created_by=self.user, status=OperationExecution.QUEUED)
            .exclude(pk=first.json()["execution"]["id"])
            .exists()
        )

    def test_enqueue_below_cap_succeeds(self) -> None:
        url = f"/api/operation-release-bundles/{self.bundle.id}/executions"
        body = json.dumps({"recording_id": str(self.recording.id)})
        with override_settings(TRAINING={"OPERATION_EXECUTION_QUEUE_LIMIT": 2}):
            first = self.client.post(url, body, content_type="application/json")
            second = self.client.post(url, body, content_type="application/json")
        self.assertEqual(first.status_code, 202, first.content)
        self.assertEqual(second.status_code, 202, second.content)
        self.assertEqual(first.json()["execution"]["status"], "queued")
        self.assertEqual(second.json()["execution"]["status"], "queued")
        self.assertNotEqual(first.json()["execution"]["id"], second.json()["execution"]["id"])
        self.assertEqual(
            OperationExecution.objects.filter(
                created_by=self.user,
                status__in=(OperationExecution.QUEUED, OperationExecution.RUNNING),
            ).count(),
            2,
        )

    def test_terminal_executions_do_not_count_toward_cap(self) -> None:
        url = f"/api/operation-release-bundles/{self.bundle.id}/executions"
        body = json.dumps({"recording_id": str(self.recording.id)})
        with override_settings(TRAINING={"OPERATION_EXECUTION_QUEUE_LIMIT": 1}):
            for status in (
                OperationExecution.SUCCEEDED,
                OperationExecution.FAILED,
                OperationExecution.CANCELLED,
            ):
                prior = self.client.post(url, body, content_type="application/json")
                self.assertEqual(prior.status_code, 202, prior.content)
                OperationExecution.objects.filter(pk=prior.json()["execution"]["id"]).update(status=status)
                nxt = self.client.post(url, body, content_type="application/json")
                self.assertEqual(nxt.status_code, 202, nxt.content)
                OperationExecution.objects.filter(pk=nxt.json()["execution"]["id"]).update(status=status)
        self.assertEqual(
            OperationExecution.objects.filter(
                created_by=self.user,
                status__in=(OperationExecution.QUEUED, OperationExecution.RUNNING),
            ).count(),
            0,
        )

    def test_queue_cap_is_owner_scoped(self) -> None:
        other = get_user_model().objects.create_user("t24-other-owner")
        for _ in range(3):
            OperationExecution.objects.create(
                bundle=self.bundle,
                recording=self.recording,
                created_by=other,
                bundle_sha256=self.bundle.snapshot_sha256,
                recording_sha256=self.recording.sha256,
                pins={},
                status=OperationExecution.QUEUED,
            )
        url = f"/api/operation-release-bundles/{self.bundle.id}/executions"
        body = json.dumps({"recording_id": str(self.recording.id)})
        with override_settings(TRAINING={"OPERATION_EXECUTION_QUEUE_LIMIT": 1}):
            response = self.client.post(url, body, content_type="application/json")
        self.assertEqual(response.status_code, 202, response.content)
        self.assertEqual(
            OperationExecution.objects.filter(created_by=self.user, status=OperationExecution.QUEUED).count(),
            1,
        )

    def test_enqueue_locks_persisted_owner_row(self) -> None:
        locked: list[type] = []
        real = QuerySet.select_for_update

        def spy(queryset, *args, **kwargs):
            locked.append(queryset.model)
            return real(queryset, *args, **kwargs)

        with patch.object(QuerySet, "select_for_update", spy):
            data = self.enqueue()
        self.assertEqual(data["status"], "queued")
        self.assertIn(get_user_model(), locked)


def _use_file_backed_sqlite_for_immediate_writers() -> None:
    """Use a process-unique file TEST DB for overlapping IMMEDIATE writers.

    Django's default SQLite test database is ``file:memorydb_...?mode=memory&cache=shared``.
    Shared-cache concurrent writers raise ``database table is locked`` (SQLITE_LOCKED)
    immediately; busy ``timeout`` does not apply. Production is a file with
    ``transaction_mode=IMMEDIATE`` and ``timeout=20``. This override is import-time so
    Django's test runner creates that file before ``setup_databases``. The path is
    process-unique and kept out of the application data directory.
    """
    test_cfg = settings.DATABASES["default"].setdefault("TEST", {})
    name = str(test_cfg.get("NAME") or "")
    if name and name != ":memory:" and "mode=memory" not in name:
        return
    test_cfg["NAME"] = str(
        Path(tempfile.gettempdir()) / f"t24-queue-concurrency-{os.getpid()}.sqlite3"
    )


if os.environ.get("ATHENA_T24_QUEUE_CONCURRENCY_CHILD") == "1":
    _use_file_backed_sqlite_for_immediate_writers()


class ExecutionQueueConcurrencyTests(TransactionTestCase):
    """Two overlapping same-owner HTTP enqueues at limit=1.

    Django 6.1 ``select_for_update()`` is a no-op on SQLite; this does not assert
    a portable row lock or a multi-process / PostgreSQL guarantee. The configured
    engine uses ``transaction_mode=IMMEDIATE`` and ``timeout=20``. Proof is in-process:
    two request-boundary clients on distinct connections, with a test-only hold at the
    view's existing ``transaction.atomic()`` after count+insert and before commit. The
    test asserts the second request reaches the atomic wrapper during that hold; it
    does not observe SQLite's internal lock wait.
    """

    _bundle = DurableExecutionBase._bundle

    def setUp(self):
        super().setUp()
        DurableExecutionBase.setUp(self)

    def test_two_simultaneous_same_owner_enqueues_at_limit_one_never_both_accepted(self) -> None:
        if os.environ.get("ATHENA_T24_QUEUE_CONCURRENCY_CHILD") != "1":
            with tempfile.TemporaryDirectory(prefix="t24-queue-test-data-") as data_home:
                env = os.environ.copy()
                env["ATHENA_T24_QUEUE_CONCURRENCY_CHILD"] = "1"
                env["ATHENA_DATA_HOME"] = data_home
                test_label = (
                    f"{self.__class__.__module__}.{self.__class__.__name__}."
                    f"{self._testMethodName}"
                )
                result = subprocess.run(
                    [sys.executable, "manage.py", "test", test_label],
                    cwd=Path(__file__).resolve().parents[1],
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=90,
                )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return

        self.assertEqual(
            settings.DATABASES["default"]["OPTIONS"].get("transaction_mode"),
            "IMMEDIATE",
        )
        url = f"/api/operation-release-bundles/{self.bundle.id}/executions"
        body = json.dumps({"recording_id": str(self.recording.id)})
        clients = [Client(), Client()]
        for client in clients:
            client.force_login(self.user)
        start = threading.Barrier(2)
        first_body_done = threading.Event()
        second_begin_attempted = threading.Event()
        self.addCleanup(first_body_done.set)
        self.addCleanup(second_begin_attempted.set)
        overlap = {"held_after_insert_until_second_begin": False}
        results: list[tuple[int, str]] = []
        db_wrappers: list[object] = []
        sqlite_conns: list[object] = []
        errors: list[BaseException] = []
        lock = threading.Lock()
        roles: dict[int, int] = {}
        real_atomic = execution_views.transaction.atomic

        def _in_bundle_executions() -> bool:
            return any(frame.function == "bundle_executions" for frame in inspect.stack(context=0))

        @contextmanager
        def overlapping_atomic(*args, **kwargs):
            using = kwargs.get("using")
            if using is None and args and isinstance(args[0], str):
                using = args[0]
            connection = connections[using or DEFAULT_DB_ALIAS]
            if connection.in_atomic_block or not _in_bundle_executions():
                with real_atomic(*args, **kwargs):
                    yield
                return
            with lock:
                role = roles.setdefault(threading.get_ident(), len(roles) + 1)
            if role >= 2:
                second_begin_attempted.set()
            with real_atomic(*args, **kwargs):
                try:
                    yield
                finally:
                    if role == 1:
                        first_body_done.set()
                        overlap["held_after_insert_until_second_begin"] = (
                            second_begin_attempted.wait(timeout=5)
                        )

        def post(client: Client) -> None:
            close_old_connections()
            try:
                start.wait(timeout=5)
                response = client.post(url, body, content_type="application/json")
                payload = response.json()
                db = connections[DEFAULT_DB_ALIAS]
                with lock:
                    results.append((response.status_code, str(payload.get("error") or "")))
                    db_wrappers.append(db)
                    sqlite_conns.append(db.connection)
            except BaseException as error:
                with lock:
                    errors.append(error)
            finally:
                close_old_connections()

        with (
            override_settings(TRAINING={"OPERATION_EXECUTION_QUEUE_LIMIT": 1}),
            patch.object(execution_views.transaction, "atomic", overlapping_atomic),
        ):
            threads = [
                threading.Thread(target=post, args=(client,), name=f"t24-enqueue-{index}")
                for index, client in enumerate(clients)
            ]
            close_old_connections()
            try:
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=25)
                    self.assertFalse(thread.is_alive(), "enqueue thread did not finish")
            finally:
                second_begin_attempted.set()
                first_body_done.set()

        self.assertEqual(errors, [], errors)
        statuses = [status for status, _error_text in results]
        self.assertEqual(sorted(statuses), [202, 429], results)
        denied = [error_text for status, error_text in results if status == 429]
        self.assertEqual(len(denied), 1, results)
        self.assertIn("queued or running", denied[0].lower())
        self.assertNotIn("full coverage", denied[0].lower())
        self.assertEqual(
            OperationExecution.objects.filter(
                created_by=self.user,
                status__in=(OperationExecution.QUEUED, OperationExecution.RUNNING),
            ).count(),
            1,
        )
        self.assertEqual(len(db_wrappers), 2, db_wrappers)
        self.assertIsNot(db_wrappers[0], db_wrappers[1])
        self.assertIsNotNone(sqlite_conns[0])
        self.assertIsNotNone(sqlite_conns[1])
        self.assertIsNot(sqlite_conns[0], sqlite_conns[1])
        self.assertTrue(
            overlap["held_after_insert_until_second_begin"],
            "second request never reached the view transaction while the first "
            "still held its uncommitted insert",
        )
        self.assertTrue(first_body_done.is_set())
        self.assertTrue(second_begin_attempted.is_set())


class AbandonedInterruptionApiTests(DurableExecutionBase):
    """Abandoned-worker failure exposes a bounded interruption summary; cancel/fail do not."""

    def test_abandonment_does_not_grow_checkpoint_past_its_byte_limit(self) -> None:
        execution = self.enqueue_and_claim("worker-a")
        assert execution is not None
        low = 0
        high = executions.MAX_CHECKPOINT_BYTES
        while low <= high:
            padding = (low + high) // 2
            try:
                written = executions.checkpoint_progress(
                    execution,
                    "worker-a",
                    executions.STAGE_RECORDING_VERIFIED,
                    effects={"padding": "x" * padding},
                )
            except executions.CheckpointError:
                high = padding - 1
            else:
                self.assertTrue(written)
                low = padding + 1
        execution.refresh_from_db()
        before = json.dumps(
            execution.progress_checkpoint, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        self.assertLessEqual(len(before), executions.MAX_CHECKPOINT_BYTES)
        self.assertGreater(len(before), executions.MAX_CHECKPOINT_BYTES - 32)

        past = timezone.now() - timedelta(seconds=3 * executions.LEASE_SECONDS)
        OperationExecution.objects.filter(pk=execution.pk).update(
            lease_expires_at=past, heartbeat_at=past
        )
        self.assertEqual(executions.fail_abandoned_executions(), 1)
        execution.refresh_from_db()
        after = json.dumps(
            execution.progress_checkpoint, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        self.assertLessEqual(
            len(after),
            executions.MAX_CHECKPOINT_BYTES,
            "recovery metadata must not overflow the bounded checkpoint",
        )

    def test_stale_lease_reaper_detail_api_shows_interruption_without_complete_result(self) -> None:
        execution = self.enqueue_and_claim("worker-a")
        self.assertTrue(
            executions.checkpoint_progress(
                execution, "worker-a", executions.STAGE_TIMELINE_BUILT
            )
        )
        past = timezone.now() - timedelta(seconds=3 * executions.LEASE_SECONDS)
        OperationExecution.objects.filter(pk=execution.pk).update(
            lease_expires_at=past, heartbeat_at=past
        )
        self.assertEqual(executions.fail_abandoned_executions(), 1)
        response = self.client.get(f"/api/operation-executions/{execution.pk}")
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()["execution"]
        self.assertEqual(payload["status"], OperationExecution.FAILED)
        self.assertEqual(payload["result"], {})
        self.assertNotEqual(payload["result"].get("kind"), "reference_replay")
        checkpoint = payload["progress_checkpoint"]
        self.assertEqual(checkpoint["stage"], executions.STAGE_TIMELINE_BUILT)
        self.assertIn(executions.STAGE_TIMELINE_BUILT, checkpoint["completed_stages"])
        self.assertFalse(checkpoint["full_frame_coverage"])
        self.assertEqual(checkpoint["recovery"], executions.RECOVERY_NOT_RESUMABLE)
        interruption = checkpoint["interruption"]
        self.assertEqual(interruption["kind"], "worker_abandoned")
        self.assertTrue(interruption["incomplete"])
        self.assertFalse(interruption["resumable"])
        self.assertNotIn("missing_frames", interruption)
        self.assertNotIn("missing_frame_count", interruption)
        self.assertNotIn("missing_frames", checkpoint)
        self.assertNotIn("missing_frame_count", checkpoint)

    def test_ordinary_cancellation_and_failure_are_not_worker_interruption(self) -> None:
        cancelled = self.enqueue_and_claim("worker-a")
        self.assertTrue(
            executions.checkpoint_progress(
                cancelled, "worker-a", executions.STAGE_RECORDING_VERIFIED
            )
        )
        OperationExecution.objects.filter(pk=cancelled.pk).update(cancel_requested=True)
        finished_cancel = executions.run_execution(cancelled, "worker-a")
        self.assertEqual(finished_cancel.status, OperationExecution.CANCELLED)
        cancel_api = self.client.get(f"/api/operation-executions/{cancelled.pk}")
        self.assertEqual(cancel_api.status_code, 200, cancel_api.content)
        cancel_checkpoint = cancel_api.json()["execution"]["progress_checkpoint"]
        self.assertNotIn("interruption", cancel_checkpoint)
        self.assertEqual(cancel_checkpoint["stage"], executions.STAGE_RECORDING_VERIFIED)

        failed = self.enqueue_and_claim("worker-b")
        with patch.object(executions, "_recording_digest", side_effect=RuntimeError("forced ordinary failure")):
            finished_fail = executions.run_execution(failed, "worker-b")
        self.assertEqual(finished_fail.status, OperationExecution.FAILED)
        self.assertEqual(finished_fail.error, "forced ordinary failure")
        fail_api = self.client.get(f"/api/operation-executions/{failed.pk}")
        self.assertEqual(fail_api.status_code, 200, fail_api.content)
        fail_payload = fail_api.json()["execution"]
        self.assertNotIn("interruption", fail_payload["progress_checkpoint"])
        self.assertEqual(fail_payload["result"], {})

    def test_failed_abandonment_fence_does_not_write_interruption(self) -> None:
        execution = self.enqueue_and_claim("worker-a")
        self.assertTrue(
            executions.checkpoint_progress(
                execution, "worker-a", executions.STAGE_RECORDING_VERIFIED
            )
        )
        now = timezone.now()
        past = now - timedelta(seconds=3 * executions.LEASE_SECONDS)
        OperationExecution.objects.filter(pk=execution.pk).update(
            lease_expires_at=past, heartbeat_at=past
        )
        deadline = now - timedelta(seconds=2 * executions.LEASE_SECONDS)
        OperationExecution.objects.filter(pk=execution.pk).update(
            lease_expires_at=now + timedelta(seconds=executions.LEASE_SECONDS),
            heartbeat_at=now,
        )
        self.assertFalse(executions._finish_abandoned(execution, deadline=deadline))
        execution.refresh_from_db()
        self.assertEqual(execution.status, OperationExecution.RUNNING)
        self.assertNotIn("interruption", execution.progress_checkpoint)
        response = self.client.get(f"/api/operation-executions/{execution.pk}")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotIn("interruption", response.json()["execution"]["progress_checkpoint"])
        self.assertEqual(response.json()["execution"]["status"], OperationExecution.RUNNING)

    def test_abandoned_empty_checkpoint_api_shows_interruption_stored_stays_empty(self) -> None:
        execution = self.enqueue_and_claim("worker-a")
        past = timezone.now() - timedelta(seconds=3 * executions.LEASE_SECONDS)
        OperationExecution.objects.filter(pk=execution.pk).update(
            lease_expires_at=past, heartbeat_at=past
        )
        self.assertEqual(executions.fail_abandoned_executions(), 1)
        execution.refresh_from_db()
        self.assertEqual(execution.progress_checkpoint, {})
        self.assertEqual(execution.result, {})
        response = self.client.get(f"/api/operation-executions/{execution.pk}")
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()["execution"]
        self.assertEqual(payload["status"], OperationExecution.FAILED)
        self.assertEqual(payload["result"], {})
        checkpoint = payload["progress_checkpoint"]
        self.assertIsNone(checkpoint["stage"])
        self.assertFalse(checkpoint["full_frame_coverage"])
        self.assertEqual(checkpoint["recovery"], executions.RECOVERY_NOT_RESUMABLE)
        self.assertEqual(checkpoint["interruption"]["kind"], "worker_abandoned")
        self.assertTrue(checkpoint["interruption"]["incomplete"])
        self.assertFalse(checkpoint["interruption"]["resumable"])


class SuccessorQueueAccountedInterruptionTests(TransactionTestCase):
    """Successor ``process_queue`` reaps a worker-path interrupt and accounts gaps.

    Evidence is in-process: a first worker thread runs ``run_execution``, a lease
    expiry after a real checkpoint simulates death, and ``process_queue`` with
    ``owner_key="worker-restart"`` fences the same row. This is not an OS/SIGKILL
    or T56 redeployment drill, and not a direct ORM expiry plus
    ``fail_abandoned_executions()`` stand-in for restart.
    """

    _bundle = DurableExecutionBase._bundle
    enqueue = DurableExecutionBase.enqueue
    enqueue_and_claim = DurableExecutionBase.enqueue_and_claim

    def setUp(self) -> None:
        super().setUp()
        DurableExecutionBase.setUp(self)

    def _pin_gapped_t20_clip(self) -> None:
        """Replace the 1s fixture with the T20 gapped clip and truthful bundle pins."""
        import shutil

        from django.core.files.storage import default_storage

        from .tests_t19_durable import _NamedBytes
        from .tests_t20_source_time import make_clip

        tmp = tempfile.mkdtemp(prefix="t24-gap-clip-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        body = make_clip(tmp, seconds=3, drop_between=(1.0, 2.0), creation_time="2026-11-01T09:30:00Z")
        default_storage.delete(self.recording.storage_key)
        key = default_storage.save(self.recording.storage_key, _NamedBytes(body))
        self.addCleanup(default_storage.delete, key)
        self.recording.storage_key = key
        self.recording.sha256 = hashlib.sha256(body).hexdigest()
        self.recording.size_bytes = len(body)
        self.recording.duration_seconds = 3.0
        self.recording.save(update_fields=["storage_key", "sha256", "size_bytes", "duration_seconds"])
        snapshot = dict(self.bundle.snapshot)
        recording_pin = dict(snapshot.get("recording") or {})
        recording_pin["sha256"] = self.recording.sha256
        recording_pin["size_bytes"] = self.recording.size_bytes
        recording_pin["duration_seconds"] = 3.0
        snapshot["recording"] = recording_pin
        encoded = json.dumps(
            snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        from .models import OperationDraft

        draft = OperationDraft.objects.create(
            created_by=self.user, operation=self.operation, name="Gate count gapped"
        )
        next_number = OperationReleaseBundle.objects.filter(operation=self.operation).count() + 1
        self.bundle = OperationReleaseBundle.objects.create(
            operation=self.operation,
            draft=draft,
            draft_revision=1,
            number=next_number,
            created_by=self.user,
            snapshot=snapshot,
            snapshot_sha256=hashlib.sha256(encoded.encode()).hexdigest(),
        )

    @skipUnless(REQUIRE_FFMPEG, "running an execution needs a decodable recording (ffmpeg)")
    def test_successor_process_queue_accounts_unprocessed_stages_after_worker_path_interrupt(
        self,
    ) -> None:
        self._pin_gapped_t20_clip()
        execution = self.enqueue_and_claim("worker-a")
        assert execution is not None
        execution_id = execution.pk
        real_beat = executions._beat

        def expire_after_timeline_checkpoint(execution_row, owner_key, lease_seconds=executions.LEASE_SECONDS):
            current = OperationExecution.objects.filter(pk=execution_row.pk).values("progress_checkpoint").first()
            completed = ((current or {}).get("progress_checkpoint") or {}).get("completed_stages") or []
            if executions.STAGE_TIMELINE_BUILT in completed:
                past = timezone.now() - timedelta(seconds=3 * executions.LEASE_SECONDS)
                OperationExecution.objects.filter(pk=execution_row.pk).update(
                    lease_expires_at=past, heartbeat_at=past
                )
            return real_beat(execution_row, owner_key, lease_seconds)

        worker_errors: list[BaseException] = []
        finished_holder: dict[str, OperationExecution] = {}

        def worker() -> None:
            close_old_connections()
            try:
                with patch.object(executions, "_beat", side_effect=expire_after_timeline_checkpoint):
                    finished_holder["row"] = executions.run_execution(execution, "worker-a")
            except BaseException as error:  # noqa: BLE001 - contain the worker thread
                worker_errors.append(error)
            finally:
                close_old_connections()

        thread = threading.Thread(target=worker, name="t24-interrupted-worker")
        close_old_connections()
        try:
            thread.start()
            thread.join(timeout=60)
            self.assertFalse(thread.is_alive(), "interrupted worker thread did not finish")
        finally:
            if thread.is_alive():
                thread.join(timeout=5)

        self.assertEqual(worker_errors, [], worker_errors)
        finished = finished_holder["row"]
        self.assertEqual(finished.pk, execution_id)
        self.assertEqual(finished.status, OperationExecution.RUNNING)
        self.assertEqual(finished.result, {})
        completed = finished.progress_checkpoint.get("completed_stages") or []
        self.assertIn(executions.STAGE_RECORDING_VERIFIED, completed)
        self.assertIn(executions.STAGE_TIMELINE_BUILT, completed)
        self.assertNotIn(executions.STAGE_REFERENCE_EVENTS_COMMITTED, completed)
        stored_before = json.dumps(
            finished.progress_checkpoint, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

        processed = executions.process_queue(owner_key="worker-restart", once=True)
        self.assertEqual(processed, 0)
        again = executions.process_queue(owner_key="worker-restart", once=True)
        self.assertEqual(again, 0)

        reaped = OperationExecution.objects.get(pk=execution_id)
        self.assertEqual(reaped.status, OperationExecution.FAILED)
        self.assertEqual(reaped.error, executions.ABANDONED_WORKER_ERROR)
        self.assertEqual(reaped.result, {})
        self.assertEqual(reaped.lease_owner, "")
        self.assertEqual(
            OperationExecution.objects.filter(pk=execution_id).count(),
            1,
        )
        stored_after = json.dumps(
            reaped.progress_checkpoint, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        self.assertEqual(stored_after, stored_before)
        self.assertNotIn("unprocessed_stages", reaped.progress_checkpoint)
        self.assertNotIn("interruption", reaped.progress_checkpoint)
        self.assertLessEqual(len(stored_after), executions.MAX_CHECKPOINT_BYTES)

        serialized = executions.serialize_progress_checkpoint(reaped)
        self.assertFalse(serialized["full_frame_coverage"])
        self.assertEqual(serialized["recovery"], executions.RECOVERY_NOT_RESUMABLE)
        interruption = serialized["interruption"]
        self.assertEqual(interruption["kind"], executions.INTERRUPTION_WORKER_ABANDONED)
        self.assertTrue(interruption["incomplete"])
        self.assertFalse(interruption["resumable"])
        self.assertNotIn("missing_frames", interruption)
        self.assertNotIn("missing_frame_count", interruption)
        self.assertNotIn("missing_frames", serialized)
        self.assertNotIn("missing_frame_count", serialized)
        expected_unprocessed = [
            executions.STAGE_REFERENCE_EVENTS_COMMITTED,
            executions.STAGE_DETECTOR_SAMPLED,
            executions.STAGE_TRACKS_ASSOCIATED,
        ]
        self.assertIn("unprocessed_stages", interruption)
        self.assertEqual(interruption["unprocessed_stages"], expected_unprocessed)
        self.assertEqual(
            interruption["in_memory_timeline_detections_tracks"],
            "discarded_not_restored",
        )

        response = self.client.get(f"/api/operation-executions/{execution_id}")
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()["execution"]
        self.assertEqual(payload["status"], OperationExecution.FAILED)
        self.assertEqual(payload["result"], {})
        api_interrupt = payload["progress_checkpoint"]["interruption"]
        self.assertEqual(api_interrupt["unprocessed_stages"], expected_unprocessed)
        self.assertEqual(
            api_interrupt["in_memory_timeline_detections_tracks"],
            "discarded_not_restored",
        )
        self.assertFalse(payload["progress_checkpoint"]["full_frame_coverage"])
        self.assertEqual(payload["progress_checkpoint"]["recovery"], executions.RECOVERY_NOT_RESUMABLE)
        coverage = payload["progress_checkpoint"]["timeline_coverage"]
        self.assertEqual(coverage["gap_count"], 1)
        self.assertEqual(coverage["session_count"], 2)
        self.assertEqual(coverage["frame_count"], 9)
        self.assertEqual(coverage["gap_threshold_seconds"], 1.0)
        self.assertIn("jitter_count", coverage)
        self.assertIn("late_frame_count", coverage)
        self.assertIn("timeline_sha256", coverage)
        self.assertNotIn("gaps", coverage)
        self.assertNotIn("sessions", coverage)
        self.assertNotIn("missing_frame_count", coverage)
        self.assertNotIn("missing_frame_count", payload["progress_checkpoint"])
        stored_coverage = reaped.progress_checkpoint["timeline_coverage"]
        self.assertEqual(stored_coverage["gap_count"], 1)
        self.assertNotIn("gaps", stored_coverage)
        self.assertNotIn("sessions", stored_coverage)
        self.assertEqual(serialized["timeline_coverage"]["gap_count"], 1)
        self.assertNotIn("missing_frame_count", serialized["timeline_coverage"])


class FramePolicyAndGapAccountingTests(DurableExecutionBase):
    """Recorded full analysis vs live frame-dropping policies and full gap accounting (T24)."""

    _pin_fake_detector = CheckpointWorkerIntegrationTests._pin_fake_detector

    def _pin_clean_3s_clip(self) -> None:
        """Replace the 1s (5-frame) fixture with a clean 3s (15-frame) clip (> DEFAULT_SAMPLE_FRAMES=6)."""
        import shutil

        from django.core.files.storage import default_storage

        from .models import OperationDraft
        from .tests_t19_durable import _NamedBytes
        from .tests_t20_source_time import make_clip

        tmp = tempfile.mkdtemp(prefix="t24-clean-3s-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        body = make_clip(tmp, seconds=3, creation_time="2026-11-01T09:30:00Z")
        default_storage.delete(self.recording.storage_key)
        key = default_storage.save(self.recording.storage_key, _NamedBytes(body))
        self.addCleanup(default_storage.delete, key)
        self.recording.storage_key = key
        self.recording.sha256 = hashlib.sha256(body).hexdigest()
        self.recording.size_bytes = len(body)
        self.recording.duration_seconds = 3.0
        self.recording.save(update_fields=["storage_key", "sha256", "size_bytes", "duration_seconds"])
        snapshot = dict(self.bundle.snapshot)
        recording_pin = dict(snapshot.get("recording") or {})
        recording_pin["sha256"] = self.recording.sha256
        recording_pin["size_bytes"] = self.recording.size_bytes
        recording_pin["duration_seconds"] = 3.0
        snapshot["recording"] = recording_pin
        encoded = json.dumps(
            snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        draft = OperationDraft.objects.create(
            created_by=self.user, operation=self.operation, name="Gate count clean 3s"
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

    def _pin_policy_bundle(
        self,
        *,
        frame_processing_policy: str,
        max_in_flight_frames: int | None = None,
        max_coverage_gap_seconds: float = 5.0,
    ) -> None:
        from .models import OperationDraft, PolicyVersion

        self._pin_fake_detector()
        policy_cfg = {
            "evidence_retention_days": 90,
            "max_coverage_gap_seconds": max_coverage_gap_seconds,
            "frame_processing_policy": frame_processing_policy,
        }
        if max_in_flight_frames is not None:
            policy_cfg["max_in_flight_frames"] = max_in_flight_frames
        encoded_cfg = json.dumps(policy_cfg, sort_keys=True, separators=(",", ":"))
        policy_row = PolicyVersion.objects.create(
            operation=self.operation,
            version=PolicyVersion.objects.filter(operation=self.operation).count() + 1,
            created_by=self.user,
            configuration=policy_cfg,
            configuration_sha256=hashlib.sha256(encoded_cfg.encode()).hexdigest(),
        )
        snapshot = dict(self.bundle.snapshot)
        pins = dict(snapshot.get("component_pins") or {})
        pins["policy"] = {
            "id": str(policy_row.id),
            "version": policy_row.version,
            "sha256": policy_row.configuration_sha256,
            "configuration": policy_cfg,
        }
        snapshot["component_pins"] = pins
        encoded = json.dumps(
            snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        draft = OperationDraft.objects.create(
            created_by=self.user,
            operation=self.operation,
            name=f"Policy {frame_processing_policy}",
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

    @skipUnless(REQUIRE_FFMPEG, "running an execution needs a decodable recording (ffmpeg)")
    def test_recorded_full_policy_analyses_every_frame_and_reports_full_coverage_when_clean(self) -> None:
        register_backend(_T24FakeDetector)
        self.addCleanup(lambda: unregister_backend("t24-fake-detector"))
        self._pin_clean_3s_clip()
        self._pin_policy_bundle(frame_processing_policy=executions.FRAME_POLICY_RECORDED_FULL)
        execution = self.enqueue_and_claim("worker-a")
        finished = executions.run_execution(execution, "worker-a")
        self.assertEqual(finished.status, OperationExecution.SUCCEEDED, finished.error)
        total_frames = finished.result["frame_timeline"]["frame_count"]
        self.assertEqual(total_frames, 15)
        self.assertGreater(total_frames, executions.inference.DEFAULT_SAMPLE_FRAMES)
        self.assertEqual(finished.result["detector"]["frame_count"], total_frames)
        self.assertEqual(
            finished.result["detector"]["frame_policy"],
            executions.FRAME_POLICY_RECORDED_FULL,
        )
        gap_acc = finished.result["gap_accounting"]
        self.assertEqual(gap_acc["frame_processing_policy"], executions.FRAME_POLICY_RECORDED_FULL)
        self.assertEqual(gap_acc["analysed_frame_count"], total_frames)
        self.assertEqual(gap_acc["unanalysed_frame_count"], 0)
        self.assertEqual(gap_acc["sampled_skip_count"], 0)
        self.assertEqual(gap_acc["dropped_frame_count"], 0)
        self.assertFalse(gap_acc["overload_dropped"])
        self.assertEqual(gap_acc["source_gap_count"], 0)
        self.assertTrue(gap_acc["full_frame_coverage"])
        self.assertFalse(finished.progress_checkpoint["sampled_analysis"])
        self.assertTrue(finished.progress_checkpoint["full_frame_coverage"])

    @skipUnless(REQUIRE_FFMPEG, "running an execution needs a decodable recording (ffmpeg)")
    def test_recorded_full_on_gapped_clip_never_claims_full_coverage_and_flags_policy_gap(self) -> None:
        register_backend(_T24FakeDetector)
        self.addCleanup(lambda: unregister_backend("t24-fake-detector"))
        SuccessorQueueAccountedInterruptionTests._pin_gapped_t20_clip(self)
        self._pin_policy_bundle(
            frame_processing_policy=executions.FRAME_POLICY_RECORDED_FULL,
            max_coverage_gap_seconds=0.5,
        )
        execution = self.enqueue_and_claim("worker-a")
        finished = executions.run_execution(execution, "worker-a")
        self.assertEqual(finished.status, OperationExecution.SUCCEEDED, finished.error)
        gap_acc = finished.result["gap_accounting"]
        self.assertEqual(gap_acc["total_timeline_frames"], 9)
        self.assertEqual(gap_acc["analysed_frame_count"], gap_acc["on_time_frame_count"])
        self.assertEqual(gap_acc["source_gap_count"], 1)
        self.assertTrue(gap_acc["policy_gap_exceeded"])
        self.assertTrue(gap_acc["gaps"][0]["exceeds_policy_max_gap"])
        self.assertFalse(gap_acc["full_frame_coverage"])
        self.assertFalse(finished.progress_checkpoint["full_frame_coverage"])

    @skipUnless(REQUIRE_FFMPEG, "running an execution needs a decodable recording (ffmpeg)")
    def test_recorded_full_refuses_to_drop_frames_when_queue_limit_exceeded(self) -> None:
        register_backend(_T24FakeDetector)
        self.addCleanup(lambda: unregister_backend("t24-fake-detector"))
        self._pin_policy_bundle(
            frame_processing_policy=executions.FRAME_POLICY_RECORDED_FULL,
            max_in_flight_frames=3,
        )
        execution = self.enqueue_and_claim("worker-a")
        finished = executions.run_execution(execution, "worker-a")
        self.assertEqual(finished.status, OperationExecution.FAILED)
        self.assertIn("refuses to drop frames", finished.error.lower())
        self.assertFalse(finished.progress_checkpoint.get("full_frame_coverage", True))

    @skipUnless(REQUIRE_FFMPEG, "running an execution needs a decodable recording (ffmpeg)")
    def test_live_frame_drop_policy_bounds_queue_splits_sessions_and_never_claims_full_coverage(
        self,
    ) -> None:
        register_backend(_T24FakeDetector)
        self.addCleanup(lambda: unregister_backend("t24-fake-detector"))
        self._pin_policy_bundle(
            frame_processing_policy=executions.FRAME_POLICY_LIVE_DROP,
            max_in_flight_frames=3,
        )
        execution = self.enqueue_and_claim("worker-a")
        finished = executions.run_execution(execution, "worker-a")
        self.assertEqual(finished.status, OperationExecution.SUCCEEDED, finished.error)
        total_frames = finished.result["frame_timeline"]["frame_count"]
        gap_acc = finished.result["gap_accounting"]
        self.assertEqual(gap_acc["frame_processing_policy"], executions.FRAME_POLICY_LIVE_DROP)
        self.assertEqual(gap_acc["analysed_frame_count"], 3)
        self.assertEqual(gap_acc["dropped_frame_count"], total_frames - 3)
        self.assertTrue(gap_acc["overload_dropped"])
        self.assertEqual(gap_acc["overload_gap_count"], 2)
        self.assertFalse(gap_acc["full_frame_coverage"])
        self.assertFalse(finished.progress_checkpoint["full_frame_coverage"])
        self.assertFalse(finished.progress_checkpoint["sampled_analysis"])
        # Overload drop gaps split tracking into 3 distinct sessions so tracks never bridge dropped frames.
        self.assertEqual(finished.result["tracks"]["sessions"], 3)
        self.assertTrue(
            all(g["kind"] == "live_overload_drop" for g in gap_acc["gaps"]),
            gap_acc["gaps"],
        )


class WorkerRestartAndReconnectRecoveryTests(DurableExecutionBase):
    """Worker restart and source reconnect drills recover state, account gaps, and deduplicate ledger rows."""

    _pin_fake_detector = CheckpointWorkerIntegrationTests._pin_fake_detector

    def _pin_detector_and_line(self) -> None:
        from .models import GeometryVersion, OperationDraft, SourceViewVersion

        self._pin_fake_detector()
        source_view = SourceViewVersion.objects.create(
            source=self.source,
            version=1,
            created_by=self.user,
            configuration={"width": 320, "height": 240, "rotation": 0, "horizontal_flip": False, "vertical_flip": False},
            configuration_sha256="1" * 64,
        )
        geometry = GeometryVersion.objects.create(
            operation=self.operation,
            source_view_version=source_view,
            version=1,
            created_by=self.user,
            geometry={"line": {"start": [0.1, 0.5], "end": [0.9, 0.5]}},
            geometry_sha256="2" * 64,
        )
        snapshot = dict(self.bundle.snapshot)
        pins = dict(snapshot.get("component_pins") or {})
        pins["geometry"] = {
            "id": str(geometry.id),
            "version": geometry.version,
            "sha256": geometry.geometry_sha256,
        }
        snapshot["component_pins"] = pins
        encoded = json.dumps(
            snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        draft = OperationDraft.objects.create(
            created_by=self.user, operation=self.operation, name="Restart recovery draft"
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

    def test_recover_execution_refuses_to_steal_a_live_lease(self) -> None:
        execution = self.enqueue_and_claim("worker-a")
        with self.assertRaises(executions.LeaseLost):
            executions.recover_execution(execution, "worker-b")
        execution.refresh_from_db()
        self.assertEqual(execution.status, OperationExecution.RUNNING)
        self.assertEqual(execution.lease_owner, "worker-a")

    @skipUnless(REQUIRE_FFMPEG, "running an execution needs a decodable recording (ffmpeg)")
    def test_worker_restart_and_reconnect_recovers_checkpoint_deduplicates_events_and_accounts_gap(
        self,
    ) -> None:
        register_backend(_T24FakeDetector)
        self.addCleanup(lambda: unregister_backend("t24-fake-detector"))
        self._pin_detector_and_line()
        execution = self.enqueue_and_claim("worker-a")

        # Worker A completes early stages and commits reference events before dying.
        self.assertTrue(
            executions.checkpoint_progress(
                execution, "worker-a", executions.STAGE_RECORDING_VERIFIED
            )
        )
        self.assertTrue(
            executions.checkpoint_progress(
                execution, "worker-a", executions.STAGE_TIMELINE_BUILT
            )
        )
        snapshot = execution.bundle.snapshot or {}
        reference = executions._reference_events(snapshot)
        tolerance = executions._comparison_tolerance(snapshot)
        pre_events = executions.commit_reference_events_owned(
            execution,
            "worker-a",
            [
                executions.ledger.EventDraft(
                    kind=OperationEvent.KIND_REFERENCE,
                    source=executions._ledger_source(execution),
                    session=0,
                    timestamp=crossing.time,
                    payload={"direction": crossing.direction, "tolerance_seconds": tolerance},
                )
                for crossing in reference
            ],
        )
        self.assertGreater(len(pre_events), 0)
        self.assertTrue(
            executions.checkpoint_progress(
                execution, "worker-a", executions.STAGE_REFERENCE_EVENTS_COMMITTED
            )
        )

        # Simulate worker death and stale-lease abandonment reaping.
        past = timezone.now() - timedelta(seconds=3 * executions.LEASE_SECONDS)
        OperationExecution.objects.filter(pk=execution.pk).update(
            lease_expires_at=past, heartbeat_at=past
        )
        self.assertEqual(executions.fail_abandoned_executions(), 1)
        execution.refresh_from_db()
        self.assertEqual(execution.status, OperationExecution.FAILED)

        # Worker B restarts/reconnects and recovers the execution with a reconnect boundary at 0.4s.
        recovered = executions.recover_execution(
            execution, "worker-b", reconnect_after_pts=0.4
        )
        self.assertEqual(recovered.status, OperationExecution.SUCCEEDED, recovered.error)
        recovery_info = recovered.result["recovery"]
        self.assertTrue(recovery_info["recovered"])
        self.assertEqual(recovery_info["recovered_by"], "worker-b")
        self.assertIn(executions.STAGE_REFERENCE_EVENTS_COMMITTED, recovery_info["restored_stages"])
        self.assertEqual(recovery_info["pre_existing_ledger_events"], len(pre_events))
        self.assertEqual(recovery_info["duplicate_events_committed"], 0)
        self.assertTrue(recovery_info["new_track_session_started"])
        self.assertEqual(recovery_info["reconnect_gap_count"], 1)
        self.assertTrue(recovery_info["aggregates_match_ledger"])

        # Gap accounting records the explicit worker_restart_reconnect gap and keeps full_frame_coverage False.
        gap_acc = recovered.result["gap_accounting"]
        self.assertEqual(gap_acc["reconnect_gap_count"], 1)
        self.assertFalse(gap_acc["full_frame_coverage"])
        reconnect_gaps = [g for g in gap_acc["gaps"] if g["kind"] == "worker_restart_reconnect"]
        self.assertEqual(len(reconnect_gaps), 1)
        self.assertTrue(reconnect_gaps[0]["new_session_started"])

        # Tracking was split into two sessions across the reconnect boundary.
        self.assertEqual(recovered.result["tracks"]["sessions"], 2)

        # Stored ledger rows have no duplicates and rebuild to the exact recorded aggregate.
        raw_rows = list(OperationEvent.objects.filter(execution=recovered))
        self.assertEqual(
            recovered.result["event_ledger"],
            executions.ledger.rebuild_aggregates(raw_rows).summary(),
        )
        ref_rows = [r for r in raw_rows if r.kind == OperationEvent.KIND_REFERENCE]
        self.assertEqual(len(ref_rows), len(pre_events))


class MultiProcessQueueLockTests(DurableExecutionBase):
    """Two separate OS processes enqueuing concurrently against a shared SQLite DB at limit=1."""

    def test_two_os_processes_enqueuing_at_limit_one_never_exceed_cap(self) -> None:
        with tempfile.TemporaryDirectory(prefix="t24-mp-queue-") as tmp_dir:
            db_path = Path(tmp_dir) / "mp_queue.sqlite3"
            ready_dir = Path(tmp_dir) / "ready"
            ready_dir.mkdir()
            go_file = Path(tmp_dir) / "go"

            setup_code = f"""
import os, django
from pathlib import Path
os.environ["DJANGO_SETTINGS_MODULE"] = "athena_web.settings"
os.environ["ATHENA_DATA_HOME"] = {str(tmp_dir)!r}
django.setup()
from django.conf import settings
from django.core.management import call_command
settings.DATABASES["default"]["NAME"] = {str(db_path)!r}
call_command("migrate", verbosity=0)
from django.contrib.auth import get_user_model
from projects.models import Operation, OperationDraft, OperationReleaseBundle, OperationSource, OperationSourceRecording, Site
from projects.tests import grant
user = get_user_model().objects.create_user("mp-owner")
grant(user, "labeler")
site = Site.objects.create(name="MP Site", created_by=user)
source = OperationSource.objects.create(site=site, name="MP Cam", kind="recorded_video")
operation = Operation.objects.create(site=site, source=source, name="MP Op", created_by=user)
draft = OperationDraft.objects.create(created_by=user, operation=operation, name="MP Draft")
rec = OperationSourceRecording.objects.create(
    source=source, created_by=user, storage_key="k.mp4", filename="k.mp4",
    sha256="a"*64, size_bytes=100, width=320, height=240, duration_seconds=1.0
)
bundle = OperationReleaseBundle.objects.create(
    operation=operation, draft=draft, draft_revision=1, number=1, created_by=user,
    snapshot={{"component_pins": {{}}}}, snapshot_sha256="b"*64
)
import json as _json
Path({str(Path(tmp_dir) / "ids.json")!r}).write_text(
    _json.dumps({{"bundle_id": str(bundle.id), "recording_id": str(rec.id)}})
)
"""
            web_dir = Path(__file__).resolve().parents[1]
            setup_res = subprocess.run(
                [sys.executable, "-c", setup_code],
                cwd=web_dir,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
            self.assertEqual(setup_res.returncode, 0, setup_res.stdout + setup_res.stderr)
            ids = json.loads((Path(tmp_dir) / "ids.json").read_text())

            worker_code = f"""
import json, os, sys, time, django
from pathlib import Path
idx = sys.argv[1]
os.environ["DJANGO_SETTINGS_MODULE"] = "athena_web.settings"
os.environ["ATHENA_DATA_HOME"] = {str(tmp_dir)!r}
django.setup()
from django.conf import settings
from django.db import connections
settings.DATABASES["default"]["NAME"] = {str(db_path)!r}
settings.TRAINING["OPERATION_EXECUTION_QUEUE_LIMIT"] = 1
connections.close_all()
from django.contrib.auth import get_user_model
from django.test import Client
user = get_user_model().objects.get(username="mp-owner")
client = Client()
client.force_login(user)
(Path({str(ready_dir)!r}) / f"ready-{{idx}}").write_text("1")
deadline = time.monotonic() + 10.0
while not Path({str(go_file)!r}).exists() and time.monotonic() < deadline:
    time.sleep(0.005)
resp = client.post(
    f"/api/operation-release-bundles/{ids['bundle_id']}/executions",
    json.dumps({{"recording_id": {ids['recording_id']!r}}}),
    content_type="application/json",
)
print(json.dumps({{"status": resp.status_code, "body": resp.json()}}))
"""
            procs = [
                subprocess.Popen(
                    [sys.executable, "-c", worker_code, str(i)],
                    cwd=web_dir,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                for i in (1, 2)
            ]
            try:
                deadline = timezone.now() + timedelta(seconds=15)
                while len(list(ready_dir.glob("ready-*"))) < 2 and timezone.now() < deadline:
                    pass
                go_file.write_text("1")
                outputs = [p.communicate(timeout=30) for p in procs]
            finally:
                for p in procs:
                    if p.poll() is None:
                        p.kill()

            for index, (out, err) in enumerate(outputs):
                self.assertEqual(procs[index].returncode, 0, f"proc {index}: {out}\n{err}")
            parsed = [json.loads(out.strip().splitlines()[-1]) for out, _ in outputs]
            statuses = sorted(item["status"] for item in parsed)
            self.assertEqual(statuses, [202, 429], parsed)
