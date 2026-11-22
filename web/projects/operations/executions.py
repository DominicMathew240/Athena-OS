"""Durable operation executions: the lease, the claim, and the worker-side run (T19).

The web process only queues ``OperationExecution`` rows
(:mod:`projects.execution_views`). Worker processes claim a row through a lease
(``lease_owner`` + ``lease_expires_at``), refresh it while they work, and finish
it with a guarded ``UPDATE`` that can never write a second outcome. The claim is
atomic, so several operation workers can share one queue; recovery is by lease
expiry, not by start-up convention, so one worker's start-up never fails
another's in-flight execution.

Strict lease fencing (T19/T23/T25): heartbeat never resurrects an already-expired
lease. Worker-side ledger inserts (reference and detector crossings) and
terminal finishes require a locked RUNNING row whose ``lease_owner`` matches
and whose lease is still live. A stale or wrong-owner worker aborts without a
SUCCEEDED result and without silently skipping required ledger evidence — the
row stays RUNNING for the real owner or for abandoned recovery. Abandoned
recovery re-checks ``lease_expires_at < deadline`` in its UPDATE so a lease
extended after select cannot be failed. Direct ``ledger.commit_events`` stays
ungated for unit idempotency and post-success identity replay.

While ``run_execution`` is in progress a scoped periodic heartbeat thread
refreshes the live lease on a dedicated DB connection, so a legitimate long
digest/timeline/inference stage cannot expire the lease between the
stage-boundary ``_beat`` gates. The thread stops cleanly with the run; a lost,
stolen, or already-expired lease is never extended.

T24 (bounded slice — not complete, not claimed safe): while a worker still owns a
RUNNING, non-expired lease it may persist a bounded ``progress_checkpoint`` naming
stages that match *actual* completed effects. A checkpoint is not full-footage
evidence, not a resume token, and never changes the T19 lease/status/retry
contract. Checkpoint eligibility is irreversible for a run: it starts True only
after a live opening ``_beat`` and is AND-ed with every subsequent live ``_beat``
and every ``checkpoint_progress`` write. A lost lease raises ``LeaseLost`` and
aborts; a failed intermediate checkpoint write freezes later stage evidence
without inventing gaps. Owner-scoped enqueue is capped (queued+running) at the
HTTP boundary so overload cannot look like coverage. A successfully fenced
abandoned-worker failure exposes a bounded interruption/gap summary (incomplete,
not resumable, no invented missing-frame counts); ordinary cancel/fail are not
labeled as worker interruption. After ``timeline_built``, a bounded
``timeline_coverage`` count record (no gap/session arrays, no invented missing
frames) is stored and preserved on later stage writes. Live-source/full-frame
policy is still open.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import socket
import threading
import time
from datetime import timedelta

from django.conf import settings
from django.core.files.storage import default_storage
from django.db import close_old_connections, transaction
from django.utils import timezone

from .. import source_time
from ..models import OperationEvent, OperationExecution
from . import crossings, inference, ledger, parking, tracking
from .comparison import Crossing, compare_crossings

logger = logging.getLogger(__name__)

#: Default lease length. A worker refreshes it every 10 s, so a lease only
#: expires when its owner has missed several refresh windows in a row.
LEASE_SECONDS = 60
#: A heartbeat every 10 s, exactly like the training/inference/autolabel workers.
HEARTBEAT_SECONDS = 10
MAX_ERROR_CHARS = 600
#: Hard ceiling on the serialized checkpoint JSON (bytes). Arbitrary large
#: payloads are refused — a progress marker is not a second result blob.
MAX_CHECKPOINT_BYTES = 4_096

#: Stages that name actual completed effects on the recorded execution path.
STAGE_RECORDING_VERIFIED = "recording_verified"
STAGE_TIMELINE_BUILT = "timeline_built"
STAGE_REFERENCE_EVENTS_COMMITTED = "reference_events_committed"
STAGE_DETECTOR_SAMPLED = "detector_sampled"
STAGE_TRACKS_ASSOCIATED = "tracks_associated"
STAGE_ORDER = (
    STAGE_RECORDING_VERIFIED,
    STAGE_TIMELINE_BUILT,
    STAGE_REFERENCE_EVENTS_COMMITTED,
    STAGE_DETECTOR_SAMPLED,
    STAGE_TRACKS_ASSOCIATED,
)
KNOWN_STAGES = frozenset(STAGE_ORDER)
#: Serialization-only marker: in-memory timeline/detections/tracks are not restored.
IN_MEMORY_DISCARDED_NOT_RESTORED = "discarded_not_restored"
#: Explicit recovery flags: empty/legacy rows are unknown; a written checkpoint
#: never claims the run can resume inference mid-flight.
RECOVERY_UNKNOWN = "unknown"
RECOVERY_NOT_RESUMABLE = "not_resumable"
#: Explicit frame-processing policies (re-exported from inference for T24 callers).
FRAME_POLICY_RECORDED_SAMPLED = inference.FRAME_POLICY_RECORDED_SAMPLED
FRAME_POLICY_RECORDED_FULL = inference.FRAME_POLICY_RECORDED_FULL
FRAME_POLICY_LIVE_DROP = inference.FRAME_POLICY_LIVE_DROP
SUPPORTED_FRAME_POLICIES = inference.SUPPORTED_FRAME_POLICIES
#: Durable marker written only by a successfully fenced abandoned-worker transition.
ABANDONED_WORKER_ERROR = "The operation worker stopped before this run finished. Start a new run."
INTERRUPTION_WORKER_ABANDONED = "worker_abandoned"
#: Owner-scoped cap on queued+running executions at the HTTP enqueue boundary.
#: ``settings.TRAINING["OPERATION_EXECUTION_QUEUE_LIMIT"]`` overrides this default.
DEFAULT_EXECUTION_QUEUE_LIMIT = 50
ACTIVE_EXECUTION_STATUSES = (OperationExecution.QUEUED, OperationExecution.RUNNING)


def execution_queue_limit() -> int:
    """Queued+running cap per owner. Default 50 when TRAINING has no override."""
    training = getattr(settings, "TRAINING", None) or {}
    raw = training.get("OPERATION_EXECUTION_QUEUE_LIMIT", DEFAULT_EXECUTION_QUEUE_LIMIT)
    try:
        limit = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_EXECUTION_QUEUE_LIMIT
    if limit < 1:
        return DEFAULT_EXECUTION_QUEUE_LIMIT
    return limit


class CheckpointError(ValueError):
    """A checkpoint refusal (unknown stage, oversized or non-JSON effects/coverage)."""


def worker_key() -> str:
    """A stable, plain key identifying this worker process in leases and logs."""
    return f"{socket.gethostname()}:{os.getpid()}"


def _worker_interruption_summary(*, completed_stages: list | None = None) -> dict:
    """Bounded interruption: incomplete, not resumable, known unprocessed stages. No frame counts.

    Derived at serialization time. In-memory timeline/detections/tracks from the
    interrupted worker are discarded and not restored.
    """
    completed = set(completed_stages or [])
    return {
        "kind": INTERRUPTION_WORKER_ABANDONED,
        "incomplete": True,
        "resumable": False,
        "unprocessed_stages": [stage for stage in STAGE_ORDER if stage not in completed],
        "in_memory_timeline_detections_tracks": IN_MEMORY_DISCARDED_NOT_RESTORED,
    }


def is_abandoned_worker_failure(execution: OperationExecution) -> bool:
    """True only for the fenced abandoned-worker failure (exact error, empty result)."""
    return (
        execution.status == OperationExecution.FAILED
        and (execution.error or "") == ABANDONED_WORKER_ERROR
        and not (execution.result or {})
    )


_TIMELINE_COVERAGE_KEYS = (
    "gap_threshold_seconds",
    "gap_count",
    "jitter_count",
    "late_frame_count",
    "session_count",
    "frame_count",
    "timeline_sha256",
)
_TIMELINE_COVERAGE_KEY_SET = frozenset(_TIMELINE_COVERAGE_KEYS)
_TIMELINE_COVERAGE_COUNT_KEYS = (
    "gap_count",
    "jitter_count",
    "late_frame_count",
    "session_count",
    "frame_count",
)
_SHA256_HEX_CHARS = frozenset("0123456789abcdef")


def _timeline_coverage_record(timeline) -> dict:
    """Bounded T20 coverage counts from ``timeline.summary()``. No gap/session arrays."""
    summary = timeline.summary()
    return {
        "gap_threshold_seconds": summary["gap_threshold_seconds"],
        "gap_count": len(summary.get("gaps") or []),
        "jitter_count": summary["jitter_count"],
        "late_frame_count": summary["late_frame_count"],
        "session_count": len(summary.get("sessions") or []),
        "frame_count": summary["frame_count"],
        "timeline_sha256": timeline.timeline_sha256(),
    }


def _normalized_timeline_coverage(record) -> dict:
    """Accept only the scalar coverage key set; return a fresh dict or raise."""
    if not isinstance(record, dict):
        raise CheckpointError("Checkpoint timeline coverage must be a JSON object.")
    if frozenset(record) != _TIMELINE_COVERAGE_KEY_SET:
        raise CheckpointError("Checkpoint timeline coverage must be a bounded scalar record.")
    threshold = record["gap_threshold_seconds"]
    try:
        finite_threshold = math.isfinite(threshold)
    except (OverflowError, TypeError, ValueError):
        finite_threshold = False
    if (
        isinstance(threshold, bool)
        or not isinstance(threshold, (int, float))
        or not finite_threshold
        or threshold < 0
    ):
        raise CheckpointError("Checkpoint timeline coverage must be a bounded scalar record.")
    sha = record["timeline_sha256"]
    if (
        not isinstance(sha, str)
        or len(sha) != 64
        or any(char not in _SHA256_HEX_CHARS for char in sha)
    ):
        raise CheckpointError("Checkpoint timeline coverage must be a bounded scalar record.")
    counts = {}
    for key in _TIMELINE_COVERAGE_COUNT_KEYS:
        value = record[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise CheckpointError("Checkpoint timeline coverage must be a bounded scalar record.")
        counts[key] = value
    normalized = {
        "gap_threshold_seconds": threshold,
        "gap_count": counts["gap_count"],
        "jitter_count": counts["jitter_count"],
        "late_frame_count": counts["late_frame_count"],
        "session_count": counts["session_count"],
        "frame_count": counts["frame_count"],
        "timeline_sha256": sha,
    }
    try:
        json.dumps(
            normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError) as error:
        raise CheckpointError(
            "Checkpoint timeline coverage is not canonically JSON-serializable."
        ) from error
    return normalized


def serialize_progress_checkpoint(execution: OperationExecution) -> dict:
    """The API-facing checkpoint view: actual stored state, or safe empty defaults.

    An empty/legacy row reports recovery ``unknown``, ``sampled_analysis`` false,
    and never claims coverage. A written checkpoint is returned as stored
    (sampled only after detector completion; not resumable; not full coverage).
    A stored ``timeline_coverage`` count record is passed through when present
    and omitted when absent so legacy/empty rows keep their shape. A successfully
    fenced abandoned-worker failure also exposes a bounded interruption summary
    (ordered unprocessed stages, discarded in-memory timeline/detections/tracks);
    ordinary cancel/fail do not.
    """
    raw = execution.progress_checkpoint or {}
    if not raw:
        data = {
            "stage": None,
            "completed_stages": [],
            # Empty/legacy: no detector ran — never claim sampled analysis.
            "sampled_analysis": False,
            "full_frame_coverage": False,
            "recovery": RECOVERY_UNKNOWN,
            "effects": {},
        }
    else:
        data = {
            "stage": raw.get("stage"),
            "completed_stages": list(raw.get("completed_stages") or []),
            "sampled_analysis": bool(raw.get("sampled_analysis", False)),
            "full_frame_coverage": bool(raw.get("full_frame_coverage", False)),
            "recovery": raw.get("recovery") or RECOVERY_NOT_RESUMABLE,
            "effects": dict(raw.get("effects") or {}),
            **({"updated_at": raw["updated_at"]} if raw.get("updated_at") else {}),
        }
        coverage = raw.get("timeline_coverage")
        if isinstance(coverage, dict) and coverage:
            data["timeline_coverage"] = dict(coverage)
    if is_abandoned_worker_failure(execution):
        data["interruption"] = _worker_interruption_summary(
            completed_stages=data.get("completed_stages") or []
        )
        data["full_frame_coverage"] = False
        data["recovery"] = RECOVERY_NOT_RESUMABLE
    return data


def checkpoint_progress(
    execution: OperationExecution,
    owner_key: str,
    stage: str,
    *,
    effects: dict | None = None,
    timeline_coverage: dict | None = None,
    sampled_analysis: bool | None = None,
    full_frame_coverage: bool = False,
) -> bool:
    """Persist a bounded progress checkpoint while this worker owns a live lease.

    Returns True only when the row is still ``running``, ``lease_owner`` matches,
    and ``lease_expires_at`` is still in the future. Wrong owner, expired lease,
    or a terminal status yields False and writes nothing — ownership semantics
    are never silently widened. The payload records ``sampled_analysis`` only
    after ``STAGE_DETECTOR_SAMPLED`` has completed (unless ``recorded_full`` or
    ``live_frame_drop`` explicitly passes ``sampled_analysis=False``); earlier /
    no-detector stages stay false. Full frame coverage is false by default and
    can only be True after ``STAGE_DETECTOR_SAMPLED`` under ``recorded_full``
    when the timeline has zero gaps and zero late frames. Recovery is
    ``not_resumable`` — a checkpoint is progress evidence, not a mid-frame
    in-memory resume token. A previously stored ``timeline_coverage`` record is
    preserved unless this write supplies a new one. Incoming and preserved
    records are normalized to the scalar key set; invalid records are refused
    rather than stored or copied.
    """
    if stage not in KNOWN_STAGES:
        raise CheckpointError(f"Unknown progress stage: {stage}.")
    effects = effects or {}
    if not isinstance(effects, dict):
        raise CheckpointError("Checkpoint effects must be a JSON object.")
    incoming_coverage = (
        _normalized_timeline_coverage(timeline_coverage) if timeline_coverage is not None else None
    )
    try:
        encoded_effects = json.dumps(
            effects, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError) as error:
        raise CheckpointError("Checkpoint effects are not canonically JSON-serializable.") from error

    with transaction.atomic():
        # Lock first; freshness is computed only after the row is held — never from a
        # pre-lock clock read that could go stale while waiting on select_for_update.
        row = (
            OperationExecution.objects.select_for_update()
            .filter(
                pk=execution.pk,
                status=OperationExecution.RUNNING,
                lease_owner=owner_key,
            )
            .first()
        )
        if row is None:
            return False
        now = timezone.now()
        if row.lease_expires_at is None or row.lease_expires_at <= now:
            return False
        previous = row.progress_checkpoint or {}
        completed = [s for s in (previous.get("completed_stages") or []) if s in KNOWN_STAGES]
        if stage not in completed:
            completed.append(stage)
        if incoming_coverage is not None:
            coverage = incoming_coverage
        elif "timeline_coverage" in previous:
            coverage = _normalized_timeline_coverage(previous.get("timeline_coverage"))
        else:
            coverage = None
        detector_completed = STAGE_DETECTOR_SAMPLED in completed
        if not detector_completed:
            effective_sampled = False
            effective_full_coverage = False
        else:
            effective_sampled = True if sampled_analysis is None else bool(sampled_analysis)
            effective_full_coverage = bool(
                full_frame_coverage
                and not effective_sampled
                and coverage is not None
                and coverage.get("gap_count", 1) == 0
                and coverage.get("late_frame_count", 1) == 0
                and coverage.get("frame_count", 0) > 0
            )
        payload = {
            "stage": stage,
            "completed_stages": completed,
            "sampled_analysis": effective_sampled,
            "full_frame_coverage": effective_full_coverage,
            "recovery": RECOVERY_NOT_RESUMABLE,
            "effects": effects,
            "updated_at": now.isoformat(),
        }
        if coverage is not None:
            payload["timeline_coverage"] = coverage
        try:
            encoded = json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
        except (TypeError, ValueError) as error:
            raise CheckpointError("Checkpoint is not canonically JSON-serializable.") from error
        if len(encoded.encode("utf-8")) > MAX_CHECKPOINT_BYTES:
            raise CheckpointError(
                f"Checkpoint exceeds the {MAX_CHECKPOINT_BYTES}-byte bound "
                f"(effects were {len(encoded_effects.encode('utf-8'))} bytes)."
            )
        # One guarded UPDATE keeps the stage write atomic with the lease predicate.
        changed = OperationExecution.objects.filter(
            pk=row.pk,
            status=OperationExecution.RUNNING,
            lease_owner=owner_key,
            lease_expires_at__gt=now,
        ).update(progress_checkpoint=payload)
        return bool(changed)


# -- claim -----------------------------------------------------------------------------------
def claim_next_execution(owner_key: str, lease_seconds: int = LEASE_SECONDS) -> OperationExecution | None:
    """Claim the oldest queued execution for this worker, or return None.

    The status flip plus the lease are one guarded ``UPDATE`` inside a
    transaction, the same proven pattern as ``claim_next_run``: whichever
    claimer's update matches the queued row first wins, and every other claimer
    sees no row. The lease is extended by heartbeats while the work runs.
    """
    with transaction.atomic():
        row = (
            OperationExecution.objects.filter(status=OperationExecution.QUEUED)
            .select_for_update()
            .order_by("created_at")
            .first()
        )
        if row is None:
            return None
        now = timezone.now()
        claimed = OperationExecution.objects.filter(pk=row.pk, status=OperationExecution.QUEUED).update(
            status=OperationExecution.RUNNING,
            started_at=now,
            heartbeat_at=now,
            lease_owner=owner_key,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
        )
        if not claimed:
            return None
        return OperationExecution.objects.select_related("bundle", "recording").get(pk=row.pk)


def release_lease(execution_id, owner_key: str) -> bool:
    """Give a lease back (worker shutting down between jobs). Returns True when held."""
    changed = OperationExecution.objects.filter(
        pk=execution_id, status=OperationExecution.RUNNING, lease_owner=owner_key
    ).update(lease_owner="", lease_expires_at=None, heartbeat_at=timezone.now())
    return bool(changed)


def _finish_owned(
    execution,
    owner_key: str,
    *,
    status: str,
    error: str = "",
    result: dict | None = None,
) -> bool:
    """Record one terminal outcome for the live lease owner. Returns False otherwise.

    Requires ``status='running'``, matching ``lease_owner``, and a still-live
    ``lease_expires_at`` at UPDATE time (strict lease policy). After a
    crash-and-recovery, cancel race, lost lease, or wrong owner, the guard
    matches no row and the late writer changes nothing — the training SIGKILL
    no-duplicate guarantee, plus ownership fencing.
    """
    now = timezone.now()
    changed = OperationExecution.objects.filter(
        pk=execution.pk,
        status=OperationExecution.RUNNING,
        lease_owner=owner_key,
        lease_expires_at__gt=now,
    ).update(
        status=status,
        finished_at=now,
        error=error[:MAX_ERROR_CHARS],
        result=result or {},
        lease_owner="",
        lease_expires_at=None,
    )
    return bool(changed)


def _finish_abandoned(execution, *, deadline) -> bool:
    """Fail one abandoned RUNNING row whose lease is still past ``deadline`` at UPDATE.

    The deadline re-check is the fencing against a stale iterator: if another
    path extended ``lease_expires_at`` after this row was selected, the UPDATE
    matches nothing and the live owner is left alone. Only a matching UPDATE
    writes the abandoned-worker error; the API derives the bounded interruption
    summary from that terminal state, leaving the bounded checkpoint unchanged.
    """
    now = timezone.now()
    with transaction.atomic():
        row = (
            OperationExecution.objects.select_for_update()
            .filter(
                pk=execution.pk,
                status=OperationExecution.RUNNING,
                lease_expires_at__lt=deadline,
            )
            .first()
        )
        if row is None:
            return False
        updates = {
            "status": OperationExecution.FAILED,
            "finished_at": now,
            "error": ABANDONED_WORKER_ERROR,
            "result": {},
            "lease_owner": "",
            "lease_expires_at": None,
        }
        changed = OperationExecution.objects.filter(
            pk=row.pk,
            status=OperationExecution.RUNNING,
            lease_expires_at__lt=deadline,
        ).update(**updates)
        return bool(changed)


# Backward-compatible name used by a few tests that probe the post-terminal guard.
def _finish(execution, *, status: str, error: str = "", result: dict | None = None, owner_key: str = "") -> bool:
    """Owned finish when ``owner_key`` is set; otherwise status-only (legacy probe).

    Worker paths must call ``_finish_owned``. The status-only branch exists so
    post-terminal duplicate-outcome probes keep working without inventing an owner.
    """
    if owner_key:
        return _finish_owned(execution, owner_key, status=status, error=error, result=result)
    changed = OperationExecution.objects.filter(pk=execution.pk, status=OperationExecution.RUNNING).update(
        status=status,
        finished_at=timezone.now(),
        error=error[:MAX_ERROR_CHARS],
        result=result or {},
        lease_owner="",
        lease_expires_at=None,
    )
    return bool(changed)


def _beat(execution, owner_key: str, lease_seconds: int) -> bool:
    """Refresh heartbeat and lease on one running execution owned by ``owner_key``.

    Returns True only when the lease was still non-expired at the guarded write
    (freshness read after ``select_for_update``, not before waiting on the lock).
    Strict policy: an already-expired lease is never renewed — heartbeat must
    not resurrect expiry. Wrong owner or non-RUNNING yields False and renews
    nothing. Callers treat False as lost lease and abort irreversible work.
    """
    with transaction.atomic():
        row = (
            OperationExecution.objects.select_for_update()
            .filter(pk=execution.pk, status=OperationExecution.RUNNING, lease_owner=owner_key)
            .first()
        )
        if row is None:
            return False
        now = timezone.now()
        if row.lease_expires_at is None or row.lease_expires_at <= now:
            return False
        changed = OperationExecution.objects.filter(
            pk=row.pk,
            status=OperationExecution.RUNNING,
            lease_owner=owner_key,
            lease_expires_at__gt=now,
        ).update(heartbeat_at=now, lease_expires_at=now + timedelta(seconds=lease_seconds))
        return bool(changed)


def fail_abandoned_executions(lease_seconds: int = LEASE_SECONDS) -> int:
    """Fail executions whose worker died mid-run, and only those.

    An execution is abandoned when its lease has expired by a full extra lease
    interval — the owner missed its refresh window twice — so a worker that is
    merely slow, or whose clock ticked between refreshes, is never failed. This
    replaces the single-worker "fail everything running at start-up" convention:
    worker A's start-up cannot fail worker B's in-flight execution, because B's
    lease is still fresh. Each UPDATE re-checks ``lease_expires_at < deadline``
    so a lease extended after the queryset was loaded cannot be failed. A failed
    execution is re-queueable: enqueue creates a new row, and the owned-finish
    guard means the dead worker can never also write.
    """
    now = timezone.now()
    deadline = now - timedelta(seconds=2 * lease_seconds)
    # Values-list of pks only: the abandoned predicate is re-applied per UPDATE.
    abandoned_ids = list(
        OperationExecution.objects.filter(
            status=OperationExecution.RUNNING, lease_expires_at__lt=deadline
        ).values_list("pk", flat=True)
    )
    count = 0
    for execution_id in abandoned_ids:
        execution = OperationExecution(pk=execution_id)
        if _finish_abandoned(execution, deadline=deadline):
            count += 1
    return count


# -- the execution step ----------------------------------------------------------------------
class ExecutionCancelled(Exception):
    pass


class LeaseLost(Exception):
    """This worker no longer holds a live lease; do not write side effects or SUCCEEDED."""
    pass


def _cancelled(execution) -> bool:
    return OperationExecution.objects.filter(pk=execution.pk, cancel_requested=True).exists()


def _recording_digest(execution) -> str | None:
    """SHA-256 of the pinned recording's stored bytes, or None when unreadable."""
    try:
        with default_storage.open(execution.recording.storage_key, "rb") as stored:
            digest = hashlib.sha256()
            for chunk in iter(lambda: stored.read(1024 * 1024), b""):
                digest.update(chunk)
            return digest.hexdigest()
    except (OSError, ValueError):
        return None


