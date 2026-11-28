"""Event-linked learning examples and verified negatives (T33).

Bridge from diagnosis (T32) into review (T34) and dataset splits (T35).

Contracts:
* Provenance on every row: site, recording digest, time, optional execution/event,
  and only authorized evidence references (no media bytes, no credentials).
* Content-addressed dedupe per operation so capture retries do not invent rows.
* Explicit verified negatives are first-class — never inferred from a silent frame.
* Capture from a succeeded execution only uses diagnosis items categorized as
  recognition (training-relevant). Source/geometry/rules mismatches stay out.

Verified by ``projects.tests_t33_examples``.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Iterable

from django.db import IntegrityError, transaction
from django.utils import timezone

from ..models import (
    Operation,
    OperationEvent,
    OperationExecution,
    OperationLearningExample,
    OperationSourceRecording,
)

SCHEMA = "t33.learning_example.v1"
_TIMESTAMP_QUANTUM = 1_000_000


class ExampleError(ValueError):
    """A learning-example refusal, with a plain-language reason."""


def quantize_time(value) -> int:
    """Microsecond-quantized timeline stamp (same family as the ledger)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExampleError("Provide a numeric time on the recording timeline.")
    if not math.isfinite(float(value)):
        raise ExampleError("Provide a finite time on the recording timeline.")
    return int(round(float(value) * _TIMESTAMP_QUANTUM))


def canonical_json(payload: dict) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ExampleError("Example identity payload is not canonically JSON-serializable.") from error


def content_identity(
    *,
    operation_id,
    recording_sha256: str,
    time_pts: float,
    polarity: str,
    selection_kind: str,
    event_key: str = "",
) -> str:
    """SHA-256 identity used to dedupe selections inside one operation."""
    body = {
        "event_key": event_key or "",
        "operation_id": str(operation_id),
        "polarity": polarity,
        "recording_sha256": recording_sha256,
        "selection_kind": selection_kind,
        "time_us": quantize_time(time_pts),
    }
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def _event_key(identity: dict | None) -> str:
    if not identity:
        return ""
    parts = [
        str(identity.get("kind") or ""),
        str(identity.get("bay_id") or identity.get("lane") or identity.get("direction") or ""),
        str(identity.get("reason") or ""),
    ]
    return "|".join(parts)


def serialize_example(row: OperationLearningExample) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "id": str(row.id),
        "operation_id": str(row.operation_id),
        "site_id": str(row.site_id),
        "execution_id": str(row.execution_id) if row.execution_id else None,
        "event_id": str(row.event_id) if row.event_id else None,
        "recording_id": str(row.recording_id) if row.recording_id else None,
        "recording_sha256": row.recording_sha256,
        "bundle_sha256": row.bundle_sha256 or None,
        "time_pts": row.time_pts,
        "polarity": row.polarity,
        "selection_kind": row.selection_kind,
        "status": row.status,
        "content_sha256": row.content_sha256,
        "evidence": row.evidence or {},
        "event_identity": row.event_identity or {},
        "note": row.note or "",
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "verified_at": row.verified_at.isoformat() if row.verified_at else None,
        "verified_by_id": row.verified_by_id,
    }


@dataclass
class CaptureResult:
    created: list[OperationLearningExample]
    existing: list[OperationLearningExample]
    skipped: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "t33.capture_result.v1",
            "created": [serialize_example(r) for r in self.created],
            "existing": [serialize_example(r) for r in self.existing],
            "skipped": list(self.skipped),
            "counts": {
                "created": len(self.created),
                "existing": len(self.existing),
                "skipped": len(self.skipped),
            },
        }


def _authorized_evidence(*, diagnosis_item: dict, execution: OperationExecution) -> dict[str, Any]:
    """Strip anything that is not an authorized reference."""
    allowed_item_keys = (
        "kind",
        "category",
        "reason",
        "time",
        "bay_id",
        "direction",
        "label",
        "predicted_time",
        "reference_time",
        "iou",
    )
    slim = {k: diagnosis_item[k] for k in allowed_item_keys if k in diagnosis_item}
    return {
        "execution_id": str(execution.id),
        "bundle_sha256": execution.bundle_sha256,
        "recording_sha256": execution.recording_sha256,
        "diagnosis_item": slim,
    }


