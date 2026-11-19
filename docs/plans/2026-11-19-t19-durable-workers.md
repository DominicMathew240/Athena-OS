# T19 — Supervised durable workers for operation execution (design record)

Date: 19 November 2026. Status: implemented and locally verified by tests (no real-deployment claim).
Task: T19 in [the vision workflows task list](2026-11-15-vision-workflows-task-list.md); the same
idea is formalized as OS-CORE-004 in [the multimodal platform roadmap](2026-11-17-multimodal-platform-roadmap.md).

## What existed before (survey)

Athena already had three durable, worker-side queue consumers, none of them in the web process:

| Queue | Worker command | Claim | Recovery |
|---|---|---|---|
| `TrainingRun` | `manage.py train_worker` | status flip `queued` → `running` inside one transaction | `fail_stale_runs` (heartbeat older than `STALE_HEARTBEAT_SECONDS`), `fail_orphaned_runs` (anything still `running` at start-up), cancel flag, run timeout |
| `ObservationRecord` | `manage.py inference_worker` | same flip pattern | `fail_stale_observations`, `fail_orphaned_observations` |
| `AutoLabelJob` | `manage.py autolabel_worker` | same flip pattern | `fail_stale_jobs` |

Their contract is "one worker per deployment, DB is SQLite": recovery assumes the single worker,
so **a second worker would both race on claims and wrongly fail the other's in-flight work at
start-up**. DEPLOYMENT.md and the docker-compose file enforce the single instance by convention.

Operations had **no execution path at all**: T05 stopped at append-only release bundles that are
deliberately not executed, and T09's source recordings are durable imports that decode nothing.
The only in-web-process background execution anywhere in the console is photo inspections
(`vision/service.py` daemon thread) — that predates operations and is unchanged here.

So the honest delta for T19 is:

1. **A first-class lease** (`lease_owner` + `lease_expires_at` on a new `OperationExecution` row,
   the durable job record for operation executions) instead of an implicit status flip. A lease
   identifies *which* worker holds the job and *until when*, so any number of operation workers
   can run against one database safely, and a crashed worker's job is reclaimable by evidence
   (expiry), not by start-up convention.
2. **A shared, atomic claim primitive** (`projects/operations/leases.py`) that all operation
   workers use: one `UPDATE ... WHERE lease is absent/expired` inside a transaction wins; every
   other claimer loses. Ported from the proven `claim_next_run` pattern.
