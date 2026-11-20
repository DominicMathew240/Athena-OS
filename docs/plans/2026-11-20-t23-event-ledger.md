# T23 — Durable event ledger and duplicate prevention (design record)

Date: 20 November 2026. Status: implemented and locally verified by tests (no real-deployment claim).
Task: T23 in [the vision workflows task list](2026-11-15-vision-workflows-task-list.md); depends on
T05 (append-only release bundles), T19 (durable workers), T20 (source time), T21 (inference
adapters) and T22 (tracking). T25 (crossing episodes) and T27 (period/shift aggregates) consume
what this builds.

## The problem

T19 made operation executions durable — but their events lived only inside the outcome JSON blob
on the `OperationExecution` row. That has three honest defects:

1. **A retry can double-commit.** Nothing structural prevents the same event being written twice
   if a worker replays a step after a crash; the outcome's once-only `UPDATE` guard protects the
   *outcome*, not per-event facts.
2. **Events are not queryable by pinned versions.** "Show me every event computed under geometry
   version X / recording digest Y" requires parsing JSON blobs, not a database query.
3. **Aggregates are recorded, not derivable.** A count stored in an outcome cannot be re-derived
   from raw events, so it cannot be audited, rebuilt, or reconciled (T27's requirement).

## What was built

* **`OperationEvent`** (`projects.models`, migration `0031_operationevent`): one immutable,
  append-only row per committed event (`_AppendOnlyModel`, like the audit events — no save of an
  existing row, no update, no delete, no upsert). Columns: the execution (FK, `PROTECT`),
  `identity_sha256`, `kind` (`reference_crossing` today, `detector_crossing` reserved for T25),
  `source` (the stream name, e.g. `recording:<id>`), `session` (T20 session number; 0 = the whole
  recording, which is what human reference events are recorded against), `timestamp` (source time,
  see below), `payload` + `payload_sha256`, and the pinned versions copied from the execution:
  `bundle_sha256`, `recording_sha256`, `geometry_sha256`. All plain columns, all queryable.
* **The ledger** (`projects/operations/ledger.py`): the single commit path.
* **Worker wiring** (`projects/operations/executions.py`, additive): the execution step now
  commits the recorded human reference events through the ledger before running the comparison,
  and the outcome carries an `event_ledger` aggregate block. Nothing else in T19's lease/claim/
  recovery layer changed; the terminal-outcome guard is untouched.

## The contracts

**Event identity is content-addressed.** `identity_sha256` = SHA-256 over the canonical JSON of
`{execution_id, source, kind, session, timestamp, payload}` (sorted keys, tight separators, no
NaN — the same canonical-JSON rules as the release-bundle and timeline digests). Timestamps are
quantized to microseconds and stored as fixed-width zero-padded strings, so identities never
carry float noise and the char column sorts in true time order. Same event computed twice → same
identity. Different payload → different identity.

**Commit is idempotent; conflicts are loud.** `ledger.commit_events(execution, drafts)`:

* an identity already committed → the existing row is returned, nothing written (a retry or
  replay after a crash commits nothing new);
* a *different* payload under the same slot (same execution, source, kind, session, timestamp) →
  `ConflictingEventError` — the database's unique constraint on that slot is the backstop, so
  even two racing committers cannot both land, and the ledger never silently merges;
* the whole call is one transaction: all events commit or none do, so a crash mid-batch leaves no
  partial batch for a retry to duplicate;
* drafts are validated and hashed before anything is written: a malformed draft anywhere refuses
  the whole batch (bad kind, empty source, negative session, non-finite or negative timestamp,
  payload that is not canonically JSON-serializable).

**Pinned versions are queryable.** Every row copies `bundle_sha256` / `recording_sha256` /
`geometry_sha256` from the execution (which froze them at enqueue, T19). Later bundle or geometry
versions change nothing about committed rows — `OperationEvent.objects.filter(recording_sha256=...,
kind=...)` is a plain indexed query.

**Raw events rebuild aggregates.** `ledger.rebuild_aggregates(events)` is a pure function over
raw rows alone (event count, counts by kind, counts by session, first/last timestamp, identity
set). The worker's recorded `event_ledger` block *is* the output of that function over the
committed rows, and the tests pin that a rebuild from re-queried raw rows equals the recorded
aggregate. Nothing aggregate-shaped is stored apart from the raw events.

## What survives what

| Event | Survives? | Why |
|---|---|---|
| Worker crash mid-batch | Yes, without partial state | the commit is one transaction; nothing or everything landed |
| Retry / replay of the same execution's events | Yes, without duplicates | identical content → identical identity → the existing row is returned |
| Conflicting content under an event slot | Refused loudly | unique slot constraint + `ConflictingEventError`; never a silent merge |
| Bundle/geometry versions move on | Yes | every row carries the pinned digests it was computed under |
| Editing or deleting a committed event | Refused by the model | `_AppendOnlyModel` guards save/update/delete/upsert |
| Deleting an execution that has events | Refused by `PROTECT` | the ledger outlives the job record, like bundles/drafts |

## Honest limits

* **In-process SQLite tests only.** All duplicate-prevention evidence is single-process test
  cases plus the database constraints themselves; no multi-process broker, no real
  crash-and-recover run — the same honest limit as T19 (that evidence belongs to T56). The racing
  committer case is handled in code (IntegrityError → re-fetch → replay or conflict) but is not
  exercised by a true concurrent test.
* **Identity is scoped to one execution.** The same real-world crossing produced by two different
  executions is two ledger rows, by design: the execution — not the world — is the idempotency
  scope. Cross-execution event identity (world-level dedup) is not attempted and would need
  source-time + geometry identity work.
* **No REST surface.** Queryability is by Django ORM and tests, per the task's instruction;
  T19 added no routes and neither does this. If an operator-facing ledger view comes, it belongs
  with T27's aggregate UI work.
* **Detector events are not written yet.** Only reference events flow through the ledger today;
  T25's crossing episodes will commit `detector_crossing` events through the same path (the kind
  is reserved and validated).
* **Aggregates are counts, not period/shift totals.** Time-zone-aware bucketing (site time zones,
  midnight, DST) is T27; `rebuild_aggregates` is deliberately naive about wall-clock periods.

## Remaining work (for T25/T27)

T25 consumes the ledger for detector-driven crossing episodes: the `CrossingCounter`'s emitted
crossings become `detector_crossing` drafts committed through `commit_events` with the session
number from T20's timeline — the idempotency already proven here is what makes episode retries
safe. T27 derives period/shift aggregates from the raw rows (the rebuild function is the seam)
and reconciles them against the execution outcomes' `event_ledger` blocks.
