# SPDX-License-Identifier: Apache-2.0
"""The connector pipeline: source -> bounded queue -> sink, with retries and a durable ledger.

Flow for each item offered by a connector:

1. stamp connector name/version, write a ledger row (``received``);
2. already ``submitted`` under the same idempotency key -> ack as ``duplicate``, never resubmit;
3. queue it (policy ``block`` pushes back on the source, ``drop_oldest`` sheds the oldest
   queued item back to its source via ``nack``);
4. one worker delivers it to the sink with exponential backoff + jitter, recording each
   attempt in the ledger *before* sending so a crash leaves an in-doubt marker, not a gap;
5. on success record the receipt and ``ack``; on a permanent refusal ``nack(permanent=True)``.

The pipeline never decides what an item means and never posts feedback or decisions.
"""

from __future__ import annotations

import dataclasses
import logging
import random
import threading
from collections.abc import Callable
from datetime import datetime
from typing import Any

from athena_sdk.connectors.base import (
    DataSourceConnector,
    Health,
    Outcome,
    PermanentItemError,
    Receipt,
    Sink,
    SinkUnavailableError,
    SourceItem,
    TransientSinkError,
    utcnow,
)
from athena_sdk.connectors.bounded_queue import BoundedQueue
from athena_sdk.connectors.ledger import REJECTED, SUBMITTED, SUBMITTING, Ledger, LedgerConflict
from athena_sdk.connectors.retry import RetryPolicy

logger = logging.getLogger("athena_sdk.connectors")
EventCallback = Callable[[dict[str, Any]], None]