def candidates_from_diagnosis(diagnosis: dict | None) -> list[dict[str, Any]]:
    """Turn T32 diagnosis items into capture candidates (recognition only)."""
    if not diagnosis:
        return []
    out: list[dict[str, Any]] = []

    def take(items: Iterable[dict], *, default_polarity: str, default_kind: str) -> None:
        for item in items or []:
            if not isinstance(item, dict):
                continue
            category = item.get("category") or ""
            if category and category != "recognition":
                continue
            # Items without category (parking already filtered) still need a time.
            try:
                t = float(item.get("time"))
            except (TypeError, ValueError):
                continue
            if not math.isfinite(t):
                continue
            kind = item.get("kind") or default_kind
            if kind in ("miss", "missed_transition"):
                polarity = OperationLearningExample.POLARITY_POSITIVE
                selection_kind = OperationLearningExample.KIND_MISS
            elif kind in ("extra", "false_free"):
                polarity = OperationLearningExample.POLARITY_NEGATIVE
                selection_kind = OperationLearningExample.KIND_EXTRA
            else:
                polarity = default_polarity
                selection_kind = default_kind
            identity = {
                "kind": kind,
                "bay_id": item.get("bay_id"),
                "direction": item.get("direction"),
                "reason": item.get("reason"),
                "label": item.get("label"),
            }
            out.append(
                {
                    "time_pts": t,
                    "polarity": polarity,
                    "selection_kind": selection_kind,
                    "event_identity": {k: v for k, v in identity.items() if v is not None and v != ""},
                    "item": item,
                }
            )

    comparison = diagnosis.get("comparison") or {}
    take(comparison.get("items") or [], default_polarity=OperationLearningExample.POLARITY_POSITIVE, default_kind=OperationLearningExample.KIND_MISS)
    parking = diagnosis.get("parking") or {}
    take(parking.get("items") or [], default_polarity=OperationLearningExample.POLARITY_NEGATIVE, default_kind=OperationLearningExample.KIND_EXTRA)
    return out


@transaction.atomic
def capture_from_execution(*, execution: OperationExecution, user) -> CaptureResult:
    """Capture learning-example candidates from a succeeded execution's diagnosis."""
    if execution.status != OperationExecution.SUCCEEDED:
        raise ExampleError("Only a finished successful run can yield learning examples.")
    result = execution.result or {}
    diagnosis = result.get("diagnosis")
    if not isinstance(diagnosis, dict):
        raise ExampleError("This run has no diagnosis block to capture from.")
    if not diagnosis.get("recommend_training") and not (diagnosis.get("comparison") or {}).get("items"):
        # Still allow parking-only recognition items via candidates_from_diagnosis.
        pass

    operation = execution.bundle.operation
    if operation.created_by_id != user.id and not getattr(user, "is_superuser", False):
        # Owner-scope mirrors other operation routes; superuser test fixtures allowed.
        if operation.created_by_id != getattr(user, "pk", None):
            raise ExampleError("That operation run is not yours.")

    candidates = candidates_from_diagnosis(diagnosis)
    created: list[OperationLearningExample] = []
    existing: list[OperationLearningExample] = []
    skipped: list[dict[str, Any]] = []

    for cand in candidates:
        try:
            digest = content_identity(
                operation_id=operation.id,
                recording_sha256=execution.recording_sha256,
                time_pts=cand["time_pts"],
                polarity=cand["polarity"],
                selection_kind=cand["selection_kind"],
                event_key=_event_key(cand.get("event_identity")),
            )
        except ExampleError as error:
            skipped.append({"reason": str(error), "item": cand.get("item")})
            continue

        prior = OperationLearningExample.objects.filter(operation=operation, content_sha256=digest).first()
        if prior is not None:
            existing.append(prior)
            continue

        evidence = _authorized_evidence(diagnosis_item=cand.get("item") or {}, execution=execution)
        try:
            row = OperationLearningExample.objects.create(
                operation=operation,
                site=operation.site,
                execution=execution,
                recording=execution.recording,
                recording_sha256=execution.recording_sha256,
                bundle_sha256=execution.bundle_sha256,
                time_pts=float(cand["time_pts"]),
                polarity=cand["polarity"],
                selection_kind=cand["selection_kind"],
                status=OperationLearningExample.STATUS_CANDIDATE,
                content_sha256=digest,
                evidence=evidence,
                event_identity=cand.get("event_identity") or {},
                created_by=user,
            )
        except IntegrityError:
            prior = OperationLearningExample.objects.get(operation=operation, content_sha256=digest)
            existing.append(prior)
            continue
        created.append(row)

    return CaptureResult(created=created, existing=existing, skipped=skipped)


