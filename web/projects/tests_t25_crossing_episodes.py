"""T25 (bounded slice): directional crossing episodes on finite lines.

Contracts under test: tracks feed a finite directed line; a valid side-to-side
episode emits direction and time exactly once; a reverse return counts; jitter,
boundary touches, line-parallel motion and long ambiguous gaps never fabricate
events. Detector crossing commits on the worker path require the existing
live-lease ownership fence (T19/T23) — wrong owner or an expired lease inserts
nothing and never pretends SUCCEEDED. Direct ``ledger.commit_events`` stays
ungated for unit idempotency. This is the foundation for T25, not a claim that
live counting or operator boards are complete.
"""

from __future__ import annotations

from datetime import timedelta
from unittest import skipUnless

from django.test import SimpleTestCase
from django.utils import timezone

from .models import OperationEvent, OperationExecution
from .operations import crossings, executions, ledger
from .operations.tracking import Track, TrackObservation
from .tests_t19_durable import DurableExecutionBase, REQUIRE_FFMPEG


def _obs(pts: float, x: float, y: float, *, size: float = 0.1, frame_index: int | None = None) -> TrackObservation:
    """One track observation whose box center is at (x, y)."""
    half = size / 2.0
    idx = frame_index if frame_index is not None else int(round(pts * 10))
    return TrackObservation(
        frame_index=idx,
        pts=pts,
        detection_index=idx,
        label="person",
        score=0.9,
        box=(x - half, y - half, size, size),
    )


def _track(observations, *, track_id: int = 1, session: int = 1, camera_key: str = "camera") -> Track:
    """A minimal track carrying the given ordered observations."""
    first, last = observations[0], observations[-1]
    return Track(
        track_id=track_id,
        camera_key=camera_key,
        session=session,
        first_frame_index=first.frame_index,
        last_frame_index=last.frame_index,
        first_pts=first.pts,
        last_pts=last.pts,
        detection_count=len(observations),
        detection_indices=[o.detection_index for o in observations],
        observations=list(observations),
        last_box={
            "label": last.label,
            "x": last.box[0],
            "y": last.box[1],
            "width": last.box[2],
            "height": last.box[3],
            "score": last.score,
        },
    )


LINE_START = (0.2, 0.5)
LINE_END = (0.8, 0.5)
RECIPE = {"dead_band": 0.02, "confirmations": 2, "max_gap": 1.0}


