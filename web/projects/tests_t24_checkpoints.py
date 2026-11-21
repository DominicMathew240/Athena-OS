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
No live source, no invented endpoint, no fake full-coverage result.
"""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.utils import timezone

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
from .tests_t19_durable import DurableExecutionBase, REQUIRE_FFMPEG
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

        def watch(execution_row, owner_key, stage, *, effects=None):
            ok = real_checkpoint(execution_row, owner_key, stage, effects=effects)
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

        def refuse_timeline(execution_row, owner_key, stage, *, effects=None):
            if stage == executions.STAGE_TIMELINE_BUILT:
                return False
            return real_checkpoint(execution_row, owner_key, stage, effects=effects)

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