@transaction.atomic
def add_example(
    *,
    operation: Operation,
    user,
    time_pts: float,
    polarity: str,
    selection_kind: str,
    recording: OperationSourceRecording | None = None,
    recording_sha256: str | None = None,
    execution: OperationExecution | None = None,
    event: OperationEvent | None = None,
    event_identity: dict | None = None,
    evidence: dict | None = None,
    note: str = "",
    status: str = OperationLearningExample.STATUS_CANDIDATE,
) -> tuple[OperationLearningExample, bool]:
    """Create one example (or return the existing deduped row). Returns (row, created)."""
    if polarity not in (
        OperationLearningExample.POLARITY_POSITIVE,
        OperationLearningExample.POLARITY_NEGATIVE,
    ):
        raise ExampleError("Polarity must be positive or negative.")
    if selection_kind not in dict(OperationLearningExample.KIND_CHOICES):
        raise ExampleError("Unknown selection kind.")
    if selection_kind == OperationLearningExample.KIND_VERIFIED_NEGATIVE:
        if polarity != OperationLearningExample.POLARITY_NEGATIVE:
            raise ExampleError("A verified negative must use negative polarity.")
        if status == OperationLearningExample.STATUS_CANDIDATE:
            # Explicit verified negatives land verified immediately.
            status = OperationLearningExample.STATUS_VERIFIED

    if execution is not None:
        if execution.bundle.operation_id != operation.id:
            raise ExampleError("That run does not belong to this operation.")
        recording = recording or execution.recording
        recording_sha256 = recording_sha256 or execution.recording_sha256
        bundle_sha256 = execution.bundle_sha256
    else:
        bundle_sha256 = ""

    if recording is not None:
        if recording.source_id != operation.source_id:
            raise ExampleError("Choose a recording that belongs to this operation's source.")
        recording_sha256 = recording_sha256 or recording.sha256

    if not recording_sha256 or len(recording_sha256) != 64:
        raise ExampleError("Provide the recording digest (64 hex characters).")

    event_identity = dict(event_identity or {})
    if event is not None:
        if event.execution.bundle.operation_id != operation.id:
            raise ExampleError("That ledger event does not belong to this operation.")
        event_identity.setdefault("ledger_identity_sha256", event.identity_sha256)
        event_identity.setdefault("kind", event.kind)
        recording_sha256 = recording_sha256 or event.recording_sha256

    digest = content_identity(
        operation_id=operation.id,
        recording_sha256=recording_sha256,
        time_pts=time_pts,
        polarity=polarity,
        selection_kind=selection_kind,
        event_key=_event_key(event_identity),
    )
    prior = OperationLearningExample.objects.filter(operation=operation, content_sha256=digest).first()
    if prior is not None:
        return prior, False

    # Authorized evidence only — drop unknown top-level secrets-shaped keys.
    safe_evidence = {}
    if isinstance(evidence, dict):
        for key, value in evidence.items():
            lk = str(key).lower()
            if any(s in lk for s in ("password", "secret", "token", "credential", "authorization")):
                continue
            safe_evidence[key] = value
    safe_evidence.setdefault("recording_sha256", recording_sha256)

    verified_by = user if status == OperationLearningExample.STATUS_VERIFIED else None
    verified_at = timezone.now() if status == OperationLearningExample.STATUS_VERIFIED else None

    try:
        row = OperationLearningExample.objects.create(
            operation=operation,
            site=operation.site,
            execution=execution,
            event=event,
            recording=recording,
            recording_sha256=recording_sha256,
            bundle_sha256=bundle_sha256,
            time_pts=float(time_pts),
            polarity=polarity,
            selection_kind=selection_kind,
            status=status,
            content_sha256=digest,
            evidence=safe_evidence,
            event_identity=event_identity,
            note=(note or "")[:500],
            created_by=user,
            verified_by=verified_by,
            verified_at=verified_at,
        )
    except IntegrityError:
        return OperationLearningExample.objects.get(operation=operation, content_sha256=digest), False
    return row, True


@transaction.atomic
def set_example_status(*, example: OperationLearningExample, user, status: str, note: str | None = None) -> OperationLearningExample:
    if status not in (
        OperationLearningExample.STATUS_CANDIDATE,
        OperationLearningExample.STATUS_VERIFIED,
        OperationLearningExample.STATUS_REJECTED,
    ):
        raise ExampleError("Status must be candidate, verified, or rejected.")
    example.status = status
    if note is not None:
        example.note = note[:500]
    if status == OperationLearningExample.STATUS_VERIFIED:
        example.verified_by = user
        example.verified_at = timezone.now()
    elif status == OperationLearningExample.STATUS_REJECTED:
        example.verified_by = user
        example.verified_at = timezone.now()
    else:
        example.verified_by = None
        example.verified_at = None
    example.save(update_fields=["status", "note", "verified_by", "verified_at"])
    return example


def list_examples(*, operation: Operation, status: str | None = None, polarity: str | None = None) -> list[OperationLearningExample]:
    qs = OperationLearningExample.objects.filter(operation=operation).select_related(
        "execution", "recording", "event", "site"
    )
    if status:
        qs = qs.filter(status=status)
    if polarity:
        qs = qs.filter(polarity=polarity)
    return list(qs.order_by("-created_at", "-id")[:500])
