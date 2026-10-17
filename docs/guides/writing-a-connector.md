# Writing a connector

A **connector** moves captured data (today: images) from some source — a folder a scanner saves into, an
HTTP push, a future vendor protocol — to the Athena console's check screen, with retries, backpressure,
duplicate protection and a provenance trail. The contract lives in `athena_sdk.connectors` and is
**vendor-neutral on purpose**.

> **Status — read this first.** No real scanner, camera, hardware or vendor protocol has been selected, and
> none has been tested with this code. What exists is the *contract*, two reference connectors (folder watch,
> HTTP push), a console sink, and tests that use fakes plus a stub console and one disposable local console
> with a toy model on synthetic images. Nothing here is validated for TLS, Windows, high throughput, long
> uptime or production use. Selecting real hardware/protocol remains an open roadmap gate (M2/M5).

## The two rules

1. **Provenance travels with the bytes.** Every item carries its SHA-256, a `source_id` and an idempotency
   key; the pipeline stamps the connector name and version before anything is submitted, and the local
   ledger records them with every attempt and receipt.
2. **A connector never decides outcomes.** An item has no verdict, label or decision field, and metadata keys
   such as `decision`, `label`, `verdict`, `status` are refused. Whether an image is acceptable, flagged or
   confirmed is decided by a person in the console. The console sink has no method to post feedback or
   decisions, and a test keeps it that way.

## Shape

```text
 DataSourceConnector ──poll()/stream()──▶ Pipeline ──submit()──▶ Sink (AthenaConsoleSink)
   ack / nack ◀──────────────────────────  │  bounded queue (block | drop_oldest)
                                           │  retry: exponential backoff + jitter
                                           └─ Ledger (sqlite): idempotency + provenance
```