class CrossingEpisodeBehaviorTests(SimpleTestCase):
    """Pure track → episode rules. No database, no video."""

    def episodes(self, tracks, **kwargs):
        params = {**RECIPE, **kwargs}
        return crossings.episodes_from_tracks(tracks, LINE_START, LINE_END, **params)

    def test_crossing_and_return_are_each_counted_once(self) -> None:
        track = _track(
            [
                _obs(0.0, 0.5, 0.3),
                _obs(0.1, 0.5, 0.3),
                _obs(0.2, 0.5, 0.49),
                _obs(0.3, 0.5, 0.55),
                _obs(0.4, 0.5, 0.6),
                _obs(0.5, 0.5, 0.6),
                _obs(0.6, 0.5, 0.4),
                _obs(0.7, 0.5, 0.3),
            ]
        )
        events = self.episodes([track])
        self.assertEqual(
            [event.direction for event in events],
            ["negative_to_positive", "positive_to_negative"],
        )
        self.assertEqual(events[0].track_id, 1)
        self.assertEqual(events[0].session, 1)
        self.assertEqual(events[1].track_id, 1)
        self.assertAlmostEqual(events[0].time, 0.2 + 0.1 / 6)

    def test_jitter_touch_parallel_and_outside_segment_do_not_count(self) -> None:
        track = _track(
            [
                _obs(0.0, 0.5, 0.3),
                _obs(0.1, 0.5, 0.3),
                _obs(0.2, 0.5, 0.5),  # touch / on the line
                _obs(0.3, 0.5, 0.49),
                _obs(0.4, 0.5, 0.51),  # jitter around the dead band
                _obs(0.5, 0.5, 0.49),
                _obs(0.6, 0.5, 0.3),
                _obs(0.7, 0.5, 0.3),
                _obs(0.8, 0.95, 0.3),  # parallel outside the finite segment
                _obs(0.9, 0.95, 0.7),
                _obs(1.0, 0.95, 0.7),
                _obs(1.1, 0.96, 0.7),
            ]
        )
        self.assertEqual(self.episodes([track]), [])

    def test_first_frame_and_long_gap_do_not_manufacture_crossings(self) -> None:
        after_gap = _track(
            [
                _obs(0.0, 0.5, 0.3),
                _obs(0.1, 0.5, 0.3),
                _obs(2.0, 0.5, 0.7),
                _obs(2.1, 0.5, 0.7),
            ]
        )
        self.assertEqual(self.episodes([after_gap]), [])
        # After the gap resets the episode, a later stable reverse is a real
        # crossing — never a fabricated bridge across the ambiguous interval.
        continuous = _track(
            [
                _obs(0.0, 0.5, 0.3),
                _obs(0.1, 0.5, 0.3),
                _obs(2.0, 0.5, 0.7),
                _obs(2.1, 0.5, 0.7),
                _obs(2.2, 0.5, 0.3),
                _obs(2.3, 0.5, 0.3),
            ]
        )
        events = self.episodes([continuous])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].direction, "positive_to_negative")

    def test_ambiguous_long_boundary_dwell_does_not_count(self) -> None:
        track = _track(
            [
                _obs(0.0, 0.5, 0.3),
                _obs(0.1, 0.5, 0.3),
                _obs(0.5, 0.5, 0.5),
                _obs(1.0, 0.5, 0.5),
                _obs(1.4, 0.5, 0.7),
                _obs(1.5, 0.5, 0.7),
            ]
        )
        self.assertEqual(self.episodes([track]), [])

    def test_two_tracks_emit_independent_episodes(self) -> None:
        left = _track(
            [
                _obs(0.0, 0.35, 0.3),
                _obs(0.1, 0.35, 0.3),
                _obs(0.2, 0.35, 0.55),
                _obs(0.3, 0.35, 0.6),
                _obs(0.4, 0.35, 0.6),
            ],
            track_id=1,
            session=1,
        )
        right = _track(
            [
                _obs(0.0, 0.65, 0.7),
                _obs(0.1, 0.65, 0.7),
                _obs(0.2, 0.65, 0.45),
                _obs(0.3, 0.65, 0.4),
                _obs(0.4, 0.65, 0.4),
            ],
            track_id=2,
            session=2,
            camera_key="camera-b",
        )
        events = self.episodes([left, right])
        self.assertEqual(
            [(e.track_id, e.session, e.direction, e.camera_key) for e in events],
            [
                (1, 1, "negative_to_positive", "camera"),
                (2, 2, "positive_to_negative", "camera-b"),
            ],
        )

    def test_episode_drafts_are_detector_crossings_with_session_scope(self) -> None:
        track = _track(
            [
                _obs(0.0, 0.5, 0.3),
                _obs(0.1, 0.5, 0.3),
                _obs(0.2, 0.5, 0.55),
                _obs(0.3, 0.5, 0.6),
                _obs(0.4, 0.5, 0.6),
            ],
            session=3,
        )
        events = self.episodes([track])
        self.assertEqual(len(events), 1)
        drafts = crossings.episode_event_drafts(events, source="recording:abc")
        self.assertEqual(len(drafts), 1)
        draft = drafts[0]
        self.assertEqual(draft.kind, OperationEvent.KIND_DETECTOR)
        self.assertEqual(draft.source, "recording:abc")
        self.assertEqual(draft.session, 3)
        self.assertEqual(draft.payload["direction"], "negative_to_positive")
        self.assertEqual(draft.payload["track_id"], 1)
        self.assertEqual(draft.payload["camera_key"], "camera")


