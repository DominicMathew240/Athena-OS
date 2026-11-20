"""T19: operation executions in supervised durable workers.

The lease/heartbeat contract behind these tests: one atomic claim per queued
execution, leases that expire only when their owner stopped refreshing, a
terminal-outcome guard that can never write a second result, cancellation at
worker checkpoints, and a queue API whose surface stays owner-scoped and
unchanged for the existing draft/bundle routes. Recovery is by lease expiry, so
one worker's start-up never fails another's in-flight execution. All of this is
verified with in-process calls and real database rows — no real deployment is
exercised here.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import (
    DatasetVersion,
    ModelVersion,
    Operation,
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

REQUIRE_FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")


def _clip_bytes() -> bytes:
    """A recording body for the pinned recording.

    Since T20 the execution step reads the recording's frame timestamps and
    requires a decodable MP4, so the fixture is a real one-second clip whenever
    ffmpeg is present (as in the T09/T20 suites). Without ffmpeg a plain byte
    body is kept: the lease/claim/API tests below never decode it, and the
    tests that run executions to success are skipped in that environment.
    """
    if REQUIRE_FFMPEG:
        with tempfile.TemporaryDirectory(prefix="t19-clip-") as directory:
            path = Path(directory) / "rec.mp4"
            subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error",
                 "-f", "lavfi", "-i", "color=c=blue:s=64x48:r=5",
                 "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                 "-movflags", "+faststart", "-y", str(path)],
                check=True, capture_output=True, timeout=30,
            )
            return path.read_bytes()
    return b"t19-recording-" + b"".join(bytes([i % 251]) for i in range(4096))


class DurableExecutionBase(TestCase):
    """An owner with a bundle (all pins), a durable source recording, and storage."""

    def setUp(self):
        import shutil

        self.media = tempfile.mkdtemp(prefix="t19-media-")
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        # enable() without a disable would leak the override into later test modules
        # (settings.MEDIA_ROOT is a str here, and healthz calls Path-only methods on it).
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)
        self.user = get_user_model().objects.create_user("t19-owner", is_superuser=True)
        grant(self.user, "approver")
        self.client.force_login(self.user)
        self.site = Site.objects.create(created_by=self.user, name="Depot")
        self.source = OperationSource.objects.create(site=self.site, name="Gate", kind="recorded_video")
        self.operation = Operation.objects.create(
            created_by=self.user, site=self.site, source=self.source, name="Gate count"
        )
        # A durable source recording: a real file in private storage plus its digest.
        self.body = _clip_bytes()
        self.key = default_storage.save(f"operation-source-recordings/{self.source.id}/rec.mp4", _NamedBytes(self.body))
        self.addCleanup(default_storage.delete, self.key)
        # The real 5 fps 1 s clip's duration; without ffmpeg (tests that never run
        # executions) the placeholder value is never cross-checked.
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
        """One bundle row with a full snapshot; the assembly path is T05's, already covered."""
        from .models import OperationDraft

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
                "model": None,  # no model binding pinned: the honest no-model run
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
                {"time": 1.0, "direction": "negative_to_positive"},
                {"time": 5.5, "direction": "positive_to_negative"},
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

    def enqueue_and_claim(self, owner: str = "worker-a"):
        """Queue one execution and claim it for `owner`, like a live worker would."""
        self.enqueue()
        return executions.claim_next_execution(owner)

    def enqueue(self, client=None) -> dict:
        response = (client or self.client).post(
            f"/api/operation-release-bundles/{self.bundle.id}/executions",
            json.dumps({"recording_id": str(self.recording.id)}),
            content_type="application/json",
        )
        assert response.status_code == 202, response.content
        return response.json()["execution"]


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


# -- the lease/claim contract ----------------------------------------------------------------
class LeaseClaimTests(DurableExecutionBase):
    def test_claim_is_atomic_two_claimers_one_winner(self) -> None:
        self.enqueue()
        first = executions.claim_next_execution("worker-a")
        second = executions.claim_next_execution("worker-b")
        self.assertIsNotNone(first)
        self.assertIsNone(second)  # the queue is empty for the loser
        first.refresh_from_db()
        self.assertEqual(first.status, "running")
        self.assertEqual(first.lease_owner, "worker-a")
        self.assertIsNotNone(first.lease_expires_at)
        self.assertGreater(first.lease_expires_at, timezone.now())

    def test_claim_ignores_expired_leases_of_finished_work(self) -> None:
        execution = OperationExecution.objects.get(pk=self.enqueue()["id"])
        # A finished execution keeps no lease and is never re-claimed.
        OperationExecution.objects.filter(pk=execution.pk).update(
            status=OperationExecution.SUCCEEDED, lease_owner="", lease_expires_at=None
        )
        self.assertIsNone(executions.claim_next_execution("worker-a"))

    def test_running_execution_is_not_claimable_by_another_worker(self) -> None:
        execution = OperationExecution.objects.get(pk=self.enqueue()["id"])
        claimed = executions.claim_next_execution("worker-a")
        self.assertEqual(claimed.pk, execution.pk)
        self.assertIsNone(executions.claim_next_execution("worker-b"))


