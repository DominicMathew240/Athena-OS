"""The durable operation event ledger: identity, idempotent commit, aggregates (T23).

This module is the single commit path for operation events. Its contracts:

* **Content-addressed identity.** An event's identity is the SHA-256 over the
  execution id, the source stream, the kind, the session, the timestamp and the
  payload's canonical JSON — so the same event computed twice yields the same
  identity, and a different payload under the same event slot (same execution,
  source, kind, session and timestamp) yields a *different* identity that the
  database refuses (the slot is unique), loudly.
* **Idempotent commit.** ``commit_events`` inserts each event unless its exact
  identity is already committed; a retry or replay after a crash commits
  nothing new and returns what already exists. A conflicting payload under the
  same slot raises ``ConflictingEventError`` — never a silent merge.
* **Raw events rebuild aggregates.** ``rebuild_aggregates`` is a pure function
  over raw event rows alone: event count, counts by kind, counts by session,
  first/last timestamp and the set of identities. The recorded aggregate and a
  rebuild from the raw rows must agree — that equality is what makes the ledger
  the source of truth rather than a side effect.

What this module deliberately does NOT do: it does not count crossings (T25's
crossing episodes consume this ledger), it defines no site time zones or shift
boundaries (T27's period aggregates), and its identity is scoped to one
execution — the same real-world crossing executed twice is two events, by
design (the execution, not the world, is the ledger's idempotency scope).

Verified by ``projects.tests_t23_ledger`` with in-process calls and real
database rows; no multi-process or broker execution is exercised (same honest
limit as T19).
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass

from django.db import IntegrityError, transaction

from ..models import OperationEvent, OperationExecution


class LedgerError(ValueError):
    """A ledger refusal, with a plain-language reason."""


class ConflictingEventError(LedgerError):
    """A different payload claims the same event slot as a committed event."""


#: Timestamps are quantized to microseconds so identities never carry float
#: noise (the same quantization T20's timeline hashes use).
_TIMESTAMP_QUANTUM = 1_000_000


def canonical_payload(payload) -> str:
    """The canonical JSON every event hash is taken over.

    Same rules as the release-bundle and timeline digests: sorted keys, tight
    separators, no NaN. Payloads that cannot serialize canonically (NaN, inf,
    non-JSON types) are refused — an event that cannot be hashed cannot have an
    identity, so it can never be committed.
    """
    if not isinstance(payload, dict):
        raise LedgerError("An event payload must be a JSON object.")
    try:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise LedgerError("The event payload is not canonically JSON-serializable.") from error


def quantize_timestamp(value) -> str:
    """Quantize a source timestamp to a fixed-width microsecond string.

    Fixed width and zero padding make the stored string sort in true time
    order, so ``order_by("timestamp")`` on the char column is chronological —
    no float column, no float noise.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LedgerError("An event timestamp must be a finite nonnegative number of seconds.")
    if not math.isfinite(value) or value < 0:
        raise LedgerError("An event timestamp must be a finite nonnegative number of seconds.")
    microseconds = int(round(float(value) * _TIMESTAMP_QUANTUM))
    if microseconds < 0:  # rounding -0.4 us must not produce "-0"
        microseconds = 0
    return f"{microseconds:020d}"


def unquantize_timestamp(stored: str) -> float:
    """The numeric seconds a stored timestamp string encodes."""
    return int(stored) / _TIMESTAMP_QUANTUM


@dataclass(frozen=True)
class EventDraft:
    """One event to commit: everything the ledger needs, nothing about storage.

    ``timestamp`` is source time in seconds (T20's PTS-ordered timeline for
    recorded sources); ``session`` is the T20 session number, or 0 for events
    recorded against a whole recording rather than one session.
    """

    kind: str
    source: str
    session: int
    timestamp: float
    payload: dict

    def __post_init__(self):
        if self.kind not in (OperationEvent.KIND_REFERENCE, OperationEvent.KIND_DETECTOR):
            raise LedgerError("An event must name a supported kind.")
        if not isinstance(self.source, str) or not self.source:
            raise LedgerError("An event must name its source stream.")
        if type(self.session) is not int or self.session < 0:
            raise LedgerError("An event session must be a nonnegative integer.")