class DetectorCrossingLeaseFencingTests(DurableExecutionBase):
    """Worker-side detector crossing commits stay behind the live-lease fence."""

    def _detector_drafts(self):
        return [
            ledger.EventDraft(
                kind=OperationEvent.KIND_DETECTOR,
                source=f"recording:{self.recording.id}",
                session=1,
                timestamp=0.25,
                payload={
                    "direction": "negative_to_positive",
                    "track_id": 1,
                    "camera_key": f"recording:{self.recording.id}",
                },
            )
        ]

    def test_wrong_owner_cannot_commit_detector_crossings(self) -> None:
        execution = self.enqueue_and_claim("worker-a")
        with self.assertRaises(executions.LeaseLost):
            executions.commit_events_owned(execution, "worker-b", self._detector_drafts())
        self.assertEqual(OperationEvent.objects.filter(execution=execution).count(), 0)

    def test_expired_lease_cannot_commit_detector_crossings(self) -> None:
        execution = self.enqueue_and_claim("worker-a")
        past = timezone.now() - timedelta(seconds=1)
        OperationExecution.objects.filter(pk=execution.pk).update(lease_expires_at=past)
        with self.assertRaises(executions.LeaseLost):
            executions.commit_events_owned(execution, "worker-a", self._detector_drafts())
        self.assertEqual(OperationEvent.objects.filter(execution=execution).count(), 0)

    def test_live_owner_commits_detector_crossings_idempotently(self) -> None:
        execution = self.enqueue_and_claim("worker-a")
        drafts = self._detector_drafts()
        first = executions.commit_events_owned(execution, "worker-a", drafts)
        second = executions.commit_events_owned(execution, "worker-a", drafts)
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0].pk, second[0].pk)
        self.assertEqual(OperationEvent.objects.filter(execution=execution).count(), 1)
        row = OperationEvent.objects.get(execution=execution)
        self.assertEqual(row.kind, OperationEvent.KIND_DETECTOR)
        self.assertEqual(row.session, 1)
        self.assertEqual(row.payload["track_id"], 1)

    def test_direct_ledger_commit_events_remains_ungated_for_detector_kind(self) -> None:
        execution = self.enqueue_and_claim("worker-a")
        past = timezone.now() - timedelta(seconds=1)
        OperationExecution.objects.filter(pk=execution.pk).update(lease_expires_at=past)
        committed = ledger.commit_events(execution, self._detector_drafts())
        self.assertEqual(len(committed), 1)
        self.assertEqual(OperationEvent.objects.filter(execution=execution).count(), 1)

    @skipUnless(REQUIRE_FFMPEG, "running an execution needs a decodable recording (ffmpeg)")
    def test_worker_commits_track_episodes_only_under_live_lease(self) -> None:
        """Synthetic track episodes reach the ledger through the owned fence.

        The worker path must not insert detector crossings when the lease is
        already expired; a live owner may commit the drafts the episode engine
        produced from tracks.
        """
        execution = self.enqueue_and_claim("worker-a")
        track = _track(
            [
                _obs(0.0, 0.5, 0.3),
                _obs(0.1, 0.5, 0.3),
                _obs(0.2, 0.5, 0.55),
                _obs(0.3, 0.5, 0.6),
                _obs(0.4, 0.5, 0.6),
            ]
        )
        episodes = crossings.episodes_from_tracks([track], LINE_START, LINE_END, **RECIPE)
        drafts = crossings.episode_event_drafts(
            episodes, source=f"recording:{self.recording.id}"
        )
        self.assertEqual(len(drafts), 1)

        past = timezone.now() - timedelta(seconds=1)
        OperationExecution.objects.filter(pk=execution.pk).update(lease_expires_at=past)
        with self.assertRaises(executions.LeaseLost):
            executions.commit_events_owned(execution, "worker-a", drafts)
        self.assertEqual(
            OperationEvent.objects.filter(
                execution=execution, kind=OperationEvent.KIND_DETECTOR
            ).count(),
            0,
        )

        # Restore a live lease: the same drafts commit once under the fence.
        future = timezone.now() + timedelta(seconds=executions.LEASE_SECONDS)
        OperationExecution.objects.filter(pk=execution.pk).update(
            lease_expires_at=future, heartbeat_at=timezone.now()
        )
        committed = executions.commit_events_owned(execution, "worker-a", drafts)
        self.assertEqual(len(committed), 1)
        self.assertEqual(
            OperationEvent.objects.filter(
                execution=execution, kind=OperationEvent.KIND_DETECTOR
            ).count(),
            1,
        )