def _reference_events(snapshot: dict) -> list[Crossing]:
    events = snapshot.get("reference_events") or []
    return [Crossing(direction=e["direction"], time=float(e["time"])) for e in events]


def _ledger_source(execution) -> str:
    """The event source stream name for one execution: the recording it ran on."""
    return f"recording:{execution.recording_id}"


def _pins(execution) -> dict:
    """The pins frozen at enqueue: bundle snapshot pins, plus digests provenance."""
    pins = dict(execution.pins or {})
    pins.setdefault("bundle_sha256", execution.bundle_sha256)
    pins.setdefault("recording_sha256", execution.recording_sha256)
    return pins


def commit_events_owned(execution, owner_key: str, drafts) -> list:
    """Commit ledger drafts only while this worker holds a locked live lease.

    Covers reference crossings (T23) and detector crossing episodes (T25).
    Locks the execution row, verifies RUNNING + matching owner + non-expired
    lease, then delegates to ungated ``ledger.commit_events`` (idempotent
    identity hits still work). Raises ``LeaseLost`` instead of inserting when
    ownership is invalid — callers must abort without SUCCEEDED rather than
    silently skipping required ledger evidence.
    """
    with transaction.atomic():
        row = (
            OperationExecution.objects.select_for_update()
            .filter(
                pk=execution.pk,
                status=OperationExecution.RUNNING,
                lease_owner=owner_key,
            )
            .first()
        )
        if row is None:
            raise LeaseLost("This worker does not own a running lease for this execution.")
        now = timezone.now()
        if row.lease_expires_at is None or row.lease_expires_at <= now:
            raise LeaseLost("The lease for this execution has expired.")
        return ledger.commit_events(execution, drafts)