| Piece | Contract |
| --- | --- |
| `SourceItem` | `payload: bytes`, `content_type` (jpeg/png/webp), `source_id`, `captured_at` (tz-aware), `idempotency_key`, `metadata: dict[str,str]`; `sha256` is computed, never supplied. Frozen. |
| `DataSourceConnector` | `name`, `version`, `open()`, `close()`, `health() -> Health(status ok/degraded/down, detail, last_success_at)`, `poll(max_items) -> list[SourceItem]`, `stream(stop, interval)` (default loops `poll`), `ack(item, Outcome)`, `nack(item, reason, permanent=)`. |
| `Sink` | `submit(item) -> Receipt`; optional `find_existing(item)`, `health()`, `close()`. Raises `TransientSinkError` (retry), `SinkUnavailableError` (the sink is unusable but the item is fine), `PermanentItemError` (this item is refused for good). |
| `Pipeline` | Wires a connector, a sink and a `Ledger`; owns two threads, the queue, retries, health and graceful shutdown. |
| `Receipt` | `sink`, `remote_id` (the console's observation id), `status`, `detail` (model version id, observation status, …). Proof of delivery — not of meaning. |

`ack` means "the sink has custody (or already had it): release the item at the source" (the folder connector
moves the file to `processed/`). `nack(permanent=True)` means "never deliverable: quarantine it" (`rejected/`
with a reason file). `nack(permanent=False)` means "keep it and offer it again later".

### Guarantees and their limits

* **Never submits the same idempotency key twice** — as long as the ledger file survives. The ledger row is
  written before the first attempt and updated after, in sqlite with `synchronous=FULL`.
* **Crash recovery.** A row left in `submitting` means the process died during delivery. On restart the
  pipeline asks the sink (`find_existing`) before sending again. The console sink recognises its own earlier
  upload by SHA-256 plus a key-derived filename prefix among the app's **100 most recent observations**
  (the console's list limit). If the upload is older than that, or the lookup keeps failing, the item is
  either held (lookup failing — it is never blindly resent) or resent (not found). Delivery is therefore
  *at-least-once with duplicate suppression*, not exactly-once.
* **The console does not store connector provenance.** It records the image SHA-256, the filename (which the
  sink sets to `<16 hex of key hash>__<name>.<ext>`) and the service account. Connector name/version,
  `source_id` and the idempotency key are kept in the **local ledger**, and sent as `X-Athena-*` request
  headers that the console ignores today. Keep the ledger with your records.
* **Backpressure.** `block` (default) stops polling the source while the queue is full, so files wait on disk.
  `drop_oldest` evicts the oldest queued item and hands it back to its source with `nack` (it is not lost
  while the source can re-offer it; the drop is counted in `queue.dropped`). The HTTP receiver answers
  `503` + `Retry-After` when its spool is full.
* **Retries.** `RetryPolicy(max_attempts, base_delay, max_delay, multiplier, jitter)`; full jitter by
  default. `Retry-After` from the console is a floor. After `max_attempts` the item is **handed back, not
  rejected**. Only a sink that says `PermanentItemError` (HTTP 400/413/415 from the console) quarantines.
* **Observation timeouts do not re-post.** Once the console accepted an image (HTTP 202) the sink never
  uploads it again: if the poll window ends the receipt is `pending_timeout`; if the console's worker
  failed the receipt is `failed` (a person resubmits).
* **Graceful stop.** SIGTERM/SIGINT stop polling, deliver the queue for up to `drain_timeout`, hand the rest
  back, close connector and sink, and exit 0.

## The reference connectors

**`FolderDropConnector(root)`** — scans the top level of one directory for `.jpg/.jpeg/.png/.webp`.

* Waits until size and mtime are unchanged for `stable_seconds`, then checks magic bytes, extension and a
  completeness trailer (PNG `IEND`, JPEG `FFD9`, WebP RIFF length). The console still re-validates with a real
  decoder; its verdict is final.
* `processed/` after success; `rejected/<name>` + `<name>.reason.json` for wrong type, oversize, truncated,
  symlink, hard link, non-regular file, or a permanent refusal by the console.
* Safety: symlinks, hard links and FIFOs are never read; files are opened `O_NOFOLLOW|O_NONBLOCK` and
  re-checked with `fstat`; destinations are built only from plain base names; a file replaced after it was
  read is not moved on `ack`.
* `captured_at` is the file's modification time, which is not necessarily when the scan happened.

**`HttpPushConnector(spool_dir, secret)`** — a stdlib `http.server` receiver, loopback by default.

```bash
curl -X POST http://127.0.0.1:8820/v1/items \
  -H "X-Athena-Connector-Secret: $ATHENA_CONNECTOR_PUSH_SECRET" \
  -H "Content-Type: image/png" -H "X-Source-Id: lane-2" -H "Idempotency-Key: scan-0001" \
  --data-binary @scan.png
```

`401` wrong secret · `415` not an accepted type (or bytes do not match the header) · `413` too large (decided
from `Content-Length`, body never read) · `411` no length · `422` looks truncated · `409` key reused with
different content · `503` spool full · `202` accepted. A `202` is sent only after the item is fsynced into the
spool directory, so a crash after the reply loses nothing. **It speaks plain HTTP**; production use needs a
TLS-terminating reverse proxy in front of the loopback port (not built, not tested). Binding elsewhere needs
`allow_non_loopback=True`; the secret must be at least 16 characters.

## Delivering to the console: `AthenaConsoleSink`

Standard library only (`urllib` + `http.cookiejar`). It signs in through the real login form (reads the CSRF
token from the page), `POST`s one multipart `image` to `/api/projects/<project>/inference/apps/<app>/run`, and
polls `.../observations/<id>` until `succeeded`/`failed`. The receipt carries the observation id, the model
version id/number and artifact SHA-256 the console pinned the observation to, and whether the console's stored
image hash matches what was sent (`sha256_match`).

**Service account and role.** Credentials come from the environment only —
`ATHENA_CONNECTOR_USER` and `ATHENA_CONNECTOR_PASSWORD` — never from the config file or the command line.
Running a check screen is a `label` action (`POST .../run`), so the **minimum role is Labeler**; a Viewer gets
`403` and the sink reports itself `down` rather than rejecting images. Create the account on the console host:

```bash
python manage.py shell -c "from django.contrib.auth import get_user_model as g; g().objects.create_user('connector-svc')"
python manage.py changepassword connector-svc
python manage.py athena_roles --assign connector-svc labeler
```

Be aware: the console cannot give a service account *only* "run a check screen". A Labeler can also record
feedback and review decisions in the console. The connector never calls those endpoints, but anyone holding the
password could — treat it like any write credential, keep it out of the config file, rotate it, and consider
`ATHENA_UNASSIGNED_ROLE=viewer` so a mistakenly created account is not a Labeler by default. A console-side
"run-only" role would be a separate change.

Safety rules in the sink: it refuses plain `http://` to non-loopback hosts unless `allow_insecure_http`, stops
retrying sign-in for `auth_cooldown` after a refusal (the console locks accounts after repeated failures), and
re-signs in once if the session expires.

## Running it

```bash
export ATHENA_CONNECTOR_USER=connector-svc ATHENA_CONNECTOR_PASSWORD=...   # from your secret store
PYTHONPATH=sdk/python python -m athena_sdk.connectors.run --config connector.json --validate-only
PYTHONPATH=sdk/python python -m athena_sdk.connectors.run --config connector.json
```

Examples: `examples/connectors/connector.folder.example.json`, `connector.http_push.example.json`. Unknown keys,
credentials in the file, and bad values are refused with a plain message (exit 2). Output is one JSON object
per line: `started`, `submitted` (with provenance and receipt), `retry`, `rejected`, `deferred`,
`duplicate_skipped`, `adopted_existing`, `dropped`, `stop_requested`, `stopped`, and a periodic `health` line:

```json
{"event":"health","status":"ok","last_success_at":"2026-10-17T01:10:39+00:00","consecutive_failures":0,
 "connector":{"name":"folder-drop","version":"0.1.0","status":"ok"},"sink":{"name":"athena-console","status":"ok"},
 "queue":{"policy":"block","maxsize":8,"depth":0,"enqueued":5,"dropped":0,"blocked_puts":0,"high_watermark":1},
 "ledger":{"received":0,"submitting":0,"submitted":5,"rejected":0},"counters":{"submitted":5,"retries":0}}
```

`pipeline.health_file` also writes the latest health document atomically. Overall status is `down` if the
connector, the sink or sign-in is unusable, `degraded` while failures are being retried, otherwise `ok`.

## Write your own

Implement `DataSourceConnector` using only the public package; `examples/connectors/synthetic_scanner.py` is a
complete short example that generates synthetic PNGs. The rules that matter:

* `poll` must never re-offer an item that is still in flight, and must not block for long.
* `idempotency_key` must be stable across restarts for the same capture and different for different captures
  (the folder connector uses `label:filename:sha256`).
* `ack`/`nack` may be called from another thread and a second `ack` must be harmless.
* Keep protocol-specific retries *inside* your connector; leave delivery retries to the pipeline.
* Never put an outcome in `metadata`.

Prove it with the conformance helper:

```python
from athena_sdk.connectors.conformance import assert_connector_conforms
assert_connector_conforms(MyConnector(...))   # name/version, poll bounds, no re-offer, ack/nack, stream stop
```

Then run it through `Pipeline(connector, sink, Ledger("ledger.sqlite")).start()` (programmatic use; the JSON
runner only knows the two reference connectors by design — no config-driven imports of arbitrary code).

## What has been verified, and what has not

Verified (`tests/test_connectors.py`, 82 tests): retry/backoff arithmetic, both backpressure policies and
counters, idempotency across a simulated crash (both "console had it" and "console did not"), ledger
provenance, folder safety (symlink escape, hard link, FIFO, partial, wrong type, oversize, path-traversal
names), HTTP receiver auth/type/size/backpressure/durability, the sink against a stub console that imitates
login+CSRF+run+poll, config validation, and the runner as a subprocess (JSON lines, SIGTERM exit 0).

One disposable end-to-end run (local console on `127.0.0.1:8816`, real `inference_worker`, a toy
random-initialised torchvision model trained for 2 epochs on 16 synthetic images and approved with the weak-results
acknowledgement — plumbing only, no quality claim): 5 synthetic PNGs dropped in a watched folder produced 5
observations pinned to the one approved model version, each with matching SHA-256 and a ledger receipt;
`SIGKILL` of the connector during the third delivery left that image stored by the console but unrecorded, the
restart recognised it (`adopted_existing`) and finished the rest with **no duplicate observations**; the model row's
stage and `artifact_sha256` were unchanged, and no feedback or decision rows were created.

**Not tested:** any real scanner, device or vendor protocol; TLS or a reverse proxy; Windows (the folder safety
code uses POSIX `O_NOFOLLOW`/`mkfifo` semantics); throughput beyond a handful of images (the pipeline is one
delivery worker, and the console's own worker is sequential); long-running soak; a console behind a proxy that
rewrites cookies or CSRF origins; multiple connectors sharing one ledger file; clock changes; images at the
20 MB limit; video, audio or non-image data.
