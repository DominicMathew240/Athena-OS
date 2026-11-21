"""T19 lease heartbeat: periodic refresh during the long-running worker step.

Covers the real gap stage-boundary ``_beat`` left open: a synchronous
digest/timeline/inference stage longer than ``lease_seconds`` used to lose the
lease under strict fencing. The scoped ``ExecutionLeaseHeartbeat`` must keep a
healthy owned lease alive across real wall-clock time, refuse stolen/expired
renewal, stop cleanly without leaking threads, and keep the reaper off a live
long run.

These tests use ``TransactionTestCase`` so the heartbeat thread's separate DB
connection can see committed rows (supported for this process). They do not
assert multi-process SQLite ``select_for_update`` concurrency.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import threading
import time
from datetime import timedelta
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from .models import (
    DatasetVersion,
    ModelVersion,
    Operation,
    OperationDraft,
    OperationExecution,
    OperationReleaseBundle,
    OperationSource,
    OperationSourceRecording,
    Project,
    Site,
    TrainingRun,
)
from .operations import executions
from .tests import grant
from .tests_t19_durable import REQUIRE_FFMPEG, _NamedBytes, _clip_bytes


class LeaseHeartbeatFixture(TransactionTestCase):
    """Committed fixture rows visible to a heartbeat thread's DB connection."""

    def setUp(self):
        self.media = tempfile.mkdtemp(prefix="t19-hb-media-")
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)
        self.user = get_user_model().objects.create_user("t19-hb-owner", is_superuser=True)
        grant(self.user, "approver")
        self.client.force_login(self.user)
        self.site = Site.objects.create(created_by=self.user, name="Depot")
        self.source = OperationSource.objects.create(site=self.site, name="Gate", kind="recorded_video")
        self.operation = Operation.objects.create(
            created_by=self.user, site=self.site, source=self.source, name="Gate count"
        )
        self.body = _clip_bytes()
        self.key = default_storage.save(
            f"operation-source-recordings/{self.source.id}/rec.mp4", _NamedBytes(self.body)
        )
        self.addCleanup(default_storage.delete, self.key)
        self.duration = 1.0 if REQUIRE_FFMPEG else 60.0
        self.recording = OperationSourceRecording.objects.create(
            source=self.source,
            created_by=self.user,
            storage_key=self.key,
            filename="rec.mp4",
            sha256=hashlib.sha256(self.body).hexdigest(),
            size_bytes=len(self.body),
            width=64,
            height=48,
            duration_seconds=self.duration,
        )
        self.bundle = self._bundle()

    def _bundle(self) -> OperationReleaseBundle:
        draft = OperationDraft.objects.create(
            created_by=self.user, operation=self.operation, name="Gate count"
        )
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
        ModelVersion.objects.create(
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
                "model": None,
                "screen": {"id": "scr", "version": 1, "sha256": "5" * 64},
                "policy": {"id": "pol", "version": 1, "sha256": "6" * 64},
            },
            "recording": {
                "sha256": self.recording.sha256,
                "size_bytes": self.recording.size_bytes,
                "width": 64,
                "height": 48,
                "duration_seconds": self.duration,
            },
            "reference_events": [
                {"time": 0.2, "direction": "negative_to_positive"},
                {"time": 0.6, "direction": "positive_to_negative"},
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

    def enqueue_and_claim(self, owner: str = "worker-a", *, lease_seconds: int = executions.LEASE_SECONDS):
        response = self.client.post(
            f"/api/operation-release-bundles/{self.bundle.id}/executions",
            json.dumps({"recording_id": str(self.recording.id)}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 202, response.content)
        return executions.claim_next_execution(owner, lease_seconds=lease_seconds)


class ExecutionLeaseHeartbeatUnitTests(LeaseHeartbeatFixture):
    def test_shutdown_stops_thread_without_leak(self) -> None:
        execution = self.enqueue_and_claim("worker-a")
        heartbeat = executions.ExecutionLeaseHeartbeat(
            execution.pk, "worker-a", lease_seconds=30, interval_seconds=0.2
        )
        heartbeat.start()
        self.assertTrue(heartbeat.alive)
        heartbeat.stop(join_timeout=2.0)
        self.assertFalse(heartbeat.alive)
        # Second stop is a no-op; thread must stay gone.
        heartbeat.stop(join_timeout=1.0)
        self.assertFalse(heartbeat.alive)
        time.sleep(0.5)
        self.assertFalse(heartbeat.alive)
        self.assertFalse(
            any(t.name == f"execution-lease-{execution.pk}" and t.is_alive() for t in threading.enumerate())
        )

    def test_wrong_owner_heartbeat_does_not_extend_lease(self) -> None:
        execution = self.enqueue_and_claim("worker-a")
        before = OperationExecution.objects.get(pk=execution.pk)
        before_expiry = before.lease_expires_at
        before_heartbeat = before.heartbeat_at
        heartbeat = executions.ExecutionLeaseHeartbeat(
            execution.pk, "worker-b", lease_seconds=30, interval_seconds=0.15
        )
        heartbeat.start()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not heartbeat.lost:
            time.sleep(0.05)
        heartbeat.stop(join_timeout=2.0)
        self.assertTrue(heartbeat.lost)
        after = OperationExecution.objects.get(pk=execution.pk)
        self.assertEqual(after.lease_owner, "worker-a")
        self.assertEqual(after.lease_expires_at, before_expiry)
        self.assertEqual(after.heartbeat_at, before_heartbeat)
        self.assertEqual(after.status, OperationExecution.RUNNING)

    def test_expired_lease_heartbeat_does_not_resurrect(self) -> None:
        execution = self.enqueue_and_claim("worker-a")
        past = timezone.now() - timedelta(seconds=1)
        OperationExecution.objects.filter(pk=execution.pk).update(
            lease_expires_at=past, heartbeat_at=past
        )
        heartbeat = executions.ExecutionLeaseHeartbeat(
            execution.pk, "worker-a", lease_seconds=30, interval_seconds=0.15
        )
        heartbeat.start()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not heartbeat.lost:
            time.sleep(0.05)
        heartbeat.stop(join_timeout=2.0)
        self.assertTrue(heartbeat.lost)
        after = OperationExecution.objects.get(pk=execution.pk)
        self.assertEqual(after.lease_owner, "worker-a")
        self.assertLessEqual(after.lease_expires_at, timezone.now())
        self.assertEqual(after.status, OperationExecution.RUNNING)


@skipUnless(REQUIRE_FFMPEG, "running an execution needs a decodable recording (ffmpeg)")
class ExecutionLeaseHeartbeatElapsedTests(LeaseHeartbeatFixture):
    """Real wall-clock long-stage regression (no mocked clock-only green)."""

    def test_long_stage_without_heartbeat_loses_lease_real_elapsed(self) -> None:
        """Control: stage longer than lease_seconds with heartbeat disabled fails."""
        lease_seconds = 1
        stage_sleep = 1.6
        execution = self.enqueue_and_claim("worker-a", lease_seconds=lease_seconds)
        real_timeline = executions.source_time.build_timeline

        def slow_timeline(recording, *args, **kwargs):
            time.sleep(stage_sleep)
            return real_timeline(recording, *args, **kwargs)

        started = time.monotonic()
        with patch.object(executions.source_time, "build_timeline", side_effect=slow_timeline):
            finished = executions.run_execution(
                execution,
                "worker-a",
                lease_seconds=lease_seconds,
                heartbeat_seconds=0,  # disable periodic refresh
            )
        elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, stage_sleep)
        self.assertEqual(finished.status, OperationExecution.RUNNING)
        self.assertEqual(finished.result, {})
        self.assertEqual(finished.error, "")

    def test_long_stage_with_heartbeat_survives_and_reaper_spares_healthy_run(self) -> None:
        """Periodic heartbeat keeps ownership across a stage longer than the lease.

        Real elapsed time (not a mocked clock). Mid-stage reaper on the same
        committed row must leave the healthy run alone. Does not claim SQLite
        multi-process locking semantics.
        """
        lease_seconds = 1
        heartbeat_seconds = 0.25
        stage_sleep = 2.2  # > lease; with heartbeats the lease stays live
        execution = self.enqueue_and_claim("worker-a", lease_seconds=lease_seconds)
        claimed = OperationExecution.objects.get(pk=execution.pk)
        claim_expiry = claimed.lease_expires_at
        real_timeline = executions.source_time.build_timeline
        reaper_counts: list[int] = []

        def slow_timeline(recording, *args, **kwargs):
            time.sleep(stage_sleep)
            # After a stage that outlived the original lease, a healthy heartbeat
            # must have extended lease_expires_at; the reaper must not fail it.
            row = OperationExecution.objects.get(pk=execution.pk)
            self.assertEqual(row.status, OperationExecution.RUNNING)
            self.assertEqual(row.lease_owner, "worker-a")
            self.assertGreater(row.lease_expires_at, timezone.now())
            self.assertGreater(row.lease_expires_at, claim_expiry)
            reaper_counts.append(executions.fail_abandoned_executions(lease_seconds))
            return real_timeline(recording, *args, **kwargs)

        started = time.monotonic()
        with patch.object(executions.source_time, "build_timeline", side_effect=slow_timeline):
            finished = executions.run_execution(
                execution,
                "worker-a",
                lease_seconds=lease_seconds,
                heartbeat_seconds=heartbeat_seconds,
            )
        elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, stage_sleep)
        self.assertEqual(reaper_counts, [0])
        self.assertEqual(finished.status, OperationExecution.SUCCEEDED, finished.error)
        self.assertEqual(finished.lease_owner, "")
        self.assertIsNone(finished.lease_expires_at)