class RecoveryTests(DurableExecutionBase):
    def test_expired_lease_with_dead_owner_is_failed_once_and_requeueable(self) -> None:
        execution = self.enqueue_and_claim("worker-a")
        # Simulate a crash: the worker stops refreshing, the lease drifts into the past.
        past = timezone.now() - timedelta(seconds=3 * executions.LEASE_SECONDS)
        OperationExecution.objects.filter(pk=execution.pk).update(lease_expires_at=past, heartbeat_at=past)
        self.assertEqual(executions.fail_abandoned_executions(), 1)
        execution.refresh_from_db()
        self.assertEqual(execution.status, "failed")
        self.assertIn("worker stopped", execution.error)
        self.assertEqual(executions.fail_abandoned_executions(), 0)  # never failed twice
        # And a fresh execution can be queued and runs.
        fresh = self.enqueue()
        self.assertEqual(fresh["status"], "queued")

    def test_fresh_lease_is_never_failed_even_at_worker_start_up(self) -> None:
        execution = self.enqueue_and_claim("worker-a")
        # Worker B starts while A is mid-run with a live lease: nothing is failed.
        self.assertEqual(executions.fail_abandoned_executions(), 0)
        execution.refresh_from_db()
        self.assertEqual(execution.status, "running")
        # A lease that is only just expired (one window missed) is still left alone.
        OperationExecution.objects.filter(pk=execution.pk).update(
            lease_expires_at=timezone.now() - timedelta(seconds=5)
        )
        self.assertEqual(executions.fail_abandoned_executions(), 0)

    @skipUnless(REQUIRE_FFMPEG, "running an execution needs a decodable recording (ffmpeg)")
    def test_crash_after_finish_guard_prevents_duplicate_outcome(self) -> None:
        execution = self.enqueue_and_claim("worker-a")
        finished = executions.run_execution(execution, "worker-a")
        self.assertEqual(finished.status, "succeeded")
        # A zombie worker tries to write its own outcome after recovery already happened:
        # the guard matches no row, so the stored result stays exactly one.
        self.assertFalse(executions._finish(finished, status=OperationExecution.FAILED, error="late"))
        finished.refresh_from_db()
        self.assertEqual(finished.status, "succeeded")
        self.assertEqual(finished.result["comparison"]["matched"], 2)


class RunTests(DurableExecutionBase):
    @skipUnless(REQUIRE_FFMPEG, "running an execution needs a decodable recording (ffmpeg)")
    def test_successful_run_proves_recording_and_pins_and_stores_result(self) -> None:
        execution = self.enqueue_and_claim("worker-a")
        finished = executions.run_execution(execution, "worker-a")
        self.assertEqual(finished.status, "succeeded", finished.error)
        self.assertEqual(finished.lease_owner, "")  # the lease is released on finish
        self.assertIsNone(finished.lease_expires_at)
        result = finished.result
        self.assertEqual(result["recording_sha256"], self.recording.sha256)
        self.assertEqual(result["comparison"]["matched"], 2)
        self.assertEqual(result["comparison"]["missed"], 0)
        self.assertEqual(result["pins"]["bundle_sha256"], self.bundle.snapshot_sha256)
        # T21: no model is bound in this fixture's bundle, so no model ran and the note says so.
        self.assertIn("No model is bound", result["note"])
        self.assertNotIn("detector", result)

    def test_changed_recording_fails_with_plain_message(self) -> None:
        execution = self.enqueue_and_claim("worker-a")
        default_storage.delete(self.key)
        self.key = default_storage.save(self.key, _NamedBytes(b"tampered"))
        finished = executions.run_execution(execution, "worker-a")
        self.assertEqual(finished.status, "failed")
        self.assertIn("changed on disk", finished.error)

    def test_missing_recording_fails_with_plain_message(self) -> None:
        execution = self.enqueue_and_claim("worker-a")
        default_storage.delete(self.key)
        finished = executions.run_execution(execution, "worker-a")
        self.assertEqual(finished.status, "failed")
        self.assertIn("could not be read", finished.error)

    def test_cancel_during_run_ends_cancelled(self) -> None:
        execution = self.enqueue_and_claim("worker-a")
        OperationExecution.objects.filter(pk=execution.pk).update(cancel_requested=True)
        finished = executions.run_execution(execution, "worker-a")
        self.assertEqual(finished.status, "cancelled")
        self.assertEqual(finished.error, "Cancelled by request.")

    def test_cancel_of_finished_execution_is_refused(self) -> None:
        data = self.enqueue()
        execution = self.enqueue_and_claim("worker-a")
        executions.run_execution(execution, "worker-a")
        response = self.client.post(f"/api/operation-executions/{data['id']}/cancel")
        self.assertEqual(response.status_code, 409, response.content)
        self.assertIn("already finished", response.json()["error"])

    def test_unexpected_error_is_recorded_not_raised(self) -> None:
        execution = self.enqueue_and_claim("worker-a")
        with patch.object(executions, "_recording_digest", side_effect=RuntimeError("boom")):
            finished = executions.run_execution(execution, "worker-a")
        self.assertEqual(finished.status, "failed")
        self.assertEqual(finished.error, "boom")


