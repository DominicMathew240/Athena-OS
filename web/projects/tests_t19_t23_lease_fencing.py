"""T19/T23 lease fencing: owner + live-lease gates on worker side effects.

Strict lease policy under test: heartbeat must not resurrect an expired lease;
worker-side reference ledger inserts and terminal finishes require a locked
RUNNING row owned by the caller with a still-live lease; abandoned recovery
re-checks the stale deadline in its UPDATE; a stale/wrong-owner worker aborts
without writing a SUCCEEDED result (and without silently skipping required
ledger evidence). Direct ``ledger.commit_events`` stays ungated for
idempotency unit tests and post-success identity replay.

These are in-process sequential races — SQLite ``select_for_update`` is not a
real multi-process row lock, so nothing here claims multi-process concurrency.
"""

from __future__ import annotations

from datetime import timedelta
from unittest import skipUnless

from django.utils import timezone

from .models import OperationEvent, OperationExecution
from .operations import executions, ledger
from .tests_t19_durable import DurableExecutionBase, REQUIRE_FFMPEG


class LeaseFencingTests(DurableExecutionBase):
    def test_beat_does_not_resurrect_expired_lease(self) -> None:
        execution = self.enqueue_and_claim("worker-a")
        past = timezone.now() - timedelta(seconds=1)
        OperationExecution.objects.filter(pk=execution.pk).update(lease_expires_at=past)
        execution.refresh_from_db()
        before = execution.lease_expires_at
        self.assertFalse(executions._beat(execution, "worker-a", executions.LEASE_SECONDS))
        execution.refresh_from_db()
        self.assertEqual(execution.lease_expires_at, before)
        self.assertEqual(execution.lease_owner, "worker-a")
        self.assertEqual(execution.status, OperationExecution.RUNNING)

    def test_wrong_owner_finish_owned_leaves_running(self) -> None:
        execution = self.enqueue_and_claim("worker-a")
        self.assertFalse(
            executions._finish_owned(
                execution,
                "worker-b",
                status=OperationExecution.SUCCEEDED,
                result={"kind": "stolen"},
            )
        )
        execution.refresh_from_db()
        self.assertEqual(execution.status, OperationExecution.RUNNING)
        self.assertEqual(execution.lease_owner, "worker-a")
        self.assertEqual(execution.result, {})

    def test_expired_owner_finish_owned_leaves_running(self) -> None:
        execution = self.enqueue_and_claim("worker-a")
        past = timezone.now() - timedelta(seconds=1)
        OperationExecution.objects.filter(pk=execution.pk).update(lease_expires_at=past)
        self.assertFalse(
            executions._finish_owned(
                execution,
                "worker-a",
                status=OperationExecution.SUCCEEDED,
                result={"kind": "stale"},
            )
        )
        execution.refresh_from_db()
        self.assertEqual(execution.status, OperationExecution.RUNNING)
        self.assertEqual(execution.result, {})

    def test_abandoned_finish_rechecks_deadline_after_renewal(self) -> None:
        """Reaper UPDATE must lose cleanly if the lease was extended after select.

        Simulates concurrent renewal by writing a fresh lease_expires_at between
        discovery and the abandoned UPDATE (not via expired heartbeat resurrection).
        """
        execution = self.enqueue_and_claim("worker-a")
        now = timezone.now()
        past = now - timedelta(seconds=3 * executions.LEASE_SECONDS)
        OperationExecution.objects.filter(pk=execution.pk).update(
            lease_expires_at=past, heartbeat_at=past
        )
        deadline = now - timedelta(seconds=2 * executions.LEASE_SECONDS)
        # Concurrent "renewal" extends the lease past the reaper deadline.
        OperationExecution.objects.filter(pk=execution.pk).update(
            lease_expires_at=now + timedelta(seconds=executions.LEASE_SECONDS),
            heartbeat_at=now,
        )
        self.assertFalse(executions._finish_abandoned(execution, deadline=deadline))
        execution.refresh_from_db()
        self.assertEqual(execution.status, OperationExecution.RUNNING)
        self.assertEqual(executions.fail_abandoned_executions(), 0)

    def test_reaper_then_late_owned_finish_cannot_overwrite(self) -> None:
        execution = self.enqueue_and_claim("worker-a")
        past = timezone.now() - timedelta(seconds=3 * executions.LEASE_SECONDS)
        OperationExecution.objects.filter(pk=execution.pk).update(
            lease_expires_at=past, heartbeat_at=past
        )
        self.assertEqual(executions.fail_abandoned_executions(), 1)
        execution.refresh_from_db()
        abandoned_error = execution.error
        self.assertFalse(
            executions._finish_owned(
                execution,
                "worker-a",
                status=OperationExecution.SUCCEEDED,
                result={"kind": "late"},
            )
        )
        execution.refresh_from_db()
        self.assertEqual(execution.status, OperationExecution.FAILED)
        self.assertEqual(execution.error, abandoned_error)
        self.assertEqual(execution.result, {})

    def test_wrong_owner_cannot_commit_reference_events(self) -> None:
        execution = self.enqueue_and_claim("worker-a")
        drafts = [
            ledger.EventDraft(
                kind=OperationEvent.KIND_REFERENCE,
                source=f"recording:{self.recording.id}",
                session=0,
                timestamp=1.0,
                payload={"direction": "negative_to_positive", "tolerance_seconds": 0.5},
            )
        ]
        with self.assertRaises(executions.LeaseLost):
            executions.commit_reference_events_owned(execution, "worker-b", drafts)
        self.assertEqual(OperationEvent.objects.filter(execution=execution).count(), 0)

    def test_expired_lease_cannot_commit_reference_events(self) -> None:
        execution = self.enqueue_and_claim("worker-a")
        past = timezone.now() - timedelta(seconds=1)
        OperationExecution.objects.filter(pk=execution.pk).update(lease_expires_at=past)
        drafts = [
            ledger.EventDraft(
                kind=OperationEvent.KIND_REFERENCE,
                source=f"recording:{self.recording.id}",
                session=0,
                timestamp=1.0,
                payload={"direction": "negative_to_positive", "tolerance_seconds": 0.5},
            )
        ]
        with self.assertRaises(executions.LeaseLost):
            executions.commit_reference_events_owned(execution, "worker-a", drafts)
        self.assertEqual(OperationEvent.objects.filter(execution=execution).count(), 0)

    @skipUnless(REQUIRE_FFMPEG, "running an execution needs a decodable recording (ffmpeg)")
    def test_wrong_owner_run_cannot_insert_ledger_or_finish(self) -> None:
        execution = self.enqueue_and_claim("worker-a")
        future = timezone.now() + timedelta(seconds=executions.LEASE_SECONDS)
        OperationExecution.objects.filter(pk=execution.pk).update(
            lease_owner="worker-b",
            lease_expires_at=future,
            heartbeat_at=timezone.now(),
        )
        finished = executions.run_execution(execution, "worker-a")
        self.assertEqual(finished.status, OperationExecution.RUNNING)
        self.assertEqual(finished.lease_owner, "worker-b")
        self.assertEqual(finished.result, {})
        self.assertEqual(finished.error, "")
        self.assertEqual(OperationEvent.objects.filter(execution=finished).count(), 0)

    @skipUnless(REQUIRE_FFMPEG, "running an execution needs a decodable recording (ffmpeg)")
    def test_expired_lease_run_aborts_without_succeeded_or_ledger(self) -> None:
        execution = self.enqueue_and_claim("worker-a")
        past = timezone.now() - timedelta(seconds=1)
        OperationExecution.objects.filter(pk=execution.pk).update(lease_expires_at=past)
        finished = executions.run_execution(execution, "worker-a")
        self.assertEqual(finished.status, OperationExecution.RUNNING)
        self.assertEqual(finished.lease_owner, "worker-a")
        self.assertEqual(finished.result, {})
        self.assertEqual(finished.error, "")
        self.assertEqual(OperationEvent.objects.filter(execution=finished).count(), 0)
        self.assertEqual(finished.progress_checkpoint, {})

    @skipUnless(REQUIRE_FFMPEG, "running an execution needs a decodable recording (ffmpeg)")
    def test_cancellation_wrong_owner_cannot_finish(self) -> None:
        execution = self.enqueue_and_claim("worker-a")
        future = timezone.now() + timedelta(seconds=executions.LEASE_SECONDS)
        OperationExecution.objects.filter(pk=execution.pk).update(
            lease_owner="worker-b",
            lease_expires_at=future,
            heartbeat_at=timezone.now(),
            cancel_requested=True,
        )
        finished = executions.run_execution(execution, "worker-a")
        self.assertEqual(finished.status, OperationExecution.RUNNING)
        self.assertEqual(finished.lease_owner, "worker-b")
        self.assertTrue(finished.cancel_requested)
        self.assertEqual(finished.result, {})
        self.assertNotEqual(finished.status, OperationExecution.CANCELLED)

    @skipUnless(REQUIRE_FFMPEG, "running an execution needs a decodable recording (ffmpeg)")
    def test_live_owner_still_commits_ledger_and_succeeds(self) -> None:
        execution = self.enqueue_and_claim("worker-a")
        finished = executions.run_execution(execution, "worker-a")
        self.assertEqual(finished.status, OperationExecution.SUCCEEDED, finished.error)
        self.assertEqual(OperationEvent.objects.filter(execution=finished).count(), 2)
        self.assertEqual(finished.result["event_ledger"]["event_count"], 2)

    def test_direct_ledger_commit_events_remains_ungated(self) -> None:
        """Preserve the direct idempotency API used by unit tests / post-success replay."""
        execution = self.enqueue_and_claim("worker-a")
        # Even without a live-owner gate, direct commit_events still works (T23 unit path).
        past = timezone.now() - timedelta(seconds=1)
        OperationExecution.objects.filter(pk=execution.pk).update(lease_expires_at=past)
        committed = ledger.commit_events(
            execution,
            [
                ledger.EventDraft(
                    kind=OperationEvent.KIND_REFERENCE,
                    source=f"recording:{self.recording.id}",
                    session=0,
                    timestamp=1.0,
                    payload={"direction": "negative_to_positive", "tolerance_seconds": 0.5},
                )
            ],
        )
        self.assertEqual(len(committed), 1)
        self.assertEqual(OperationEvent.objects.filter(execution=execution).count(), 1)
