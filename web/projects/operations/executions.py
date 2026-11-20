"""Durable operation executions: the lease, the claim, and the worker-side run (T19).

The web process only queues ``OperationExecution`` rows
(:mod:`projects.execution_views`). Worker processes claim a row through a lease
(``lease_owner`` + ``lease_expires_at``), refresh it while they work, and finish
it with a guarded ``UPDATE`` that can never write a second outcome. The claim is
atomic, so several operation workers can share one queue; recovery is by lease
expiry, not by start-up convention, so one worker's start-up never fails
another's in-flight execution.
"""

from __future__ import annotations

import hashlib
import logging
import os
import socket
import time
from datetime import timedelta

from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone

from .. import source_time
from ..models import OperationExecution
from . import inference
from .comparison import Crossing, compare_crossings

logger = logging.getLogger(__name__)

#: Default lease length. A worker refreshes it every 10 s, so a lease only
#: expires when its owner has missed several refresh windows in a row.
LEASE_SECONDS = 60
#: A heartbeat every 10 s, exactly like the training/inference/autolabel workers.
HEARTBEAT_SECONDS = 10
MAX_ERROR_CHARS = 600


def worker_key() -> str:
    """A stable, plain key identifying this worker process in leases and logs."""
    return f"{socket.gethostname()}:{os.getpid()}"


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


def _finish(execution, *, status: str, error: str = "", result: dict | None = None) -> bool:
    """Record one terminal outcome. Returns False when someone else already did.

    The ``WHERE status='running'`` guard is the no-duplicate guarantee: after a
    crash-and-recovery (or a cancel racing a finish), the guard matches no row
    and the late writer changes nothing. This is the training SIGKILL pattern.
    """
    changed = OperationExecution.objects.filter(pk=execution.pk, status=OperationExecution.RUNNING).update(
        status=status,
        finished_at=timezone.now(),
        error=error[:MAX_ERROR_CHARS],
        result=result or {},
        lease_owner="",
        lease_expires_at=None,
    )
    return bool(changed)


def _beat(execution, owner_key: str, lease_seconds: int) -> None:
    """Refresh heartbeat and lease on one running execution (idempotent, cheap)."""
    now = timezone.now()
    OperationExecution.objects.filter(pk=execution.pk, status=OperationExecution.RUNNING, lease_owner=owner_key).update(
        heartbeat_at=now, lease_expires_at=now + timedelta(seconds=lease_seconds)
    )


def fail_abandoned_executions(lease_seconds: int = LEASE_SECONDS) -> int:
    """Fail executions whose worker died mid-run, and only those.

    An execution is abandoned when its lease has expired by a full extra lease
    interval — the owner missed its refresh window twice — so a worker that is
    merely slow, or whose clock ticked between refreshes, is never failed. This
    replaces the single-worker "fail everything running at start-up" convention:
    worker A's start-up cannot fail worker B's in-flight execution, because B's
    lease is still fresh. A failed execution is re-queueable: enqueue creates a
    new row, and the terminal guard means the dead worker can never also write.
    """
    now = timezone.now()
    deadline = now - timedelta(seconds=2 * lease_seconds)
    abandoned = OperationExecution.objects.filter(status=OperationExecution.RUNNING, lease_expires_at__lt=deadline)
    count = 0
    for execution in abandoned:
        if _finish(
            execution,
            status=OperationExecution.FAILED,
            error="The operation worker stopped before this run finished. Start a new run.",
        ):
            count += 1
    return count


# -- the execution step ----------------------------------------------------------------------
class ExecutionCancelled(Exception):
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


def _pins(execution) -> dict:
    """The pins frozen at enqueue: bundle snapshot pins, plus digests provenance."""
    pins = dict(execution.pins or {})
    pins.setdefault("bundle_sha256", execution.bundle_sha256)
    pins.setdefault("recording_sha256", execution.recording_sha256)
    return pins