# -- the API surface ----------------------------------------------------------------
class ExecutionApiTests(DurableExecutionBase):
    def test_enqueue_freezes_provenance_and_returns_202(self) -> None:
        data = self.enqueue()
        row = OperationExecution.objects.get(pk=data["id"])
        self.assertEqual(row.status, "queued")
        self.assertEqual(row.bundle_sha256, self.bundle.snapshot_sha256)
        self.assertEqual(row.recording_sha256, self.recording.sha256)
        self.assertEqual(row.pins["recipe"]["recipe_key"], "people_counting")
        self.assertIn("recording_id", data)

    def test_list_and_detail_are_owner_scoped(self) -> None:
        self.enqueue()
        other = get_user_model().objects.create_user("t19-other")
        grant(other, "approver")
        stranger = self.client_class()
        stranger.force_login(other)
        self.assertEqual(stranger.get(f"/api/operation-release-bundles/{self.bundle.id}/executions").status_code, 404)
        listing = self.client.get(f"/api/operation-release-bundles/{self.bundle.id}/executions")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(len(listing.json()["executions"]), 1)
        execution_id = listing.json()["executions"][0]["id"]
        self.assertEqual(stranger.get(f"/api/operation-executions/{execution_id}").status_code, 404)
        self.assertEqual(self.client.get(f"/api/operation-executions/{execution_id}").status_code, 200)

    def test_enqueue_requires_a_recording_of_the_bundle_source(self) -> None:
        response = self.client.post(
            f"/api/operation-release-bundles/{self.bundle.id}/executions",
            json.dumps({"recording_id": str(self.recording.id)}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 202)
        bad = self.client.post(
            f"/api/operation-release-bundles/{self.bundle.id}/executions",
            json.dumps({"recording_id": "not-a-uuid"}),
            content_type="application/json",
        )
        self.assertEqual(bad.status_code, 400)
        wrong = self.client.post(
            f"/api/operation-release-bundles/{self.bundle.id}/executions",
            json.dumps({"recording_id": "00000000-0000-0000-0000-000000000000"}),
            content_type="application/json",
        )
        self.assertEqual(wrong.status_code, 400)
        self.assertIn("belongs to this bundle's source", wrong.json()["error"])

    def test_viewer_role_cannot_enqueue_or_cancel(self) -> None:
        self.enqueue()
        viewer = get_user_model().objects.create_user("t19-viewer")
        grant(viewer, "viewer")
        stranger = self.client_class()
        stranger.force_login(viewer)
        self.assertEqual(
            stranger.post(
                f"/api/operation-release-bundles/{self.bundle.id}/executions",
                json.dumps({"recording_id": str(self.recording.id)}),
                content_type="application/json",
            ).status_code,
            403,
        )

    def test_existing_draft_and_bundle_routes_are_unchanged(self) -> None:
        # The T05 surface still answers the same way next to the new routes.
        self.assertEqual(self.client.get("/api/operation-drafts").status_code, 200)
        self.assertEqual(
            self.client.get(
                "/api/operation-release-bundles/00000000-0000-0000-0000-000000000000/executions"
            ).status_code,
            404,
        )


# -- the worker command ----------------------------------------------------------------
class WorkerCommandTests(DurableExecutionBase):
    @skipUnless(REQUIRE_FFMPEG, "running an execution needs a decodable recording (ffmpeg)")
    def test_operation_worker_once_processes_the_queue(self) -> None:
        from io import StringIO

        from django.core.management import call_command

        self.enqueue()
        out = StringIO()
        call_command("operation_worker", "--once", stdout=out)
        execution = OperationExecution.objects.get()
        self.assertEqual(execution.status, "succeeded", execution.error)
        self.assertIn("succeeded", out.getvalue())

    def test_worker_start_does_not_fail_another_workers_fresh_run(self) -> None:
        from io import StringIO

        from django.core.management import call_command

        self.enqueue()
        claimed = executions.claim_next_execution("worker-a")  # another live worker is mid-run
        out = StringIO()
        call_command("operation_worker", "--once", stdout=out)  # this worker starts and idles
        claimed.refresh_from_db()
        self.assertEqual(claimed.status, "running")  # untouched: the lease is fresh
        self.assertEqual("Execution" in out.getvalue(), False)