def event_identity(execution_id, draft: EventDraft) -> tuple[str, str]:
    """The (identity, payload) digests for one draft under one execution.

    Returns the identity SHA-256 over the execution id, source, kind, session,
    timestamp and payload canonical JSON — plus the payload's own digest, kept
    on the row so a slot conflict can be reported without recomputation.
    """
    if not isinstance(execution_id, str) or not execution_id:
        raise LedgerError("An event identity needs the execution id it is scoped to.")
    timestamp = quantize_timestamp(draft.timestamp)
    canonical_payload(draft.payload)  # refuse unhashable payloads before hashing
    identity_input = json.dumps(
        {
            "schema_version": 1,
            "kind": "operation_event_identity",
            "execution_id": execution_id,
            "source": draft.source,
            "event_kind": draft.kind,
            "session": draft.session,
            "timestamp": timestamp,
            "payload": draft.payload,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    payload_digest = hashlib.sha256(
        canonical_payload(draft.payload).encode()
    ).hexdigest()
    identity = hashlib.sha256(identity_input.encode()).hexdigest()
    return identity, payload_digest


def _pinned_versions(execution: OperationExecution) -> dict:
    """The pinned-version columns every event row copies from its execution.

    Same provenance the outcome records via ``executions._pins``: the bundle
    digest and recording digest are frozen on the execution row itself, the
    geometry digest comes from the bundle pins the execution froze at enqueue.
    """
    pins = execution.pins or {}
    geometry = pins.get("geometry") or {}
    geometry_sha256 = geometry.get("sha256") if isinstance(geometry, dict) else None
    return {
        "bundle_sha256": execution.bundle_sha256,
        "recording_sha256": execution.recording_sha256,
        "geometry_sha256": geometry_sha256 if isinstance(geometry_sha256, str) else "",
    }


@transaction.atomic
def commit_events(execution: OperationExecution, drafts) -> list[OperationEvent]:
    """Commit event drafts to the ledger, idempotently. Never silently merges.

    Each draft commits exactly once per execution: an identity that is already
    committed is returned as-is (a retry or replay after a crash commits
    nothing new), and a *different* payload under the same slot (same execution,
    source, kind, session, timestamp) raises ``ConflictingEventError`` — the
    database's slot uniqueness is the backstop, so even two racing committers
    cannot both land. The whole call is one transaction: all events commit or
    none do, so a crash mid-commit leaves no partial batch for a retry to
    duplicate.
    """
    if not isinstance(drafts, (list, tuple)):
        raise LedgerError("Commit a list of event drafts.")
    committed: list[OperationEvent] = []
    drafts = list(drafts)
    # Validate and hash everything first: a malformed draft anywhere refuses the
    # whole batch before any row is written.
    prepared = [(draft, *event_identity(str(execution.pk), draft)) for draft in drafts]
    pins = _pinned_versions(execution)
    for draft, identity, payload_digest in prepared:
        existing = OperationEvent.objects.filter(execution_id=execution.pk, identity_sha256=identity).first()
        if existing is not None:
            committed.append(existing)  # exact replay: idempotent, nothing new
            continue
        row = OperationEvent(
            execution_id=execution.pk,
            identity_sha256=identity,
            kind=draft.kind,
            source=draft.source,
            session=draft.session,
            timestamp=quantize_timestamp(draft.timestamp),
            payload=draft.payload,
            payload_sha256=payload_digest,
            **pins,
        )
        try:
            # A savepoint, so an IntegrityError leaves the outer transaction
            # usable for the re-fetch below (and the rest of the batch).
            with transaction.atomic():
                row.save()
        except IntegrityError as error:
            racer = OperationEvent.objects.filter(
                execution_id=execution.pk, identity_sha256=identity
            ).first()
            if racer is not None:
                # A racing committer landed the SAME identity first — and the
                # identity covers the payload, so this is a replay, not a merge.
                committed.append(racer)
                continue
            # The unique slot (execution, source, kind, session, timestamp) is
            # taken by a DIFFERENT identity: a conflicting payload, never a
            # silent merge.
            raise ConflictingEventError(
                "An event with this execution, source, kind, session and timestamp is already committed "
                "with different content. The ledger never merges conflicting events."
            ) from error
        committed.append(row)
    return committed


# -- aggregates: raw events rebuild them --------------------------------------------------------
@dataclass(frozen=True)
class EventAggregates:
    """Counts derived from raw events alone — nothing here is stored apart."""

    event_count: int
    counts_by_kind: dict
    counts_by_session: dict
    first_timestamp: str | None
    last_timestamp: str | None
    identity_sha256s: tuple

    def summary(self) -> dict:
        """A plain JSON-serializable summary, like the timeline's."""
        return {
            "event_count": self.event_count,
            "counts_by_kind": self.counts_by_kind,
            "counts_by_session": self.counts_by_session,
            "first_timestamp": self.first_timestamp,
            "last_timestamp": self.last_timestamp,
        }


def rebuild_aggregates(events) -> EventAggregates:
    """Rebuild aggregates from raw event rows alone. Pure: no database access.

    ``events`` is any iterable of ``OperationEvent`` rows (or equal shapes).
    This is the ledger's source-of-truth property made executable: whatever
    aggregate anyone records, a rebuild from the raw events must equal it —
    the tests pin that equality, and T27's period/shift aggregates must derive
    from these rows the same way.
    """
    counts_by_kind: dict = {}
    counts_by_session: dict = {}
    timestamps: list[str] = []
    identities: list[str] = []
    for event in events:
        counts_by_kind[event.kind] = counts_by_kind.get(event.kind, 0) + 1
        session = str(event.session)
        counts_by_session[session] = counts_by_session.get(session, 0) + 1
        timestamps.append(event.timestamp)
        identities.append(event.identity_sha256)
    ordered = sorted(timestamps)
    return EventAggregates(
        event_count=len(timestamps),
        counts_by_kind=counts_by_kind,
        counts_by_session=counts_by_session,
        first_timestamp=ordered[0] if ordered else None,
        last_timestamp=ordered[-1] if ordered else None,
        identity_sha256s=tuple(sorted(set(identities))),
    )