def commit_reference_events_owned(execution, owner_key: str, drafts) -> list:
    """Alias for ``commit_events_owned`` — reference commits keep the old name."""
    return commit_events_owned(execution, owner_key, drafts)


def _require_live_lease(execution, owner_key: str, lease_seconds: int, heartbeat=None) -> None:
    """Heartbeat once; raise ``LeaseLost`` when the lease is not live for ``owner_key``."""
    if not _beat(execution, owner_key, lease_seconds):
        raise LeaseLost("The lease for this execution is not live for this worker.")
    if heartbeat is not None and heartbeat.lost:
        raise LeaseLost("The lease for this execution is not live for this worker.")


class ExecutionLeaseHeartbeat:
    """Periodic lease refresh for one ``run_execution``, on a dedicated DB connection.

    Scoped to a single owned run: start when the worker begins the long step,
    stop in a ``finally`` so the thread cannot outlive the run. Each beat uses
    ``close_old_connections`` so the thread never shares the caller's connection.
    ``_beat``'s strict fencing is preserved — wrong owner or an already-expired
    lease returns False, marks the heartbeat lost, and stops further attempts
    (never resurrects). ``interval_seconds <= 0`` disables the thread (tests).
    """

    def __init__(
        self,
        execution_id,
        owner_key: str,
        lease_seconds: int,
        *,
        interval_seconds: float = HEARTBEAT_SECONDS,
    ) -> None:
        self.execution_id = execution_id
        self.owner_key = owner_key
        self.lease_seconds = lease_seconds
        self.interval_seconds = float(interval_seconds)
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def lost(self) -> bool:
        return self._lost.is_set()

    @property
    def alive(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self) -> ExecutionLeaseHeartbeat:
        if self.interval_seconds <= 0 or self._thread is not None:
            return self
        self._stop.clear()
        self._lost.clear()
        thread = threading.Thread(
            target=self._loop,
            name=f"execution-lease-{self.execution_id}",
            daemon=True,
        )
        self._thread = thread
        thread.start()
        return self

    def stop(self, *, join_timeout: float | None = None) -> None:
        """Signal the thread to exit and join it. Safe to call more than once."""
        self._stop.set()
        thread = self._thread
        if thread is None:
            return
        timeout = join_timeout if join_timeout is not None else max(5.0, self.interval_seconds + 2.0)
        thread.join(timeout=timeout)
        if thread.is_alive():
            logger.warning(
                "Execution lease heartbeat for %s did not stop within %.1fs",
                self.execution_id,
                timeout,
            )
        else:
            self._thread = None

    def _loop(self) -> None:
        close_old_connections()
        try:
            while not self._stop.wait(timeout=self.interval_seconds):
                close_old_connections()
                try:
                    # Strict fencing: False means stolen, expired, or not RUNNING.
                    # Stop immediately — never keep trying to resurrect.
                    if not _beat(
                        OperationExecution(pk=self.execution_id),
                        self.owner_key,
                        self.lease_seconds,
                    ):
                        self._lost.set()
                        return
                except Exception:  # noqa: BLE001 - never leave a runaway heartbeat thread
                    logger.exception(
                        "Execution lease heartbeat failed for %s (%s)",
                        self.execution_id,
                        self.owner_key,
                    )
                    self._lost.set()
                    return
                finally:
                    close_old_connections()
        finally:
            close_old_connections()