def run_execution(
    execution: OperationExecution, owner_key: str | None = None, lease_seconds: int = LEASE_SECONDS
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
    normalized detections. What the step never claims: this is recorded replay
    analysis on sampled frames, not a live operation, and it is not tracking
    (T22) or crossing episodes (T25).
    """
    owner_key = owner_key or execution.lease_owner or worker_key()
    snapshot = execution.bundle.snapshot or {}
    try:
        _beat(execution, owner_key, lease_seconds)
        if _cancelled(execution):
            raise ExecutionCancelled("Cancelled by request.")
        digest = _recording_digest(execution)
        if digest is None:
            raise ValueError("The pinned recording could not be read from storage. Ask your administrator to check it.")
        if digest != execution.recording_sha256:
            raise ValueError(
                "The pinned recording changed on disk since the run was queued. Start a new run from the new file."
            )
        if _cancelled(execution):
            raise ExecutionCancelled("Cancelled by request.")
        # T20: read the pinned recording's frame timestamps and derive the
        # ordered processing timeline (frame count, gaps, sessions), then close
        # T20's known gap: the stored duration must match the timeline's span.
        timeline = source_time.build_timeline(execution.recording)
        duration_check = inference.crosscheck_duration(execution.recording, timeline)
        _beat(execution, owner_key, lease_seconds)
        if _cancelled(execution):
            raise ExecutionCancelled("Cancelled by request.")
        reference = _reference_events(snapshot)
        tolerance = _comparison_tolerance(snapshot)
        # The stand-in "predicted" stream is the reference itself shifted by zero: an honest
        # placeholder that exercises the pinned recipe's comparison semantics end to end.
        comparison = compare_crossings(reference, reference, tolerance=tolerance)
        _beat(execution, owner_key, lease_seconds)
        if _cancelled(execution):
            raise ExecutionCancelled("Cancelled by request.")
        # T21: a pinned model binding means real detector inference on sampled
        # reference frames, with compatibility re-validated at run time.
        binding = _pinned_binding(execution)
        detector = None
        if binding is not None:
            recipe_key = _recipe_key(snapshot)
            report = inference.run_detector(binding, execution.recording, timeline, recipe_key)
            _beat(execution, owner_key, lease_seconds)
            detector = {
                "model_version_id": report.model_version_id,
                "model_version_number": report.model_version_number,
                "backend": report.backend,
                "artifact_sha256": report.artifact_sha256,
                "recipe_key": report.recipe_key,
                "expected_labels": list(report.expected_labels),
                "model_labels": list(report.model_labels),
                "frame_count": report.frame_count,
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
        result = {
            "kind": "reference_replay",
            "recording_sha256": digest,
            "pins": _pins(execution),
            "frame_timeline": timeline.summary(),
            "timeline_sha256": timeline.timeline_sha256(),
            "duration_check": duration_check,
            "comparison": {
                "tolerance_seconds": tolerance,
                "matched": len(comparison.matches),
                "missed": len(comparison.missed),
                "extra": len(comparison.extra),
            },
            "note": _outcome_note(binding, detector),
        }
        if detector is not None:
            result["detector"] = detector
        _finish(execution, status=OperationExecution.SUCCEEDED, result=result)
    except ExecutionCancelled:
        _finish(execution, status=OperationExecution.CANCELLED, error="Cancelled by request.")
    except Exception as exc:  # noqa: BLE001 - recorded on the row for the operator
        logger.exception("Operation execution %s failed", execution.pk)
        _finish(execution, status=OperationExecution.FAILED, error=str(exc) or exc.__class__.__name__)
    return OperationExecution.objects.get(pk=execution.pk)


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


def _recipe_key(snapshot: dict) -> str:
    """The recipe key the bundle's snapshot pinned (the operation domain's one recipe)."""
    recipe = (snapshot.get("component_pins") or {}).get("recipe") or {}
    return recipe.get("recipe_key") or "people_counting"


def _outcome_note(binding, detector: dict | None) -> str:
    """The honesty note: exactly what ran, and what this outcome is and is not."""
    if detector is not None:
        return (
            f"This run read the pinned recording's frame timestamps into an ordered timeline and ran the "
            f"bound approved model (version {detector['model_version_number']}, backend "
            f"'{detector['backend']}') on {detector['frame_count']} sampled reference frames, recording "
            f"{detector['total_detections']} detections with normalized coordinates. Compatibility was "
            "re-verified at run time. This is recorded replay analysis of the pinned recording, not a "
            "live operation; detections are per-frame and are not tracks or crossing counts."
        )
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
