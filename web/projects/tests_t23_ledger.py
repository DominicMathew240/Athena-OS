"""T23: the durable operation event ledger and its duplicate prevention.

The contracts under test: an event's identity is content-addressed (SHA-256
over execution id, source, kind, session, timestamp and canonical payload
JSON), so a retry or replay of the same execution can never double-commit;
a *different* payload under the same event slot (same execution, source,
kind, session, timestamp) is refused loudly, never silently merged; the
pinned versions (bundle digest, recording digest, geometry digest) are
plain queryable columns; and aggregates are nothing but a function of raw
events — a rebuild from the rows equals the recorded aggregate. The rows
themselves are append-only: no edit, no delete, no bulk update.

Ledger units are pure database rows (no video needed); the worker
integration reuses the T19 fixture so a real run commits its reference
events exactly once, and a replay after the outcome commits nothing new.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import (
    Operation,
    OperationDraft,
    OperationEvent,
    OperationExecution,
    OperationReleaseBundle,
    OperationSource,
    OperationSourceRecording,
    Site,
)
from .operations import executions, ledger
from .operations.ledger import ConflictingEventError, EventDraft
from .tests import grant
from .tests_t19_durable import DurableExecutionBase, REQUIRE_FFMPEG, _NamedBytes

from django.core.files.storage import default_storage


def draft(kind=OperationEvent.KIND_REFERENCE, source="recording:x", session=1, timestamp=1.0, **payload):
    """One valid event draft; extra keywords become the payload (a crossing direction)."""
    body = {"direction": "negative_to_positive"} | payload
    return EventDraft(kind=kind, source=source, session=session, timestamp=timestamp, payload=body)


class LedgerBase(TestCase):
    """An owner with one claimed execution to commit events under (no video needed)."""

    def setUp(self):
        self.user = get_user_model().objects.create_user("t23-owner", is_superuser=True)
        self.site = Site.objects.create(created_by=self.user, name="Depot")
        self.source = OperationSource.objects.create(site=self.site, name="Gate", kind="recorded_video")
        self.operation = Operation.objects.create(
            created_by=self.user, site=self.site, source=self.source, name="Gate count"
        )
        self.recording = OperationSourceRecording.objects.create(
            source=self.source,
            created_by=self.user,
            storage_key="operation-source-recordings/x/rec.mp4",
            filename="rec.mp4",
            sha256="a" * 64,
            size_bytes=10,
            width=64,
            height=48,
            duration_seconds=60.0,
        )
        draft_row = OperationDraft.objects.create(created_by=self.user, operation=self.operation, name="Gate count")
        snapshot = {
            "kind": "operation_release_bundle",
            "component_pins": {
                "source_view": {"id": "sv", "version": 1, "sha256": "1" * 64},
                "geometry": {"id": "geo", "version": 3, "sha256": "2" * 64},
                "recipe": {"recipe_key": "people_counting", "sha256": "3" * 64},
                "model": None,
                "screen": {"sha256": "5" * 64},
                "policy": {"sha256": "6" * 64},
            },
            "reference_events": [],
        }
        encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        self.bundle = OperationReleaseBundle.objects.create(
            operation=self.operation,
            draft=draft_row,
            draft_revision=1,
            number=1,
            created_by=self.user,
            snapshot=snapshot,
            snapshot_sha256=hashlib.sha256(encoded.encode()).hexdigest(),
        )
        self.execution = OperationExecution.objects.create(
            bundle=self.bundle,
            recording=self.recording,
            created_by=self.user,
            bundle_sha256=self.bundle.snapshot_sha256,
            recording_sha256=self.recording.sha256,
            pins=snapshot["component_pins"],
        )


# -- identity -----------------------------------------------------------------------------------
class IdentityTests(LedgerBase):
    def test_identity_is_content_addressed_and_stable(self):
        first = ledger.event_identity(str(self.execution.pk), draft())
        second = ledger.event_identity(str(self.execution.pk), draft())
        self.assertEqual(first, second)  # same content, same identity
        self.assertEqual(len(first[0]), 64)
        self.assertEqual(first[1], hashlib.sha256(
            json.dumps(draft().payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest())

    def test_identity_changes_with_every_identity_input(self):
        base = draft()
        changes = [
            draft(source="recording:other"),
            draft(timestamp=1.000001),
            draft(session=2),
            draft(payload={"direction": "positive_to_negative"}),
        ]
        baseline = ledger.event_identity(str(self.execution.pk), base)[0]
        for changed in changes:
            self.assertNotEqual(
                ledger.event_identity(str(self.execution.pk), changed)[0], baseline
            )
        # The same event under a DIFFERENT execution is a different row, by design.
        other = OperationExecution.objects.create(
            bundle=self.bundle,
            recording=self.recording,
            created_by=self.user,
            bundle_sha256=self.bundle.snapshot_sha256,
            recording_sha256=self.recording.sha256,
            pins={},
        )
        self.assertNotEqual(
            ledger.event_identity(str(other.pk), base)[0],
            ledger.event_identity(str(self.execution.pk), base)[0],
        )

    def test_timestamps_are_quantized_so_float_noise_cannot_split_identity(self):
        near_one = ledger.event_identity(str(self.execution.pk), draft(timestamp=1.0000000001))[0]
        exactly_one = ledger.event_identity(str(self.execution.pk), draft(timestamp=1.0))[0]
        self.assertEqual(near_one, exactly_one)

    def test_bad_drafts_are_refused_before_anything_commits(self):
        bad_payload = dict(direction="cross", score=float("inf"))  # inf is not canonical JSON
        cases = (
            dict(kind="invented_kind"),
            dict(source=""),
            dict(session=-1),
            dict(timestamp=-1.0),
            dict(timestamp="soon"),
            dict(payload=bad_payload),
        )
        for case in cases:
            kwargs = dict(
                kind=OperationEvent.KIND_REFERENCE,
                source="recording:x",
                session=1,
                timestamp=1.0,
                payload={},
            ) | case
            with self.assertRaises(ledger.LedgerError):
                ledger.commit_events(self.execution, [EventDraft(**kwargs)])
        self.assertEqual(OperationEvent.objects.count(), 0)


# -- commit: idempotency and conflict refusal ----------------------------------------------------
class CommitTests(LedgerBase):
    def test_commit_returns_rows_and_stores_canonical_content(self):
        committed = ledger.commit_events(self.execution, [draft(timestamp=2.0), draft(timestamp=5.5, session=2)])
        self.assertEqual(len(committed), 2)
        stored = OperationEvent.objects.order_by("timestamp")
        self.assertEqual([row.timestamp for row in stored], [f"{2_000_000:020d}", f"{5_500_000:020d}"])
        self.assertEqual(
            [row.kind for row in stored], [OperationEvent.KIND_REFERENCE, OperationEvent.KIND_REFERENCE]
        )
        self.assertEqual(stored[0].payload, {"direction": "negative_to_positive"})

    def test_duplicate_commit_same_identity_twice_is_idempotent(self):
        first = ledger.commit_events(self.execution, [draft()])
        second = ledger.commit_events(self.execution, [draft()])
        self.assertEqual(first[0].pk, second[0].pk)  # the same row, not a second one
        self.assertEqual(OperationEvent.objects.count(), 1)

    def test_retry_after_crash_replays_the_whole_batch_without_duplicates(self):
        # The crash scenario: the batch committed, the outcome never did, a retry
        # replays everything. The ledger accepts the replay and stays exact.
        batch = [draft(timestamp=1.0), draft(timestamp=2.0), draft(timestamp=3.0)]
        ledger.commit_events(self.execution, batch)
        replay = ledger.commit_events(self.execution, batch)
        self.assertEqual(len(replay), 3)
        self.assertEqual(OperationEvent.objects.count(), 3)

    def test_conflicting_payload_under_the_same_slot_is_refused_loudly(self):
        original = draft(direction="negative_to_positive")
        ledger.commit_events(self.execution, [original])
        with self.assertRaises(ConflictingEventError) as raised:
            ledger.commit_events(self.execution, [draft(direction="positive_to_negative")])
        self.assertIn("never merges conflicting events", str(raised.exception))
        # The original event is untouched, and nothing else committed.
        row = OperationEvent.objects.get()
        self.assertEqual(row.payload, {"direction": "negative_to_positive"})
        self.assertEqual(row.payload_sha256, ledger.event_identity(str(self.execution.pk), original)[1])

    def test_conflicting_batch_is_refused_whole_no_partial_commit(self):
        # The slot's owner commits first; a later batch that conflicts on that
        # slot and would also commit a good event is refused whole.
        ledger.commit_events(self.execution, [draft(timestamp=1.0)])
        with self.assertRaises(ConflictingEventError):
            ledger.commit_events(
                self.execution,
                [
                    draft(timestamp=1.0, direction="positive_to_negative"),  # conflicts with the owner
                    draft(timestamp=9.0),  # would be fine on its own
                ],
            )
        self.assertEqual(OperationEvent.objects.count(), 1)

    def test_same_timestamp_different_session_or_source_is_not_a_conflict(self):
        ledger.commit_events(
            self.execution,
            [
                draft(session=1, timestamp=4.0, source="recording:a"),
                draft(session=2, timestamp=4.0, source="recording:a"),
                draft(session=1, timestamp=4.0, source="recording:b"),
            ],
        )
        self.assertEqual(OperationEvent.objects.count(), 3)

    def test_rows_are_append_only(self):
        ledger.commit_events(self.execution, [draft()])
        row = OperationEvent.objects.get()
        with self.assertRaises(ValueError):
            row.payload = {"direction": "positive_to_negative"}
            row.save()
        with self.assertRaises(ValueError):
            row.delete()
        with self.assertRaises(ValueError):
            OperationEvent.objects.all().update(kind=OperationEvent.KIND_DETECTOR)
        with self.assertRaises(ValueError):
            OperationEvent.objects.all().delete()


# -- pinned-version queryability ------------------------------------------------------------------
class PinnedVersionTests(LedgerBase):
    def test_event_rows_carry_the_execution_pinned_versions(self):
        ledger.commit_events(self.execution, [draft()])
        row = OperationEvent.objects.get()
        self.assertEqual(row.bundle_sha256, self.bundle.snapshot_sha256)
        self.assertEqual(row.recording_sha256, self.recording.sha256)
        self.assertEqual(row.geometry_sha256, "2" * 64)  # from the frozen bundle pins

    def test_identity_and_pinned_versions_are_queryable(self):
        target = draft(timestamp=7.0)
        ledger.commit_events(self.execution, [target, draft(timestamp=8.0, session=2)])
        identity = ledger.event_identity(str(self.execution.pk), target)[0]
        # By identity, within the execution.
        found = OperationEvent.objects.filter(execution=self.execution, identity_sha256=identity).get()
        self.assertEqual(found.timestamp, f"{7_000_000:020d}")
        # By pinned versions + kind, without touching the execution row.
        self.assertEqual(
            OperationEvent.objects.filter(
                recording_sha256=self.recording.sha256,
                bundle_sha256=self.bundle.snapshot_sha256,
                geometry_sha256="2" * 64,
                kind=OperationEvent.KIND_REFERENCE,
            ).count(),
            2,
        )
        # And the execution's events are reachable through the relation.
        self.assertEqual(self.execution.events.count(), 2)

    def test_events_pin_the_versions_the_execution_froze_not_current_ones(self):
        ledger.commit_events(self.execution, [draft()])
        # A later bundle with different pins (bundles are append-only, so this is
        # a NEW bundle row) changes nothing about the committed events.
        later_pins = dict(self.bundle.snapshot["component_pins"])
        later_pins["geometry"] = {"version": 4, "sha256": "9" * 64}
        later = dict(self.bundle.snapshot, component_pins=later_pins)
        encoded = json.dumps(later, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        OperationReleaseBundle.objects.create(
            operation=self.operation,
            draft=self.bundle.draft,
            draft_revision=1,
            number=2,
            created_by=self.user,
            snapshot=later,
            snapshot_sha256=hashlib.sha256(encoded.encode()).hexdigest(),
        )
        row = OperationEvent.objects.get()
        self.assertEqual(row.geometry_sha256, "2" * 64)
        self.assertEqual(row.bundle_sha256, self.bundle.snapshot_sha256)


# -- raw events rebuild aggregates -----------------------------------------------------------------
class AggregateTests(LedgerBase):
    def test_rebuild_from_raw_events_equals_the_recorded_aggregate(self):
        drafts = [
            draft(timestamp=1.0, session=1),
            draft(timestamp=2.5, session=1),
            draft(timestamp=2.5, session=2, payload={"direction": "positive_to_negative"}),
        ]
        committed = ledger.commit_events(self.execution, drafts)
        recorded = ledger.rebuild_aggregates(committed).summary()
        # The rebuild uses ONLY the raw rows: re-query them from the database.
        raw = list(OperationEvent.objects.filter(execution=self.execution).order_by("timestamp"))
        rebuilt = ledger.rebuild_aggregates(raw)
        self.assertEqual(rebuilt.summary(), recorded)
        self.assertEqual(rebuilt.event_count, 3)
        self.assertEqual(rebuilt.counts_by_kind, {OperationEvent.KIND_REFERENCE: 3})
        self.assertEqual(rebuilt.counts_by_session, {"1": 2, "2": 1})
        self.assertEqual(rebuilt.first_timestamp, f"{1_000_000:020d}")
        self.assertEqual(rebuilt.last_timestamp, f"{2_500_000:020d}")
        self.assertEqual(len(rebuilt.identity_sha256s), 3)
        # Rebuilding from nothing is empty, not an error.
        empty = ledger.rebuild_aggregates([])
        self.assertEqual(
            empty.summary(),
            {"event_count": 0, "counts_by_kind": {}, "counts_by_session": {}, "first_timestamp": None, "last_timestamp": None},
        )

    def test_rebuild_does_not_count_duplicates_because_there_are_none(self):
        batch = [draft(timestamp=t) for t in (0.5, 1.0, 1.5)]
        ledger.commit_events(self.execution, batch)
        ledger.commit_events(self.execution, batch)  # the retry
        ledger.commit_events(self.execution, batch)  # and another
        self.assertEqual(OperationEvent.objects.count(), 3)
        self.assertEqual(ledger.rebuild_aggregates(OperationEvent.objects.all()).event_count, 3)


# -- worker integration ---------------------------------------------------------------------------
class WorkerIntegrationTests(DurableExecutionBase):
    @skipUnless(REQUIRE_FFMPEG, "running an execution needs a decodable recording (ffmpeg)")
    def test_successful_run_commits_reference_events_once_with_pinned_versions(self):
        execution = self.enqueue_and_claim("worker-a")
        finished = executions.run_execution(execution, "worker-a")
        self.assertEqual(finished.status, "succeeded", finished.error)
        events = list(OperationEvent.objects.filter(execution=finished))
        self.assertEqual(len(events), 2)  # the fixture's two reference events
        self.assertEqual({row.kind for row in events}, {OperationEvent.KIND_REFERENCE})
        self.assertEqual({row.source for row in events}, {f"recording:{self.recording.id}"})
        for row in events:
            self.assertEqual(row.bundle_sha256, self.bundle.snapshot_sha256)
            self.assertEqual(row.recording_sha256, self.recording.sha256)
        # The outcome's ledger aggregate is exactly the rebuild from the raw rows.
        rebuilt = ledger.rebuild_aggregates(events).summary()
        self.assertEqual(finished.result["event_ledger"], rebuilt)
        self.assertEqual(rebuilt["event_count"], 2)
        self.assertEqual(rebuilt["counts_by_kind"], {OperationEvent.KIND_REFERENCE: 2})

    @skipUnless(REQUIRE_FFMPEG, "running an execution needs a decodable recording (ffmpeg)")
    def test_replaying_the_reference_events_after_the_outcome_commits_nothing_new(self):
        execution = self.enqueue_and_claim("worker-a")
        finished = executions.run_execution(execution, "worker-a")
        self.assertEqual(finished.status, "succeeded")
        before = OperationEvent.objects.filter(execution=finished).count()
        # A zombie worker replays the same commit path after the outcome: the
        # ledger is idempotent, so the replay returns the existing rows.
        reference = executions._reference_events(self.bundle.snapshot)
        replayed = ledger.commit_events(
            finished,
            [
                ledger.EventDraft(
                    kind=OperationEvent.KIND_REFERENCE,
                    source=executions._ledger_source(finished),
                    session=0,
                    timestamp=crossing.time,
                    payload={"direction": crossing.direction, "tolerance_seconds": 0.5},
                )
                for crossing in reference
            ],
        )
        self.assertEqual(len(replayed), before)
        self.assertEqual(OperationEvent.objects.filter(execution=finished).count(), before)

    @skipUnless(REQUIRE_FFMPEG, "running an execution needs a decodable recording (ffmpeg)")
    def test_two_executions_of_the_same_bundle_each_commit_their_own_events(self):
        first = self.enqueue_and_claim("worker-a")
        executions.run_execution(first, "worker-a")
        second = self.enqueue_and_claim("worker-b")
        finished = executions.run_execution(second, "worker-b")
        self.assertEqual(finished.status, "succeeded", finished.error)
        self.assertEqual(OperationEvent.objects.count(), 4)  # two events per execution
        self.assertEqual(OperationEvent.objects.filter(execution=first).count(), 2)
        self.assertEqual(OperationEvent.objects.filter(execution=finished).count(), 2)

    def test_failed_run_commits_no_events(self):
        # Tamper with the pinned recording after enqueue: the run fails at the
        # digest check, before any event is committed.
        execution = self.enqueue_and_claim("worker-a")
        default_storage.delete(self.key)
        self.key = default_storage.save(self.key, _NamedBytes(b"tampered"))
        finished = executions.run_execution(execution, "worker-a")
        self.assertEqual(finished.status, "failed")
        self.assertIn("changed on disk", finished.error)
        self.assertEqual(OperationEvent.objects.count(), 0)