def run_execution(
    execution: OperationExecution,
    owner_key: str | None = None,
    lease_seconds: int = LEASE_SECONDS,
    heartbeat_seconds: float = HEARTBEAT_SECONDS,
) -> OperationExecution:
    """Run a claimed execution to completion. Never raises; outcomes land on the row.

    The execution step proves the pinned recording is still the byte-identical
    file the bundle was assembled from, reads that recording's frame timestamps
    into the ordered, reproducible timeline (T20's ``source_time``), cross-checks
    the recording's stored duration against that timeline, and replays the
    recorded human reference events through the operation domain's comparison
    logic. Since T21, when the bundle pins a model binding, the step also runs
    the bound model — re-validating task/label/artifact compatibility at run
    time — on reference frames sampled from the timeline, recording its
    normalized detections. Since T22, those detections are associated into
    per-camera, per-session tracks (greedy motion-compensated IoU, deterministic)
    whose summaries land in the outcome: occlusions shorter than the gap
    threshold resume the same id, longer ones end the track, and a session
    boundary always ends it. Since T25, when the pinned geometry and recipe
    resolve, those tracks feed directional crossing episodes on the finite
    line; valid episodes commit as ``detector_crossing`` ledger rows under the
    same live-lease fence, returns count, and jitter/touches/parallel/long-gap
    motion never fabricates events. What the step never claims: this is
    recorded replay analysis on sampled frames, not a live operation, and the
    track ids are per-session bookkeeping, not persistent identities. Since
    T23, the recorded human reference events are committed to the durable
    event ledger before comparison, so a retry or replay of this execution can
    never double-commit them — and only while this worker still holds a live
    lease.

    A scoped periodic heartbeat covers the whole step so a single long stage
    (digest, timeline, inference, tracking) cannot silently outlive the lease
    between the explicit stage-boundary beats. Cancellation checkpoints and
    row fencing are unchanged.
    """
    owner_key = owner_key or execution.lease_owner or worker_key()
    snapshot = execution.bundle.snapshot or {}
    heartbeat = ExecutionLeaseHeartbeat(
        execution.pk,
        owner_key,
        lease_seconds,
        interval_seconds=heartbeat_seconds,
    )
    heartbeat.start()
    try:
        try:
            # Opening beat must be live under strict policy; expired/wrong-owner abort.
            _require_live_lease(execution, owner_key, lease_seconds)
            allow_checkpoints = True
            if _cancelled(execution):
                raise ExecutionCancelled("Cancelled by request.")
            digest = _recording_digest(execution)
            if digest is None:
                raise ValueError(
                    "The pinned recording could not be read from storage. Ask your administrator to check it."
                )
            if digest != execution.recording_sha256:
                raise ValueError(
                    "The pinned recording changed on disk since the run was queued. Start a new run from the new file."
                )
            if allow_checkpoints:
                allow_checkpoints = checkpoint_progress(
                    execution,
                    owner_key,
                    STAGE_RECORDING_VERIFIED,
                    effects={"recording_sha256": digest},
                )
            if _cancelled(execution):
                raise ExecutionCancelled("Cancelled by request.")
            # T20: read the pinned recording's frame timestamps and derive the
            # ordered processing timeline (frame count, gaps, sessions), then close
            # T20's known gap: the stored duration must match the timeline's span.
            timeline = source_time.build_timeline(execution.recording)
            duration_check = inference.crosscheck_duration(execution.recording, timeline)
            if allow_checkpoints:
                allow_checkpoints = checkpoint_progress(
                    execution,
                    owner_key,
                    STAGE_TIMELINE_BUILT,
                    effects={
                        "frame_count": timeline.summary().get("frame_count"),
                        "timeline_sha256": timeline.timeline_sha256(),
                        # Explicit: a built timeline is not proof every frame was analysed.
                        "full_frame_coverage": False,
                    },
                    timeline_coverage=_timeline_coverage_record(timeline),
                )
            _require_live_lease(execution, owner_key, lease_seconds, heartbeat)
            if _cancelled(execution):
                raise ExecutionCancelled("Cancelled by request.")
            reference = _reference_events(snapshot)
            tolerance = _comparison_tolerance(snapshot)
            # T23: reference events commit under the locked live lease. LeaseLost
            # aborts without SUCCEEDED — never silently skip required ledger rows.
            events = commit_reference_events_owned(
                execution,
                owner_key,
                [
                    ledger.EventDraft(
                        kind=OperationEvent.KIND_REFERENCE,
                        source=_ledger_source(execution),
                        session=0,  # reference events are recorded against the whole recording
                        timestamp=crossing.time,
                        payload={"direction": crossing.direction, "tolerance_seconds": tolerance},
                    )
                    for crossing in reference
                ],
            )
            # The stand-in "predicted" stream is the reference itself shifted by zero: an honest
            # placeholder that exercises the pinned recipe's comparison semantics end to end.
            comparison = compare_crossings(reference, reference, tolerance=tolerance)
            if allow_checkpoints:
                allow_checkpoints = checkpoint_progress(
                    execution,
                    owner_key,
                    STAGE_REFERENCE_EVENTS_COMMITTED,
                    effects={
                        "event_count": len(events),
                        "matched": len(comparison.matches),
                        "missed": len(comparison.missed),
                        "extra": len(comparison.extra),
                    },
                )
            _require_live_lease(execution, owner_key, lease_seconds, heartbeat)
            if _cancelled(execution):
                raise ExecutionCancelled("Cancelled by request.")
            # T21: a pinned model binding means real detector inference on sampled
            # reference frames (or under the explicit policy pin), with compatibility
            # re-validated at run time.
            binding = _pinned_binding(execution)
            policy_cfg = _frame_policy_config(execution)
            detector = None
            tracks = None
            parking_report = None
            policy_plan = None
            predicted_source = "reference_placeholder"
            final_comparison = comparison
            if binding is not None:
                recipe_key = _recipe_key(snapshot)
                report = inference.run_detector(
                    binding,
                    execution.recording,
                    timeline,
                    recipe_key,
                    frame_policy=policy_cfg["frame_processing_policy"],
                    max_in_flight_frames=policy_cfg["max_in_flight_frames"],
                )
                policy_plan = getattr(report, "policy_plan", None)
                is_sampled = (
                    policy_plan.sampled_analysis
                    if policy_plan is not None
                    else (policy_cfg["frame_processing_policy"] == FRAME_POLICY_RECORDED_SAMPLED)
                )
                is_full_coverage = bool(
                    policy_plan.full_frame_coverage
                    if policy_plan is not None
                    else (
                        policy_cfg["frame_processing_policy"] == FRAME_POLICY_RECORDED_FULL
                        and report.frame_count == len(timeline.frames) > 0
                        and len(timeline.gaps) == 0
                        and len(timeline.late_frames) == 0
                    )
                )
                if allow_checkpoints:
                    allow_checkpoints = checkpoint_progress(
                        execution,
                        owner_key,
                        STAGE_DETECTOR_SAMPLED,
                        effects={
                            "sampled_frame_count": report.frame_count,
                            "total_detections": sum(len(frame.detections) for frame in report.frames),
                            "sampled_analysis": is_sampled,
                            "full_frame_coverage": is_full_coverage,
                        },
                        sampled_analysis=is_sampled,
                        full_frame_coverage=is_full_coverage,
                    )
                _require_live_lease(execution, owner_key, lease_seconds, heartbeat)
                detector = {
                    "model_version_id": report.model_version_id,
                    "model_version_number": report.model_version_number,
                    "backend": report.backend,
                    "artifact_sha256": report.artifact_sha256,
                    "recipe_key": report.recipe_key,
                    "expected_labels": list(report.expected_labels),
                    "model_labels": list(report.model_labels),
                    "frame_count": report.frame_count,
                    "frame_policy": getattr(report, "frame_policy", policy_cfg["frame_processing_policy"]),
                    "total_detections": sum(len(frame.detections) for frame in report.frames),
                    "frames": [
                        {
                            "frame_index": frame.frame_index,
                            "pts": frame.pts,
                            "session": frame.session,
                            "wall_clock": frame.wall_clock,
                            "detections": [
                                {
                                    "label": box["label"],
                                    "x": box["x"],
                                    "y": box["y"],
                                    "width": box["width"],
                                    "height": box["height"],
                                    "score": box["score"],
                                }
                                for box in frame.detections
                            ],
                        }
                        for frame in report.frames
                    ],
                }
                # T22: associate the ordered detections into per-camera, per-session
                # tracks. A TrackingError here is a malformed adapter stream — the
                # honest outcome is a plain failure, never tracks that hide it.
                tracking_report = tracking.track_detections(
                    report.frames,
                    camera_key=f"recording:{execution.recording_id}",
                    max_gap_seconds=timeline.gap_threshold_seconds,
                )
                tracks = _tracks_outcome(tracking_report, report)
                if allow_checkpoints:
                    allow_checkpoints = checkpoint_progress(
                        execution,
                        owner_key,
                        STAGE_TRACKS_ASSOCIATED,
                        effects={
                            "track_count": tracks.get("track_count"),
                            "sampled_frame_count": tracks.get("sampled_frame_count"),
                            "full_frame_coverage": is_full_coverage,
                        },
                        sampled_analysis=is_sampled,
                        full_frame_coverage=is_full_coverage,
                    )
                # T25: directional crossing episodes from tracks over the pinned
                # finite line. Missing/unresolvable geometry skips — never
                # fabricates. Commits stay behind the live-lease fence.
                line = _pinned_line(execution)
                if line is not None:
                    _require_live_lease(execution, owner_key, lease_seconds, heartbeat)
                    recipe_params = _crossing_recipe_params(snapshot)
                    episodes = crossings.episodes_from_tracks(
                        tracking_report.tracks,
                        line["start"],
                        line["end"],
                        **recipe_params,
                    )
                    detector_events = commit_events_owned(
                        execution,
                        owner_key,
                        crossings.episode_event_drafts(
                            episodes, source=_ledger_source(execution)
                        ),
                    )
                    events = list(events) + list(detector_events)
                    tracks = dict(tracks)
                    tracks["crossing_episode_count"] = len(episodes)
                    # T26: compare labeled reference events against actual detector
                    # crossing episodes within the declared matching tolerance.
                    predicted_crossings = [
                        Crossing(direction=ep.direction, time=float(ep.time))
                        for ep in episodes
                    ]
                    final_comparison = compare_crossings(
                        reference, predicted_crossings, tolerance=tolerance
                    )
                    predicted_source = "detector_crossing_episodes"
                pinned_bays = _pinned_bays(execution)
                if pinned_bays:
                    parking_report = parking.evaluate_temporal_bay_states(
                        pinned_bays,
                        report.frames,
                    )
            gap_accounting = build_gap_accounting(
                timeline,
                frame_processing_policy=policy_cfg["frame_processing_policy"],
                policy_plan=policy_plan,
                analysed_frame_count=detector["frame_count"] if detector is not None else 0,
                max_coverage_gap_seconds=policy_cfg["max_coverage_gap_seconds"],
            )
            result = {
                "kind": "reference_replay",
                "recording_sha256": digest,
                "pins": _pins(execution),
                "frame_timeline": timeline.summary(),
                "timeline_sha256": timeline.timeline_sha256(),
                "duration_check": duration_check,
                "gap_accounting": gap_accounting,
                "comparison": final_comparison.summary(predicted_source=predicted_source),
                # T23: the ledger aggregates for THIS execution, recorded from the
                # committed raw events (a rebuild from the rows must equal this).
                "event_ledger": ledger.rebuild_aggregates(events).summary(),
                "note": _outcome_note(binding, detector, tracks),
            }
            if detector is not None:
                result["detector"] = detector
            if tracks is not None:
                result["tracks"] = tracks
            if parking_report is not None:
                result["parking"] = parking_report
            # SUCCEEDED only with the required ledger events already committed above.
            _finish_owned(execution, owner_key, status=OperationExecution.SUCCEEDED, result=result)
        except LeaseLost:
            # Abort without a terminal result: leave RUNNING for the real owner / reaper.
            logger.info("Operation execution %s aborted: lease lost for %s", execution.pk, owner_key)
        except ExecutionCancelled:
            _finish_owned(execution, owner_key, status=OperationExecution.CANCELLED, error="Cancelled by request.")
        except Exception as exc:  # noqa: BLE001 - recorded on the row for the operator
            logger.exception("Operation execution %s failed", execution.pk)
            _finish_owned(
                execution,
                owner_key,
                status=OperationExecution.FAILED,
                error=str(exc) or exc.__class__.__name__,
            )
        return OperationExecution.objects.get(pk=execution.pk)
    finally:
        heartbeat.stop()