class Pipeline:
    def __init__(
        self,
        connector: DataSourceConnector,
        sink: Sink,
        ledger: Ledger,
        *,
        queue_size: int = 16,
        overflow: str = "block",
        retry: RetryPolicy | None = None,
        poll_interval: float = 1.0,
        drain_timeout: float = 20.0,
        on_event: EventCallback | None = None,
        rng: random.Random | None = None,
        sleep: Callable[[float], bool] | None = None,
    ) -> None:
        if not connector.name or not connector.version:
            raise ValueError("A connector must declare a non-empty name and version.")
        self.connector = connector
        self.sink = sink
        self.ledger = ledger
        self.queue: BoundedQueue[SourceItem] = BoundedQueue(queue_size, overflow)
        self.retry = retry or RetryPolicy()
        self.poll_interval = poll_interval
        self.drain_timeout = drain_timeout
        self._on_event = on_event
        self._rng = rng or random.Random()
        self._custom_sleep = sleep
        self._stop_producer = threading.Event()
        self._hard_stop = threading.Event()
        self._producer_done = threading.Event()
        self._lock = threading.Lock()
        self._idle = threading.Condition(self._lock)
        self._pending: set[str] = set()  # keys queued or being delivered
        self._threads: list[threading.Thread] = []
        self._started_at: datetime | None = None
        self._last_success_at: datetime | None = None
        self._last_error: str = ""
        self._consecutive_failures = 0
        self._sink_unavailable = False
        self.counters = dict.fromkeys(
            ("received", "submitted", "duplicates_skipped", "rejected", "deferred", "dropped", "retries",
             "adopted", "conflicts", "ack_errors", "connector_errors", "integrity_mismatches"), 0
        )

    # -- lifecycle -----------------------------------------------------------------------
    def start(self) -> None:
        if self._threads:
            raise RuntimeError("Pipeline already started.")
        self._started_at = utcnow()
        self.connector.open()
        self._event("started", connector=self.connector.name, version=self.connector.version)
        self._threads = [
            threading.Thread(target=self._produce, name="connector-producer", daemon=True),
            threading.Thread(target=self._consume, name="connector-consumer", daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    def stop(self, *, drain: bool = True, timeout: float | None = None) -> None:
        """Graceful shutdown: stop polling, deliver what is queued (up to the drain timeout),
        hand anything left back to its source, then close the connector and sink."""
        if not self._threads:
            return
        self._stop_producer.set()
        producer, consumer = self._threads
        producer.join(timeout=max(5.0, self.poll_interval * 3))
        self._producer_done.set()
        deadline = self.drain_timeout if timeout is None else timeout
        if not drain:
            deadline = 0.0
        consumer.join(timeout=deadline)
        self._hard_stop.set()
        self.queue.close()
        consumer.join(timeout=10.0)
        for item in self.queue.drain():
            self._release(item, "shutdown before delivery")
        self._threads = []
        self.connector.close()
        self.sink.close()
        self._event("stopped", **self.counters)

    def wait_until_idle(self, timeout: float = 10.0) -> bool:
        """Block until nothing is queued or being delivered (tests, one-shot runs)."""
        with self._idle:
            return self._idle.wait_for(lambda: not self._pending and len(self.queue) == 0, timeout)

    # -- producer ------------------------------------------------------------------------
    def _produce(self) -> None:
        while not self._stop_producer.is_set():
            try:
                for item in self.connector.stream(self._stop_producer, self.poll_interval):
                    self._ingest(item)
                    if self._stop_producer.is_set():
                        break
                return
            except Exception as error:  # noqa: BLE001 - a connector bug must not kill the pipeline
                self.counters["connector_errors"] += 1
                self._last_error = f"connector: {error}"
                logger.exception("connector %s failed while polling", self.connector.name)
                self._event("connector_error", error=str(error))
                self._stop_producer.wait(min(self.retry.max_delay, 2.0))

    def _ingest(self, raw: SourceItem) -> None:
        item = dataclasses.replace(raw, connector=self.connector.name, connector_version=self.connector.version)
        key = item.idempotency_key
        with self._lock:
            if key in self._pending:
                return  # the source re-offered something already in flight
        self.counters["received"] += 1
        try:
            entry = self.ledger.record_received(item)
        except LedgerConflict as error:
            self.counters["conflicts"] += 1
            self._event("conflict", key=key, error=str(error))
            self._safe(self.connector.nack, item, str(error), permanent=True)
            return
        if entry.status == SUBMITTED:
            self.counters["duplicates_skipped"] += 1
            self._event(
                "duplicate_skipped", key=key, sha256=item.sha256,
                remote_id=entry.receipt.remote_id if entry.receipt else None,
            )
            self._safe(self.connector.ack, item, Outcome.DUPLICATE)
            return
        if entry.status == REJECTED:
            self._safe(self.connector.nack, item, f"previously rejected: {entry.last_error}", permanent=True)
            return
        with self._lock:
            self._pending.add(key)
        while True:
            result = self.queue.put(item, timeout=0.2)
            if result.accepted:
                break
            if self._stop_producer.is_set():
                self._release(item, "shutdown before queueing")
                return
            if self.queue.policy != "block":
                self._release(item, "queue full (reject_new)")
                return
        if result.dropped is not None:
            self.counters["dropped"] += 1
            self.ledger.note_error(result.dropped.idempotency_key, "dropped by overflow policy; handed back to source")
            self._event("dropped", key=result.dropped.idempotency_key, policy="drop_oldest")
            self._release(result.dropped, "dropped by overflow policy")

    # -- consumer ------------------------------------------------------------------------
    def _consume(self) -> None:
        while not self._hard_stop.is_set():
            item = self.queue.get(timeout=0.2)
            if item is None:
                if self._producer_done.is_set() and len(self.queue) == 0:
                    return
                continue
            try:
                self._deliver(item)
            except Exception as error:  # noqa: BLE001 - never let the worker die with items queued
                logger.exception("unexpected delivery failure")
                self._last_error = f"pipeline: {error}"
                self._release(item, f"internal error: {error}")

    def _deliver(self, item: SourceItem) -> None:
        key = item.idempotency_key
        entry = self.ledger.get(key)
        in_doubt = bool(entry and entry.status == SUBMITTING)  # a previous process died mid-delivery
        attempt = 0
        while True:
            if self._hard_stop.is_set():
                self.ledger.attempt_failed(key, "interrupted by shutdown")
                self._release(item, "shutdown during delivery")
                return
            try:
                if in_doubt:
                    existing = self.sink.find_existing(item)
                    in_doubt = False
                    if existing is not None:
                        self.counters["adopted"] += 1
                        self._event("adopted_existing", key=key, remote_id=existing.remote_id)
                        self._finish(item, existing)
                        return
                self.ledger.begin_attempt(key)
                receipt = self.sink.submit(item)
            except PermanentItemError as error:
                self.ledger.mark_rejected(key, str(error))
                self.counters["rejected"] += 1
                self._event("rejected", key=key, reason=str(error))
                self._forget(key)
                self._safe(self.connector.nack, item, str(error), permanent=True)
                return
            except TransientSinkError as error:
                unavailable = isinstance(error, SinkUnavailableError)
                self.ledger.attempt_failed(key, str(error))
                self._note_failure(error, unavailable)
                attempt += 1
                if not unavailable and attempt >= self.retry.max_attempts:
                    self.counters["deferred"] += 1
                    self._event("deferred", key=key, attempts=attempt, error=str(error))
                    self._release(item, f"gave up after {attempt} attempts: {error}")
                    return
                self.counters["retries"] += 1
                delay = (
                    self.retry.max_delay if unavailable
                    else self.retry.delay(attempt, self._rng, floor=error.retry_after)
                )
                self._event("retry", key=key, attempt=attempt, delay=round(delay, 3), error=str(error))
                if self._sleep(delay):
                    self.ledger.attempt_failed(key, "interrupted by shutdown")
                    self._release(item, "shutdown while waiting to retry")
                    return
                continue
            except Exception as error:  # noqa: BLE001 - a sink bug is treated as transient
                self.ledger.attempt_failed(key, f"sink bug: {error}")
                self._note_failure(error, False)
                logger.exception("sink raised an unexpected error")
                attempt += 1
                if attempt >= self.retry.max_attempts:
                    self.counters["deferred"] += 1
                    self._release(item, f"gave up after {attempt} attempts: {error}")
                    return
                self.counters["retries"] += 1
                if self._sleep(self.retry.delay(attempt, self._rng)):
                    self._release(item, "shutdown while waiting to retry")
                    return
                continue
            self._finish(item, receipt)
            return

    def _finish(self, item: SourceItem, receipt: Receipt) -> None:
        key = item.idempotency_key
        for tries in range(3):
            try:
                self.ledger.mark_submitted(key, receipt)
                break
            except Exception:  # noqa: BLE001
                logger.exception("ledger write failed after delivery (try %d)", tries + 1)
        self.counters["submitted"] += 1
        if receipt.detail.get("sha256_match") is False:
            self.counters["integrity_mismatches"] += 1
            self._event("integrity_mismatch", key=key, sent=item.sha256, receipt=receipt.to_dict())
        with self._lock:
            self._last_success_at = utcnow()
            self._consecutive_failures = 0
            self._sink_unavailable = False
        self._event(
            "submitted", key=key, source_id=item.source_id, sha256=item.sha256,
            connector=item.connector, connector_version=item.connector_version, receipt=receipt.to_dict(),
        )
        self._forget(key)
        self._safe(self.connector.ack, item, Outcome.SUBMITTED)

    # -- helpers -------------------------------------------------------------------------
    def _release(self, item: SourceItem, reason: str) -> None:
        """Hand an undelivered item back to its source for a later offer."""
        self._forget(item.idempotency_key)
        self._safe(self.connector.nack, item, reason, permanent=False)

    def _forget(self, key: str) -> None:
        with self._idle:
            self._pending.discard(key)
            self._idle.notify_all()

    def _safe(self, fn: Callable[..., None], *args: Any, **kwargs: Any) -> None:
        try:
            fn(*args, **kwargs)
        except Exception as error:  # noqa: BLE001 - the ledger protects against a re-offer duplicating
            self.counters["ack_errors"] += 1
            self._last_error = f"{getattr(fn, '__name__', 'callback')}: {error}"
            logger.exception("connector callback failed")
            self._event("callback_error", callback=getattr(fn, "__name__", "?"), error=str(error))

    def _note_failure(self, error: Exception, unavailable: bool) -> None:
        with self._lock:
            self._consecutive_failures += 1
            self._last_error = str(error)
            if unavailable:
                self._sink_unavailable = True

    def _sleep(self, seconds: float) -> bool:
        """Wait ``seconds``; return True if a hard stop interrupted the wait."""
        if self._custom_sleep is not None:
            return self._custom_sleep(seconds) or self._hard_stop.is_set()
        return self._hard_stop.wait(seconds)

    def _event(self, name: str, **fields: Any) -> None:
        if self._on_event is None:
            return
        try:
            self._on_event({"event": name, "ts": utcnow().isoformat(), **fields})
        except Exception:  # noqa: BLE001
            logger.exception("event callback failed")

    # -- observability -------------------------------------------------------------------
    def health(self) -> dict[str, Any]:
        try:
            source = self.connector.health()
        except Exception as error:  # noqa: BLE001
            source = Health("down", f"health check raised: {error}")
        try:
            target = self.sink.health()
        except Exception as error:  # noqa: BLE001
            target = Health("down", f"health check raised: {error}")
        with self._lock:
            failures, unavailable = self._consecutive_failures, self._sink_unavailable
            last_ok, last_error = self._last_success_at, self._last_error
        if source.status == "down" or unavailable or target.status == "down":
            status = "down"
        elif source.status == "degraded" or target.status == "degraded" or failures:
            status = "degraded"
        else:
            status = "ok"
        return {
            "status": status,
            "detail": last_error if status != "ok" else "",
            "last_success_at": last_ok.isoformat() if last_ok else None,
            "consecutive_failures": failures,
            "connector": {"name": self.connector.name, "version": self.connector.version, **source.to_dict()},
            "sink": {"name": self.sink.name, **target.to_dict()},
            "queue": self.queue.stats(),
            "ledger": self.ledger.counts(),
            "counters": dict(self.counters),
            "started_at": self._started_at.isoformat() if self._started_at else None,
        }