3. **Lease-aware recovery** replacing the single-worker assumption for operations: on each pass a
   worker reclaims executions whose lease expired *only if it can prove the owner is gone* (the
   owner's heartbeat died), and a completed execution can never be re-executed — the terminal
   `UPDATE ... WHERE status='running'` guard (the same one that survived a SIGKILL test during
   training work) is kept verbatim.
4. **A supervisor** (`manage.py operation_supervisor`) that keeps N operation workers alive as
   child processes, in-process (no new deployment dependency) — consistent with the existing
   `manage.py`-command worker story; `docker-compose.yml` gains one `operation_worker` service
   driven by `restart: unless-stopped` plus the supervisor for N > 1.

What this does **not** add: real video decode/inference (T20/T21), live sources (T10), an event
ledger (T23), or any change to the training/inference/autolabel workers themselves.

## The lease/heartbeat contract

* **Job record.** `OperationExecution` is one request to execute one release bundle against one
  source recording (`projects/operations/executions.py`). Statuses mirror `TrainingRun`:
  `queued`, `running`, `succeeded`, `failed`, `cancelled`, plus a `cancel_requested` flag that
  only ever moves `running` → `cancelled` at a worker checkpoint. Provenance columns (bundle
  digest, recording digest, pins copied at enqueue) make the row self-contained: later bundle or
  recording edits cannot change what a queued execution will reference.
* **Enqueue.** `POST /api/operation-release-bundles/<id>/executions` (owner-scoped, Labeler
  role) freezes the bundle's snapshot digest, pins and recording digest onto a new `queued` row
  and returns 202. The web process never executes anything; it only queues. Existing draft,
  bundle and recording routes are unchanged.
* **Claim.** `claim_next_execution(owner_key, lease_seconds)` picks the oldest `queued` row and
  atomically sets `status='running'`, `lease_owner=owner_key`, `lease_expires_at=now+lease`,
  `started_at`, `heartbeat_at`. The `UPDATE` is guarded by `status='queued'` inside a
  transaction, so two claimers cannot both win.
* **Heartbeat.** While executing, a worker thread refreshes `heartbeat_at` (and extends the
  lease) every 10 seconds, exactly like the training worker's heartbeat thread; the same key is
  also published to the cache so readiness-style status can show the worker online.
* **Finish.** The outcome is written with `UPDATE ... WHERE status='running'` (and, for
  retries/cancels, `WHERE lease_owner=me`). If the guard matches zero rows the worker **writes
  nothing** — the job was already recovered — so no duplicate outcome, no duplicated events, no
  double-write after a crash between work and commit. This is the training SIGKILL guarantee,
  ported.
* **Recovery.** `fail_abandoned_executions()` is run at worker start and on every queue pass:
  a `running` execution whose lease has expired **and** whose owning worker's heartbeat is gone
  (lease expired by more than a full lease interval, i.e. the owner missed two refresh windows)
  is failed with a plain-language message and can be re-enqueued safely. Leases that are merely
  close to expiry, or whose owner is still beating, are never touched. This removes the
  single-worker assumption: worker A's start-up does not fail worker B's in-flight job.
* **Cancellation.** `POST /api/operation-executions/<id>/cancel` sets `cancel_requested` (owner,
  Labeler). The worker checks the flag at its checkpoints, finishes with `cancelled`, and the
  claim guard means a cancel racing a finish is whichever commits first — never both.

## What survives what

| Event | Survives? | Why |
|---|---|---|
| HTTP request exits after enqueue | Yes | the row is committed; the web process holds no execution state |
| gunicorn restart / worker recycle | Yes | execution happens only in worker processes |
| Worker crash (SIGKILL) mid-run | Yes, without duplicates | work-in-progress is lost, the lease expires, recovery fails the row once (guarded `UPDATE`), and a fresh execution can be enqueued; a worker that was SIGKILLed *after* committing the outcome writes nothing further because its finish guard no longer matches |
| DB restart | Yes | every state transition is a committed row; nothing durable lives in memory or cache (the cache heartbeat is presentational only) |
| Two workers, one queue | Yes | atomic lease claim; only one row flips to `running` |
| Lease expiry alone (owner alive but slow) | No false failure | recovery also requires the owner's refresh window to have passed twice |

Verified locally by `projects.tests_t19_durable` (claim races, expiry, crash simulation,
no-duplicate, cancel-under-lease, API surface). No real multi-process deployment was exercised;
that evidence belongs to T56 (job failure and cancellation validation).

## Supervision

`manage.py operation_supervisor --workers N` (default 1) spawns N `operation_worker` child
processes and restarts any child that exits unexpectedly; SIGTERM/SIGINT to the supervisor stop
the children and wait for them. N=1 is the default and the single-worker deployment story stays
valid; N>1 is safe because of the lease claim. Under docker-compose, one `operation_worker`
service with `restart: unless-stopped` is the documented configuration, matching the existing
workers; the supervisor is for non-Docker hosts that want more than one worker without a process
manager of their own (systemd/Supervisor config is documented in DEPLOYMENT.md as equivalent).

## Remaining work (for T10/T20/T21 to build on)

The worker's execution step itself is deliberately minimal: it verifies the pinned recording is
still byte-identical, runs the operation domain's pure comparison/crossing logic against the
recorded human reference events, and stores the plain-language result on the row. T10 (live
camera) can put a real capture loop where the reference replay now stands, T20 (source time
contracts) can define the ordered event stream the worker consumes, and T21 (inference adapters)
can replace the reference-events stand-in with real detector output — all without touching the
lease, claim, recovery or supervision layer built here. T23's event ledger will hang off the same
"finish writes exactly once" guard.