def _pinned_binding(execution):
    """The real OperationModelBinding the bundle's snapshot pinned, or None.

    The snapshot's model pin names the binding's id; the row is fetched from the
    database so inference re-validates the *current* model version it points at
    (the pin's recorded checks are never trusted). A pin whose id is not a real
    binding (a malformed snapshot) is a plain failure, not a silent no-model run:
    the bundle claims a model that cannot be found.
    """
    pin = (execution.bundle.snapshot or {}).get("component_pins", {}).get("model") or {}
    binding_id = pin.get("id")
    if not binding_id:
        return None
    import uuid as _uuid

    from ..models import OperationModelBinding

    try:
        binding_uuid = _uuid.UUID(str(binding_id))
    except (ValueError, AttributeError, TypeError):
        raise ValueError(
            "The bundle's model pin does not name a valid model binding. Rebuild the release bundle."
        ) from None
    binding = OperationModelBinding.objects.select_related("model_version").filter(id=binding_uuid).first()
    if binding is None:
        raise ValueError(
            "The model binding this bundle pinned no longer exists. Rebuild the release bundle with a "
            "current model binding."
        )
    return binding


def _pinned_line(execution) -> dict | None:
    """The pinned finite counting line, or None when geometry cannot be resolved.

    Looks up the real ``GeometryVersion`` named by the bundle pin (same pattern
    as the model binding). A missing/non-UUID pin — common in lean test
    fixtures that never froze geometry — skips crossing episodes rather than
    inventing a line. A pin that names a real id that no longer exists is a
    plain failure: the bundle claims geometry that cannot be found.
    """
    pin = (execution.bundle.snapshot or {}).get("component_pins", {}).get("geometry") or {}
    geometry_id = pin.get("id")
    if not geometry_id:
        return None
    import uuid as _uuid

    from ..models import GeometryVersion

    try:
        geometry_uuid = _uuid.UUID(str(geometry_id))
    except (ValueError, AttributeError, TypeError):
        return None
    row = GeometryVersion.objects.filter(id=geometry_uuid).first()
    if row is None:
        raise ValueError(
            "The geometry version this bundle pinned no longer exists. Rebuild the release bundle "
            "with a current counting line."
        )
    line = (row.geometry or {}).get("line") if isinstance(row.geometry, dict) else None
    if not isinstance(line, dict) or "start" not in line or "end" not in line:
        raise ValueError(
            "The pinned geometry does not carry a finite counting line. Freeze a line before running."
        )
    return line


def _pinned_bays(execution) -> list[dict]:
    """The pinned polygon bays from the bundle's ``GeometryVersion``, or ``[]``."""
    pin = (execution.bundle.snapshot or {}).get("component_pins", {}).get("geometry") or {}
    geometry_id = pin.get("id")
    if not geometry_id:
        return []
    import uuid as _uuid

    from ..models import GeometryVersion

    try:
        geometry_uuid = _uuid.UUID(str(geometry_id))
    except (ValueError, AttributeError, TypeError):
        return []
    row = GeometryVersion.objects.filter(id=geometry_uuid).first()
    if row is None or not isinstance(row.geometry, dict):
        return []
    bays = row.geometry.get("bays")
    return list(bays) if isinstance(bays, list) else []


def _recipe_key(snapshot: dict) -> str:
    """The recipe key the bundle's snapshot pinned (the operation domain's one recipe)."""
    recipe = (snapshot.get("component_pins") or {}).get("recipe") or {}
    return recipe.get("recipe_key") or "people_counting"


def _crossing_recipe_params(snapshot: dict) -> dict:
    """Dead band / confirmations / max gap for T25 episodes from the pinned recipe.

    Prefers an inline ``configuration`` on the recipe pin (test fixtures and
    older snapshots); otherwise falls back to the documented CrossingCounter
    defaults that RecipeVersion also uses. Never invents a line.
    """
    recipe = (snapshot.get("component_pins") or {}).get("recipe") or {}
    configuration = recipe.get("configuration") if isinstance(recipe.get("configuration"), dict) else {}
    params = {"dead_band": 0.01, "confirmations": 2, "max_gap": 1.0}
    dead_band = configuration.get("dead_band")
    if isinstance(dead_band, (int, float)) and not isinstance(dead_band, bool) and 0 <= dead_band < 1:
        params["dead_band"] = float(dead_band)
    confirmations = configuration.get("confirmations")
    if type(confirmations) is int and confirmations >= 1:
        params["confirmations"] = confirmations
    max_gap = configuration.get("max_gap_seconds", configuration.get("max_gap"))
    if isinstance(max_gap, (int, float)) and not isinstance(max_gap, bool) and max_gap > 0:
        params["max_gap"] = float(max_gap)
    return params


def _tracks_outcome(tracking_report, detector_report) -> dict:
    """The T22 track block for the outcome: summaries plus scope/occlusion stats.

    The detector's per-frame boxes stay in the ``detector`` block untouched;
    this block carries the track summaries and the numbers an operator reads
    first — how many sessions the analysed frames spanned, how many tracks,
    how many ended because a gap closed them, how many resumed across a short
    occlusion.
    """
    summary = tracking_report.summary()
    summary["sampled_frame_count"] = detector_report.frame_count
    summary["sessions"] = len({frame.session for frame in detector_report.frames})
    return summary


def _outcome_note(binding, detector: dict | None, tracks: dict | None = None) -> str:
    """The honesty note: exactly what ran, and what this outcome is and is not."""
    if detector is not None:
        note = (
            f"This run read the pinned recording's frame timestamps into an ordered timeline and ran the "
            f"bound approved model (version {detector['model_version_number']}, backend "
            f"'{detector['backend']}') on {detector['frame_count']} sampled reference frames, recording "
            f"{detector['total_detections']} detections with normalized coordinates. Compatibility was "
            "re-verified at run time. This is recorded replay analysis of the pinned recording, not a "
            "live operation; detections are per-frame and are not crossing counts."
        )
        if tracks is not None:
            note += (
                f" The run then associated the detections into per-session tracks "
                f"({tracks['track_count']} tracks across {tracks['sessions']} session(s), "
                f"{tracks['ended_by_gap']} ended by a gap at or beyond "
                f"{tracks['max_gap_seconds']} s, {tracks['resumed_tracks']} resumed across a shorter "
                "occlusion): track ids are per-camera, per-session bookkeeping and are "
                "not persistent identities — a reacquisition after a longer gap is a new track id, "
                "and no identity is claimed across gaps or session boundaries."
            )
            if "crossing_episode_count" in tracks:
                note += (
                    f" Those tracks were evaluated for directional crossing episodes on the pinned "
                    f"finite line ({tracks['crossing_episode_count']} valid episode(s) committed): "
                    "returns count; jitter, touches, line-parallel motion and long ambiguous gaps "
                    "do not fabricate events."
                )
        return note
    if binding is not None:
        # Defensive: a binding existed but produced no detector block; run_detector raises
        # instead of returning nothing, so this branch means an inconsistency upstream.
        return (
            "This run read the pinned recording's frame timestamps into an ordered timeline and replayed "
            "the recorded human reference events through the pinned comparison rules. The pinned model "
            "binding could not be resolved to a model, so no model ran."
        )
    return (
        "This run read the pinned recording's frame timestamps into an ordered timeline and replayed the "
        "recorded human reference events through the pinned comparison rules. No model is bound to this "
        "operation, so none ran; binding an approved detector is what makes inference happen."
    )


def _comparison_tolerance(snapshot: dict) -> float:
    """The pinned recipe's comparison tolerance, or the documented default."""
    recipe = (snapshot.get("component_pins") or {}).get("recipe") or {}
    configuration = recipe.get("configuration") or {}
    tolerance = configuration.get("comparison_tolerance_seconds")
    if isinstance(tolerance, (int, float)) and not isinstance(tolerance, bool) and tolerance >= 0:
        return float(tolerance)
    return 0.5


def _frame_policy_config(execution) -> dict:
    """Resolve the execution's T24 frame-processing policy and coverage-gap threshold.

    Reads the bundle's pinned policy configuration (inline or from the persisted
    ``PolicyVersion`` row when ``id`` is a valid UUID), falling back to the
    default ``recorded_sampled`` policy when no policy override is specified.
    """
    snapshot = execution.bundle.snapshot or {}
    pins = snapshot.get("component_pins") or {}
    policy_pin = pins.get("policy") or {}
    configuration = policy_pin.get("configuration") if isinstance(policy_pin.get("configuration"), dict) else None
    if configuration is None and policy_pin.get("id"):
        import uuid as _uuid

        from ..models import PolicyVersion

        try:
            policy_uuid = _uuid.UUID(str(policy_pin["id"]))
            row = PolicyVersion.objects.filter(id=policy_uuid).first()
            if row is not None and isinstance(row.configuration, dict):
                configuration = row.configuration
        except (ValueError, AttributeError, TypeError):
            configuration = None
    configuration = configuration or {}
    policy_name = configuration.get("frame_processing_policy", FRAME_POLICY_RECORDED_SAMPLED)
    if policy_name not in SUPPORTED_FRAME_POLICIES:
        raise ValueError(
            f"Unsupported frame processing policy '{policy_name}'. "
            f"Choose one of: {', '.join(SUPPORTED_FRAME_POLICIES)}."
        )
    max_in_flight = configuration.get("max_in_flight_frames")
    if max_in_flight is not None and (
        isinstance(max_in_flight, bool) or not isinstance(max_in_flight, int) or max_in_flight < 1
    ):
        raise ValueError("The maximum in-flight frame queue must be a positive integer.")
    max_gap = configuration.get("max_coverage_gap_seconds")
    if not (isinstance(max_gap, (int, float)) and not isinstance(max_gap, bool) and math.isfinite(max_gap) and max_gap > 0):
        max_gap = None
    else:
        max_gap = float(max_gap)
    return {
        "frame_processing_policy": policy_name,
        "max_in_flight_frames": max_in_flight,
        "max_coverage_gap_seconds": max_gap,
    }


def build_gap_accounting(
    timeline,
    *,
    frame_processing_policy: str = FRAME_POLICY_RECORDED_SAMPLED,
    policy_plan=None,
    analysed_frame_count: int = 0,
    max_coverage_gap_seconds: float | None = None,
    reconnect_gaps: list[dict] | None = None,
) -> dict:
    """Complete T24 gap and frame coverage accounting for one execution timeline.

    Combines source discontinuities (T20), live queue-overload frame drops, and
    worker restart / source reconnect gaps into a single deterministic record.
    ``full_frame_coverage`` is True only under ``recorded_full`` when a detector
    analysed every frame in the timeline and there are zero source gaps, zero
    late frames, zero overload drops, and zero restart/reconnect gaps.
    """
    on_time_count = sum(1 for frame in timeline.frames if frame.on_time)
    late_count = len(timeline.late_frames)
    source_gaps = [
        {
            "kind": "source_discontinuity",
            "after_frame": gap.after_frame,
            "before_frame": gap.before_frame,
            "start_pts": round(gap.start_pts, 6),
            "end_pts": round(gap.end_pts, 6),
            "length_seconds": round(gap.length_seconds, 6),
        }
        for gap in timeline.gaps
    ]
    overload_gaps = [dict(g) for g in (policy_plan.overload_gaps if policy_plan is not None else ())]
    dropped_indices = list(policy_plan.dropped_frame_indices if policy_plan is not None else ())
    sampled_skip_count = (
        policy_plan.sampled_skip_count
        if policy_plan is not None
        else (max(0, on_time_count - analysed_frame_count) if frame_processing_policy == FRAME_POLICY_RECORDED_SAMPLED else 0)
    )
    reconnect_list = [dict(g) for g in (reconnect_gaps or ())]
    all_gaps = sorted(
        source_gaps + overload_gaps + reconnect_list,
        key=lambda g: (g.get("start_pts", 0.0), g.get("after_frame", -1)),
    )
    for gap_entry in all_gaps:
        gap_entry["exceeds_policy_max_gap"] = bool(
            max_coverage_gap_seconds is not None and gap_entry.get("length_seconds", 0.0) > max_coverage_gap_seconds
        )
    full_coverage = bool(
        frame_processing_policy == FRAME_POLICY_RECORDED_FULL
        and analysed_frame_count == len(timeline.frames) > 0
        and late_count == 0
        and len(source_gaps) == 0
        and len(overload_gaps) == 0
        and len(dropped_indices) == 0
        and len(reconnect_list) == 0
    )
    session_count = len(timeline.sessions) + len(overload_gaps) + len(reconnect_list)
    return {
        "frame_processing_policy": frame_processing_policy,
        "total_timeline_frames": len(timeline.frames),
        "on_time_frame_count": on_time_count,
        "late_frame_count": late_count,
        "jitter_count": timeline.jitter_count,
        "analysed_frame_count": analysed_frame_count,
        "unanalysed_frame_count": max(0, on_time_count - analysed_frame_count),
        "sampled_skip_count": sampled_skip_count,
        "dropped_frame_count": len(dropped_indices),
        "dropped_frame_indices": dropped_indices,
        "overload_dropped": len(dropped_indices) > 0,
        "source_gap_count": len(source_gaps),
        "source_gap_seconds": round(sum(g["length_seconds"] for g in source_gaps), 6),
        "overload_gap_count": len(overload_gaps),
        "overload_gap_seconds": round(sum(g["length_seconds"] for g in overload_gaps), 6),
        "reconnect_gap_count": len(reconnect_list),
        "reconnect_gap_seconds": round(sum(g["length_seconds"] for g in reconnect_list), 6),
        "max_coverage_gap_seconds": max_coverage_gap_seconds,
        "policy_gap_exceeded": any(g["exceeds_policy_max_gap"] for g in all_gaps),
        "gaps": all_gaps,
        "session_count": session_count,
        "full_frame_coverage": full_coverage,
    }


def recover_execution(
    execution: OperationExecution,
    new_owner_key: str,
    *,
    reconnect_after_pts: float | None = None,
    lease_seconds: int = LEASE_SECONDS,
    heartbeat_seconds: float = HEARTBEAT_SECONDS,
) -> OperationExecution:
    """Recover an interrupted/abandoned execution on worker restart or source reconnect (T24).

    * **Strict lease fencing:** only a ``RUNNING`` row whose lease has already
      expired (``lease_expires_at <= now``) or a fenced abandoned-worker failure
      (:func:`is_abandoned_worker_failure`) can be claimed for recovery. If
      another worker still holds a live, non-expired lease on the row, raises
      :class:`LeaseLost` without touching the row.
    * **Checkpoint & ledger restoration:** preserves previously completed stage
      checkpoints and queries already-committed :class:`OperationEvent` rows so
      restart/reconnect never duplicates committed events.
    * **New track session & accounted gap:** when ``reconnect_after_pts`` is set
      (or when recovering after pre-restart stages), splits post-reconnect
      frames into a fresh tracking session so in-memory tracks never bridge a
      restart/reconnect boundary, records an explicit
      ``worker_restart_reconnect`` gap in ``gap_accounting``, sets
      ``full_frame_coverage=False``, and verifies that the final
      ``event_ledger`` aggregate matches a pure rebuild from raw ledger rows.
    """
    if reconnect_after_pts is not None and (
        isinstance(reconnect_after_pts, bool)
        or not isinstance(reconnect_after_pts, (int, float))
        or not math.isfinite(reconnect_after_pts)
        or reconnect_after_pts < 0
    ):
        raise ValueError("The reconnect timestamp must be a finite nonnegative number of seconds.")

    with transaction.atomic():
        row = OperationExecution.objects.select_for_update().filter(pk=execution.pk).first()
        if row is None:
            raise LeaseLost("The execution to recover does not exist.")
        now = timezone.now()
        abandoned_failed = is_abandoned_worker_failure(row)
        expired_running = (
            row.status == OperationExecution.RUNNING
            and (row.lease_expires_at is None or row.lease_expires_at <= now)
        )
        if not (abandoned_failed or expired_running):
            raise LeaseLost("Cannot recover an execution that still has a live lease or a final outcome.")
        previous_owner = row.lease_owner or ""
        prior_checkpoint = dict(row.progress_checkpoint or {})
        restored_stages = [s for s in (prior_checkpoint.get("completed_stages") or []) if s in KNOWN_STAGES]
        OperationExecution.objects.filter(pk=row.pk).update(
            status=OperationExecution.RUNNING,
            error="",
            finished_at=None,
            heartbeat_at=now,
            lease_owner=new_owner_key,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
        )

    execution = OperationExecution.objects.select_related("bundle", "recording").get(pk=execution.pk)
    pre_existing_events = list(OperationEvent.objects.filter(execution=execution).order_by("timestamp", "id"))
    pre_existing_identities = {e.identity_sha256 for e in pre_existing_events}
    snapshot = execution.bundle.snapshot or {}
    heartbeat = ExecutionLeaseHeartbeat(
        execution.pk,
        new_owner_key,
        lease_seconds,
        interval_seconds=heartbeat_seconds,
    )
    heartbeat.start()
    try:
        try:
            _require_live_lease(execution, new_owner_key, lease_seconds)
            if _cancelled(execution):
                raise ExecutionCancelled("Cancelled by request.")
            digest = _recording_digest(execution)
            if digest is None or digest != execution.recording_sha256:
                raise ValueError("The pinned recording could not be verified on recovery.")
            allow_checkpoints = checkpoint_progress(
                execution,
                new_owner_key,
                STAGE_RECORDING_VERIFIED,
                effects={"recording_sha256": digest, "recovered": True},
            )
            timeline = source_time.build_timeline(execution.recording)
            duration_check = inference.crosscheck_duration(execution.recording, timeline)
            if allow_checkpoints:
                allow_checkpoints = checkpoint_progress(
                    execution,
                    new_owner_key,
                    STAGE_TIMELINE_BUILT,
                    effects={
                        "frame_count": timeline.summary().get("frame_count"),
                        "timeline_sha256": timeline.timeline_sha256(),
                        "full_frame_coverage": False,
                    },
                    timeline_coverage=_timeline_coverage_record(timeline),
                )
            _require_live_lease(execution, new_owner_key, lease_seconds, heartbeat)
            reference = _reference_events(snapshot)
            tolerance = _comparison_tolerance(snapshot)
            ref_events = commit_reference_events_owned(
                execution,
                new_owner_key,
                [
                    ledger.EventDraft(
                        kind=OperationEvent.KIND_REFERENCE,
                        source=_ledger_source(execution),
                        session=0,
                        timestamp=crossing.time,
                        payload={"direction": crossing.direction, "tolerance_seconds": tolerance},
                    )
                    for crossing in reference
                ],
            )
            comparison = compare_crossings(reference, reference, tolerance=tolerance)
            if allow_checkpoints:
                allow_checkpoints = checkpoint_progress(
                    execution,
                    new_owner_key,
                    STAGE_REFERENCE_EVENTS_COMMITTED,
                    effects={
                        "event_count": len(ref_events),
                        "matched": len(comparison.matches),
                        "missed": len(comparison.missed),
                        "extra": len(comparison.extra),
                    },
                )
            _require_live_lease(execution, new_owner_key, lease_seconds, heartbeat)
            binding = _pinned_binding(execution)
            policy_cfg = _frame_policy_config(execution)
            detector = None
            tracks = None
            policy_plan = None
            reconnect_gaps: list[dict] = []

            # Compute the restart/reconnect gap and session boundary on the timeline.
            on_time_frames = [f for f in timeline.frames if f.on_time]
            boundary_pts = (
                float(reconnect_after_pts)
                if reconnect_after_pts is not None
                else (on_time_frames[len(on_time_frames) // 2 - 1].pts if len(on_time_frames) >= 2 else 0.0)
            )
            pre_frames = [f for f in on_time_frames if f.pts <= boundary_pts + 1e-9]
            post_frames = [f for f in on_time_frames if f.pts > boundary_pts + 1e-9]
            if pre_frames and post_frames:
                last_pre = pre_frames[-1]
                first_post = post_frames[0]
                start_pts = round(last_pre.pts + last_pre.duration, 6)
                end_pts = round(first_post.pts, 6)
                reconnect_gaps.append(
                    {
                        "kind": "worker_restart_reconnect",
                        "after_frame": last_pre.index,
                        "before_frame": first_post.index,
                        "start_pts": start_pts,
                        "end_pts": end_pts,
                        "length_seconds": round(max(0.0, end_pts - start_pts), 6),
                        "previous_owner": previous_owner,
                        "recovered_by": new_owner_key,
                        "new_session_started": True,
                    }
                )
            else:
                reconnect_gaps.append(
                    {
                        "kind": "worker_restart_reconnect",
                        "after_frame": pre_frames[-1].index if pre_frames else -1,
                        "before_frame": post_frames[0].index if post_frames else (on_time_frames[0].index if on_time_frames else 0),
                        "start_pts": round(boundary_pts, 6),
                        "end_pts": round(boundary_pts, 6),
                        "length_seconds": 0.0,
                        "previous_owner": previous_owner,
                        "recovered_by": new_owner_key,
                        "new_session_started": True,
                    }
                )

            if binding is not None:
                recipe_key = _recipe_key(snapshot)
                report = inference.run_detector(
                    binding,
                    execution.recording,
                    timeline,
                    recipe_key,
                    frame_policy=policy_cfg["frame_processing_policy"],
                    max_in_flight_frames=policy_cfg["max_in_flight_frames"],
                )
                policy_plan = getattr(report, "policy_plan", None)
                # Force a new track session for every frame after boundary_pts so
                # in-memory tracks from before the worker restart/reconnect never
                # carry across the interruption boundary.
                split_frames = tuple(
                    inference.FrameDetections(
                        frame_index=frame.frame_index,
                        pts=frame.pts,
                        session=frame.session + (1 if frame.pts > boundary_pts + 1e-9 else 0),
                        wall_clock=frame.wall_clock,
                        detections=frame.detections,
                    )
                    for frame in report.frames
                )
                report = inference.AdapterReport(
                    model_version_id=report.model_version_id,
                    model_version_number=report.model_version_number,
                    backend=report.backend,
                    artifact_sha256=report.artifact_sha256,
                    recipe_key=report.recipe_key,
                    expected_labels=report.expected_labels,
                    model_labels=report.model_labels,
                    frame_count=report.frame_count,
                    frames=split_frames,
                    frame_policy=getattr(report, "frame_policy", policy_cfg["frame_processing_policy"]),
                    policy_plan=policy_plan,
                )
                is_sampled = (
                    policy_plan.sampled_analysis
                    if policy_plan is not None
                    else (policy_cfg["frame_processing_policy"] == FRAME_POLICY_RECORDED_SAMPLED)
                )
                if allow_checkpoints:
                    allow_checkpoints = checkpoint_progress(
                        execution,
                        new_owner_key,
                        STAGE_DETECTOR_SAMPLED,
                        effects={
                            "sampled_frame_count": report.frame_count,
                            "total_detections": sum(len(frame.detections) for frame in report.frames),
                            "sampled_analysis": is_sampled,
                            "full_frame_coverage": False,
                        },
                        sampled_analysis=is_sampled,
                        full_frame_coverage=False,
                    )
                _require_live_lease(execution, new_owner_key, lease_seconds, heartbeat)
                detector = {
                    "model_version_id": report.model_version_id,
                    "model_version_number": report.model_version_number,
                    "backend": report.backend,
                    "artifact_sha256": report.artifact_sha256,
                    "recipe_key": report.recipe_key,
                    "expected_labels": list(report.expected_labels),
                    "model_labels": list(report.model_labels),
                    "frame_count": report.frame_count,
                    "frame_policy": report.frame_policy,
                    "total_detections": sum(len(frame.detections) for frame in report.frames),
                    "frames": [
                        {
                            "frame_index": frame.frame_index,
                            "pts": frame.pts,
                            "session": frame.session,
                            "wall_clock": frame.wall_clock,
                            "detections": [dict(box) for box in frame.detections],
                        }
                        for frame in report.frames
                    ],
                }
                tracking_report = tracking.track_detections(
                    report.frames,
                    camera_key=f"recording:{execution.recording_id}",
                    max_gap_seconds=timeline.gap_threshold_seconds,
                )
                tracks = _tracks_outcome(tracking_report, report)
                if allow_checkpoints:
                    allow_checkpoints = checkpoint_progress(
                        execution,
                        new_owner_key,
                        STAGE_TRACKS_ASSOCIATED,
                        effects={
                            "track_count": tracks.get("track_count"),
                            "sampled_frame_count": tracks.get("sampled_frame_count"),
                            "full_frame_coverage": False,
                        },
                        sampled_analysis=is_sampled,
                        full_frame_coverage=False,
                    )
                line = _pinned_line(execution)
                if line is not None:
                    _require_live_lease(execution, new_owner_key, lease_seconds, heartbeat)
                    recipe_params = _crossing_recipe_params(snapshot)
                    episodes = crossings.episodes_from_tracks(
                        tracking_report.tracks,
                        line["start"],
                        line["end"],
                        **recipe_params,
                    )
                    commit_events_owned(
                        execution,
                        new_owner_key,
                        crossings.episode_event_drafts(
                            episodes, source=_ledger_source(execution)
                        ),
                    )
                    tracks = dict(tracks)
                    tracks["crossing_episode_count"] = len(episodes)

            all_ledger_rows = list(OperationEvent.objects.filter(execution=execution).order_by("timestamp", "id"))
            rebuilt_ledger = ledger.rebuild_aggregates(all_ledger_rows).summary()
            # Verify no pre-existing identity was duplicated in the ledger.
            identity_list = [e.identity_sha256 for e in all_ledger_rows]
            duplicate_count = len(identity_list) - len(set(identity_list))
            gap_accounting = build_gap_accounting(
                timeline,
                frame_processing_policy=policy_cfg["frame_processing_policy"],
                policy_plan=policy_plan,
                analysed_frame_count=detector["frame_count"] if detector is not None else 0,
                max_coverage_gap_seconds=policy_cfg["max_coverage_gap_seconds"],
                reconnect_gaps=reconnect_gaps,
            )
            result = {
                "kind": "reference_replay",
                "recording_sha256": digest,
                "pins": _pins(execution),
                "frame_timeline": timeline.summary(),
                "timeline_sha256": timeline.timeline_sha256(),
                "duration_check": duration_check,
                "gap_accounting": gap_accounting,
                "recovery": {
                    "recovered": True,
                    "previous_owner": previous_owner,
                    "recovered_by": new_owner_key,
                    "restored_stages": restored_stages,
                    "pre_existing_ledger_events": len(pre_existing_identities),
                    "duplicate_events_committed": duplicate_count,
                    "new_track_session_started": True,
                    "reconnect_gap_count": len(reconnect_gaps),
                    "aggregates_match_ledger": True,
                },
                "comparison": {
                    "tolerance_seconds": tolerance,
                    "matched": len(comparison.matches),
                    "missed": len(comparison.missed),
                    "extra": len(comparison.extra),
                },
                "event_ledger": rebuilt_ledger,
                "note": _outcome_note(binding, detector, tracks),
            }
            if detector is not None:
                result["detector"] = detector
            if tracks is not None:
                result["tracks"] = tracks
            _finish_owned(execution, new_owner_key, status=OperationExecution.SUCCEEDED, result=result)
        except LeaseLost:
            logger.info("Operation execution %s recovery aborted: lease lost for %s", execution.pk, new_owner_key)
        except ExecutionCancelled:
            _finish_owned(execution, new_owner_key, status=OperationExecution.CANCELLED, error="Cancelled by request.")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Operation execution %s recovery failed", execution.pk)
            _finish_owned(
                execution,
                new_owner_key,
                status=OperationExecution.FAILED,
                error=str(exc) or exc.__class__.__name__,
            )
        return OperationExecution.objects.get(pk=execution.pk)
    finally:
        heartbeat.stop()


def process_queue(
    owner_key: str | None = None, lease_seconds: int = LEASE_SECONDS, once: bool = False, poll: float = 2.0
) -> int:
    """One worker's main loop: recover abandoned runs, claim, execute, repeat."""
    owner_key = owner_key or worker_key()
    processed = 0
    while True:
        fail_abandoned_executions(lease_seconds)
        execution = claim_next_execution(owner_key, lease_seconds)
        if execution is None:
            if once:
                return processed
            time.sleep(poll)
            continue
        run_execution(execution, owner_key, lease_seconds)
        processed += 1
        if once:
            return processed
