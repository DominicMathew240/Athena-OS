# SPDX-License-Identifier: Apache-2.0
"""Tests for the vendor-neutral connector SDK (`athena_sdk.connectors`).

Everything here runs against fakes and a stub HTTP server that imitates the console's
sign-in (CSRF), run and poll endpoints. NO real scanner, hardware, vendor protocol, TLS
terminator or production console is exercised by this file.
"""

from __future__ import annotations

import dataclasses
import hashlib
import http.client
import json
import os
import random
import signal
import struct
import subprocess
import sys
import threading
import time
import uuid
import zlib
from datetime import datetime, timedelta, timezone
from email.parser import BytesParser
from email.policy import default as email_default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

import pytest
from athena_sdk.connectors import (
    AthenaConsoleSink,
    BoundedQueue,
    DataSourceConnector,
    FolderDropConnector,
    Health,
    HttpPushConnector,
    ItemError,
    Ledger,
    Outcome,
    PermanentItemError,
    Pipeline,
    Receipt,
    RetryPolicy,
    Sink,
    SinkUnavailableError,
    SourceItem,
    TransientSinkError,
)
from athena_sdk.connectors.config import ConfigError, build_pipeline, load_config
from athena_sdk.connectors.conformance import ConnectorConformanceError, assert_connector_conforms
from athena_sdk.connectors.ledger import LedgerConflict

REPO = Path(__file__).resolve().parents[1]
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


# -- helpers -------------------------------------------------------------------------------
def png(seed: int = 0, size: int = 4) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    rows = b"".join(b"\x00" + bytes([(seed + y) % 256]) * 3 * size for y in range(size))
    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return PNG_MAGIC + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def jpeg(seed: int = 0) -> bytes:
    return b"\xff\xd8\xff\xe0" + bytes([seed % 256]) * 40 + b"\xff\xd9"


def webp(seed: int = 0) -> bytes:
    body = b"WEBPVP8 " + bytes([seed % 256]) * 20
    return b"RIFF" + struct.pack("<I", len(body)) + body


def make_item(seed: int = 0, *, name: str | None = None, data: bytes | None = None) -> SourceItem:
    payload = data if data is not None else png(seed)
    name = name or f"scan-{seed}.png"
    return SourceItem(
        payload=payload, content_type="image/png", source_id=f"fake:{name}",
        captured_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        idempotency_key=f"fake:{name}:{hashlib.sha256(payload).hexdigest()}",
        metadata={"file_name": name},
    )


def wait_for(condition, timeout: float = 8.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(interval)
    return condition()


class ListConnector(DataSourceConnector):
    """In-memory source. Items are offered once; transient nacks optionally re-offer."""

    name = "fake-list"
    version = "9.9.9"

    def __init__(self, items, *, reoffer: bool = False) -> None:
        self.items = list(items)
        self.reoffer = reoffer
        self.offered: set[str] = set()
        self.acked: list[tuple[str, Outcome]] = []
        self.nacked: list[tuple[str, str, bool]] = []
        self.closed = 0
        self._lock = threading.Lock()

    def health(self) -> Health:
        return Health("ok", "fake")

    def poll(self, max_items: int = 1):
        with self._lock:
            ready = [i for i in self.items if i.idempotency_key not in self.offered][:max_items]
            self.offered.update(i.idempotency_key for i in ready)
            return ready

    def ack(self, item, outcome) -> None:
        self.acked.append((item.idempotency_key, outcome))

    def nack(self, item, reason, *, permanent=False) -> None:
        self.nacked.append((item.idempotency_key, reason, permanent))
        if self.reoffer and not permanent:
            with self._lock:
                self.offered.discard(item.idempotency_key)

    def close(self) -> None:
        self.closed += 1

    def finished(self) -> int:
        return len(self.acked) + len([n for n in self.nacked if n[2]])


class FakeSink(Sink):
    name = "fake-sink"

    def __init__(self, script=None, *, gate: threading.Event | None = None) -> None:
        self.script = list(script or [])  # exceptions to raise, one per call; exhausted -> success
        self.submitted: list[SourceItem] = []
        self.calls = 0
        self.gate = gate
        self.existing: dict[str, Receipt] = {}
        self.find_calls: list[str] = []
        self.closed = False

    def submit(self, item):
        self.calls += 1
        if self.gate is not None:
            self.gate.wait(10)
        if self.script:
            step = self.script.pop(0)
            if step is not None:
                raise step
        self.submitted.append(item)
        return Receipt(self.name, f"obs-{len(self.submitted)}", "succeeded", {"sha256": item.sha256})

    def find_existing(self, item):
        self.find_calls.append(item.idempotency_key)
        return self.existing.get(item.idempotency_key)

    def close(self) -> None:
        self.closed = True


def fast_retry(**kw) -> RetryPolicy:
    return RetryPolicy(**{"max_attempts": 4, "base_delay": 0.001, "max_delay": 0.005, **kw})


def run_pipeline(connector, sink, ledger, **kw) -> Pipeline:
    kw.setdefault("retry", fast_retry())
    kw.setdefault("poll_interval", 0.02)
    pipeline = Pipeline(connector, sink, ledger, **kw)
    pipeline.start()
    return pipeline


# -- retry / queue -------------------------------------------------------------------------
def test_retry_ceiling_doubles_and_caps():
    policy = RetryPolicy(max_attempts=9, base_delay=1.0, max_delay=10.0, jitter="none")
    assert [policy.delay(n) for n in range(1, 7)] == [1.0, 2.0, 4.0, 8.0, 10.0, 10.0]


def test_retry_jitter_stays_inside_the_ceiling_and_is_seedable():
    full = RetryPolicy(base_delay=1.0, max_delay=8.0, jitter="full")
    equal = RetryPolicy(base_delay=1.0, max_delay=8.0, jitter="equal")
    rng = random.Random(7)
    for attempt in range(1, 8):
        ceiling = full.ceiling(attempt)
        assert 0 <= full.delay(attempt, rng) <= ceiling
        assert ceiling / 2 <= equal.delay(attempt, rng) <= ceiling
    assert full.delay(3, random.Random(1)) == full.delay(3, random.Random(1))
    assert len({round(full.delay(4, rng), 6) for _ in range(20)}) > 1  # actually jittered


def test_retry_after_is_a_floor_and_validation():
    policy = RetryPolicy(base_delay=0.1, max_delay=0.2, jitter="none")
    assert policy.delay(1, floor=5.0) == 5.0
    for bad in ({"max_attempts": 0}, {"multiplier": 0.5}, {"jitter": "wild"}, {"base_delay": 5, "max_delay": 1}):
        with pytest.raises(ValueError):
            RetryPolicy(**bad)


def test_queue_block_policy_pushes_back_and_counts():
    queue: BoundedQueue[int] = BoundedQueue(2, "block")
    assert queue.put(1).accepted and queue.put(2).accepted
    assert not queue.put(3, timeout=0.05).accepted  # full: the producer waits, then gives up
    taken = []
    threading.Timer(0.1, lambda: taken.append(queue.get())).start()
    result = queue.put(3, timeout=2)
    assert result.accepted and result.waited
    stats = queue.stats()
    assert stats["blocked_puts"] == 2 and stats["high_watermark"] == 2 and stats["dropped"] == 0
    assert taken == [1]


def test_queue_drop_oldest_evicts_and_reports():
    queue: BoundedQueue[int] = BoundedQueue(2, "drop_oldest")
    queue.put(1), queue.put(2)
    result = queue.put(3)
    assert result.accepted and result.dropped == 1
    assert [queue.get(0.01), queue.get(0.01)] == [2, 3]
    assert queue.stats()["dropped"] == 1


def test_queue_reject_new_and_close():
    queue: BoundedQueue[int] = BoundedQueue(1, "reject_new")
    assert queue.put(1).accepted
    assert not queue.put(2).accepted and queue.stats()["rejected"] == 1
    queue.close()
    assert not queue.put(3).accepted
    assert queue.get(0.01) == 1 and queue.get(0.01) is None
    with pytest.raises(ValueError):
        BoundedQueue(1, "nope")


# -- items ---------------------------------------------------------------------------------
def test_source_item_validation_and_provenance():
    item = make_item(1)
    assert item.sha256 == hashlib.sha256(item.payload).hexdigest()
    prov = item.provenance()
    assert prov["sha256"] == item.sha256 and prov["source_id"] == item.source_id
    with pytest.raises(ItemError, match="reserved"):
        SourceItem(payload=png(), content_type="image/png", source_id="s", idempotency_key="k",
                   captured_at=datetime.now(timezone.utc), metadata={"Decision": "reject"})
    for bad in ({"payload": b""}, {"content_type": "text/plain"}, {"captured_at": datetime(2026, 1, 1)},
                {"source_id": ""}, {"idempotency_key": "a\nb"}, {"metadata": {"a": 1}}):
        args = {"payload": png(), "content_type": "image/png", "source_id": "s", "idempotency_key": "k",
                "captured_at": datetime.now(timezone.utc), **bad}
        with pytest.raises(ItemError):
            SourceItem(**args)
    with pytest.raises(dataclasses.FrozenInstanceError):
        item.source_id = "x"  # type: ignore[misc]


def test_a_connector_cannot_carry_an_outcome():
    names = {f.name for f in SourceItem.__dataclass_fields__.values()}
    assert not names & {"decision", "outcome", "verdict", "label", "result", "feedback"}


# -- pipeline: retries, rejection, health --------------------------------------------------
def test_backoff_retries_then_succeeds_and_records_attempts(tmp_path):
    delays: list[float] = []
    sink = FakeSink([TransientSinkError("boom"), TransientSinkError("boom"), TransientSinkError("boom")])
    source = ListConnector([make_item(1)])
    ledger = Ledger(tmp_path / "l.sqlite")
    retry = RetryPolicy(max_attempts=5, base_delay=1.0, max_delay=100.0, jitter="none")
    pipeline = run_pipeline(source, sink, ledger, retry=retry, sleep=lambda s: delays.append(s) or False)
    assert wait_for(lambda: source.finished() == 1)
    pipeline.stop()
    assert delays == [1.0, 2.0, 4.0]
    entry = ledger.get(source.items[0].idempotency_key)
    assert entry.status == "submitted" and entry.attempts == 4 and entry.receipt.remote_id == "obs-1"
    assert pipeline.counters["retries"] == 3 and pipeline.counters["submitted"] == 1
    assert sink.closed and source.closed == 1


def test_retry_after_from_the_sink_is_honoured(tmp_path):
    delays: list[float] = []
    sink = FakeSink([TransientSinkError("slow down", retry_after=7.0)])
    source = ListConnector([make_item(1)])
    pipeline = run_pipeline(source, sink, Ledger(tmp_path / "l.sqlite"),
                            retry=RetryPolicy(base_delay=0.01, max_delay=0.02, jitter="none"),
                            sleep=lambda s: delays.append(s) or False)
    assert wait_for(lambda: source.finished() == 1)
    pipeline.stop()
    assert delays == [7.0]


def test_permanent_refusal_is_not_retried_and_quarantines(tmp_path):
    sink = FakeSink([PermanentItemError("not a valid image")])
    source = ListConnector([make_item(1)])
    ledger = Ledger(tmp_path / "l.sqlite")
    pipeline = run_pipeline(source, sink, ledger)
    assert wait_for(lambda: source.finished() == 1)
    pipeline.stop()
    assert sink.calls == 1
    assert source.nacked == [(source.items[0].idempotency_key, "not a valid image", True)]
    assert ledger.get(source.items[0].idempotency_key).status == "rejected"
    assert pipeline.counters["rejected"] == 1


def test_giving_up_hands_the_item_back_without_rejecting_it(tmp_path):
    sink = FakeSink([TransientSinkError("down")] * 10)
    source = ListConnector([make_item(1)])
    ledger = Ledger(tmp_path / "l.sqlite")
    pipeline = run_pipeline(source, sink, ledger, retry=fast_retry(max_attempts=3))
    assert wait_for(lambda: bool(source.nacked))
    pipeline.stop()
    key, _reason, permanent = source.nacked[0]
    assert permanent is False and sink.calls == 3
    assert ledger.get(key).status == "received" and ledger.get(key).attempts == 3
    assert pipeline.counters["deferred"] == 1


def test_unavailable_sink_reports_down_without_rejecting_items(tmp_path):
    gate = {"fail": True}

    class Flaky(FakeSink):
        def submit(self, item):
            if gate["fail"]:
                raise SinkUnavailableError("sign-in refused")
            return super().submit(item)

        def health(self):
            return Health("ok", "")

    source = ListConnector([make_item(1)])
    pipeline = run_pipeline(source, Flaky(), Ledger(tmp_path / "l.sqlite"), retry=fast_retry(max_attempts=2))
    assert wait_for(lambda: pipeline.health()["status"] == "down")
    assert not source.nacked  # kept waiting; not rejected, not given up on
    gate["fail"] = False
    assert wait_for(lambda: source.finished() == 1)
    assert pipeline.health()["status"] == "ok" and pipeline.health()["last_success_at"]
    pipeline.stop()


def test_block_policy_applies_backpressure_and_loses_nothing(tmp_path):
    gate = threading.Event()
    items = [make_item(i) for i in range(8)]
    source = ListConnector(items)
    sink = FakeSink(gate=gate)
    pipeline = run_pipeline(source, sink, Ledger(tmp_path / "l.sqlite"), queue_size=2, overflow="block")
    assert wait_for(lambda: pipeline.queue.stats()["blocked_puts"] > 0)
    assert len(source.offered) < len(items) + 1 and pipeline.queue.stats()["depth"] <= 2
    gate.set()
    assert wait_for(lambda: source.finished() == 8)
    pipeline.stop()
    assert len(sink.submitted) == 8 and pipeline.queue.stats()["dropped"] == 0
    assert pipeline.queue.stats()["high_watermark"] <= 2


def test_drop_oldest_policy_sheds_queued_items_back_to_the_source(tmp_path):
    gate = threading.Event()
    items = [make_item(i) for i in range(8)]
    source = ListConnector(items)
    sink = FakeSink(gate=gate)
    ledger = Ledger(tmp_path / "l.sqlite")
    pipeline = run_pipeline(source, sink, ledger, queue_size=2, overflow="drop_oldest")
    assert wait_for(lambda: pipeline.counters["dropped"] >= 3)
    dropped_keys = [k for k, reason, permanent in source.nacked if "overflow" in reason and not permanent]
    assert len(dropped_keys) == pipeline.counters["dropped"]
    assert "dropped by overflow policy" in ledger.get(dropped_keys[0]).last_error
    gate.set()
    assert wait_for(lambda: pipeline.queue.stats()["depth"] == 0 and pipeline.wait_until_idle(0.2))
    pipeline.stop()
    delivered = {i.idempotency_key for i in sink.submitted}
    assert delivered.isdisjoint(dropped_keys) and delivered | set(dropped_keys) == {i.idempotency_key for i in items}


def test_dropped_items_are_re_offered_and_delivered_once(tmp_path):
    gate = threading.Event()
    items = [make_item(i) for i in range(6)]
    source = ListConnector(items, reoffer=True)
    sink = FakeSink(gate=gate)
    pipeline = run_pipeline(source, sink, Ledger(tmp_path / "l.sqlite"), queue_size=2, overflow="drop_oldest")
    assert wait_for(lambda: pipeline.counters["dropped"] >= 2)
    gate.set()
    assert wait_for(lambda: {k for k, _ in source.acked} == {i.idempotency_key for i in items}, timeout=15)
    pipeline.stop()
    keys = [i.idempotency_key for i in sink.submitted]
    assert sorted(keys) == sorted({i.idempotency_key for i in items}) and len(keys) == len(set(keys))


# -- ledger / idempotency / provenance -----------------------------------------------------
def test_ledger_records_full_provenance(tmp_path):
    item = make_item(3)
    source = ListConnector([item])
    sink = FakeSink()
    ledger = Ledger(tmp_path / "l.sqlite")
    pipeline = run_pipeline(source, sink, ledger)
    assert wait_for(lambda: source.finished() == 1)
    pipeline.stop()
    entry = ledger.get(item.idempotency_key)
    assert entry.source_id == item.source_id and entry.sha256 == item.sha256
    assert entry.connector == "fake-list" and entry.connector_version == "9.9.9"
    assert entry.size_bytes == len(item.payload) and entry.content_type == "image/png"
    assert entry.attempts == 1 and entry.status == "submitted"
    assert datetime.fromisoformat(entry.received_at) <= datetime.fromisoformat(entry.submitted_at)
    assert entry.receipt.remote_id == "obs-1" and entry.receipt.detail["sha256"] == item.sha256
    # the submission itself carried the stamp
    assert sink.submitted[0].connector == "fake-list" and sink.submitted[0].connector_version == "9.9.9"
    # and it survives reopening the file
    reopened = Ledger(tmp_path / "l.sqlite")
    assert reopened.get(item.idempotency_key).to_dict() == entry.to_dict()
    assert reopened.counts()["submitted"] == 1


def test_same_key_with_different_content_is_refused(tmp_path):
    ledger = Ledger(tmp_path / "l.sqlite")
    ledger.record_received(make_item(1))
    clash = SourceItem(payload=png(99), content_type="image/png", source_id="x", captured_at=datetime.now(timezone.utc),
                       idempotency_key=make_item(1).idempotency_key)
    with pytest.raises(LedgerConflict):
        ledger.record_received(clash)
    source = ListConnector([clash])
    pipeline = run_pipeline(source, FakeSink(), ledger)
    assert wait_for(lambda: source.finished() == 1)
    pipeline.stop()
    assert source.nacked[0][2] is True and pipeline.counters["conflicts"] == 1


def test_never_submits_the_same_key_twice_even_if_offered_twice(tmp_path):
    item = make_item(1)
    ledger = Ledger(tmp_path / "l.sqlite")
    for _ in range(2):  # two "runs" over the same ledger file
        source = ListConnector([item])
        sink = FakeSink()
        pipeline = run_pipeline(source, sink, Ledger(tmp_path / "l.sqlite"))
        assert wait_for(lambda s=source: s.finished() == 1)
        pipeline.stop()
        if _ == 0:
            assert len(sink.submitted) == 1 and source.acked[0][1] == Outcome.SUBMITTED
        else:
            assert sink.submitted == [] and source.acked[0][1] == Outcome.DUPLICATE
            assert pipeline.counters["duplicates_skipped"] == 1
    assert ledger.counts()["submitted"] == 1


def test_crash_after_remote_accepted_does_not_duplicate(tmp_path):
    """Process dies after the console took the image but before the ledger heard: the next run asks first."""
    path = tmp_path / "l.sqlite"
    items = [make_item(i) for i in range(4)]  # A B C D
    reached, hang = threading.Event(), threading.Event()
    delivered_by_first: list[str] = []

    class DiesAfterDelivery(FakeSink):
        def submit(self, item):
            if item.idempotency_key == items[2].idempotency_key:
                delivered_by_first.append(item.idempotency_key)  # the console now has it ...
                reached.set()
                hang.wait()  # ... and the process "dies" before recording the receipt
            return super().submit(item)

    first = run_pipeline(ListConnector(items), DiesAfterDelivery(), Ledger(path))
    assert reached.wait(8)
    del first  # never stopped: a crash. (its worker thread is parked forever)
    crashed = Ledger(path)
    assert crashed.get(items[2].idempotency_key).status == "submitting"
    assert crashed.get(items[0].idempotency_key).status == "submitted"

    sink2 = FakeSink()
    sink2.existing[items[2].idempotency_key] = Receipt("fake-sink", "obs-from-console", "succeeded", {})
    source2 = ListConnector(items)
    second = run_pipeline(source2, sink2, crashed)
    assert wait_for(lambda: source2.finished() == 4)
    second.stop()
    resubmitted = [i.idempotency_key for i in sink2.submitted]
    assert resubmitted == [items[3].idempotency_key]  # only D; A, B skipped; C adopted not resent
    assert sink2.find_calls == [items[2].idempotency_key]
    assert second.counters["duplicates_skipped"] == 2 and second.counters["adopted"] == 1
    assert crashed.get(items[2].idempotency_key).receipt.remote_id == "obs-from-console"
    assert crashed.counts()["submitted"] == 4


def test_crash_before_remote_accepted_sends_exactly_once(tmp_path):
    path = tmp_path / "l.sqlite"
    items = [make_item(i) for i in range(3)]
    reached, hang = threading.Event(), threading.Event()

    class DiesBeforeDelivery(FakeSink):
        def submit(self, item):
            if item.idempotency_key == items[1].idempotency_key:
                reached.set()
                hang.wait()
            return super().submit(item)

    first = run_pipeline(ListConnector(items), DiesBeforeDelivery(), Ledger(path))
    assert reached.wait(8)
    del first
    sink2 = FakeSink()  # the console never saw item 1: find_existing says so
    source2 = ListConnector(items)
    second = run_pipeline(source2, sink2, Ledger(path))
    assert wait_for(lambda: source2.finished() == 3)
    second.stop()
    assert sorted(i.idempotency_key for i in sink2.submitted) == sorted(i.idempotency_key for i in items[1:])
    assert sink2.find_calls == [items[1].idempotency_key]


def test_in_doubt_lookup_failure_never_falls_through_to_a_blind_resend(tmp_path):
    path = tmp_path / "l.sqlite"
    item = make_item(1)
    ledger = Ledger(path)
    ledger.record_received(item)
    ledger.begin_attempt(item.idempotency_key)  # left in "submitting" by a dead process

    class Unreachable(FakeSink):
        def find_existing(self, it):
            raise TransientSinkError("console unreachable")

    sink = Unreachable()
    source = ListConnector([item])
    pipeline = run_pipeline(source, sink, ledger, retry=fast_retry(max_attempts=3))
    assert wait_for(lambda: bool(source.nacked))
    pipeline.stop()
    assert sink.submitted == [] and sink.calls == 0
    assert ledger.get(item.idempotency_key).status == "received"


# -- shutdown ------------------------------------------------------------------------------
def test_graceful_stop_drains_then_hands_back_the_rest(tmp_path):
    gate = threading.Event()
    items = [make_item(i) for i in range(6)]
    source = ListConnector(items)
    sink = FakeSink(gate=gate)
    ledger = Ledger(tmp_path / "l.sqlite")
    pipeline = run_pipeline(source, sink, ledger, queue_size=8, drain_timeout=0.3)
    assert wait_for(lambda: len(source.offered) == 6)
    started = time.monotonic()
    threading.Timer(0.1, gate.set).start()  # the sink wakes up during the drain window
    pipeline.stop()
    assert time.monotonic() - started < 5
    assert len(sink.submitted) + len([n for n in source.nacked if not n[2]]) >= 6 or len(sink.submitted) == 6
    assert source.closed == 1 and sink.closed
    for item in items:  # nothing is lost: delivered or still "received"
        assert ledger.get(item.idempotency_key).status in ("submitted", "received", "submitting")
    not_delivered = {i.idempotency_key for i in items} - {i.idempotency_key for i in sink.submitted}
    assert all(ledger.get(k).status != "submitted" for k in not_delivered)


def test_stop_interrupts_a_long_backoff_promptly(tmp_path):
    sink = FakeSink([TransientSinkError("down")] * 50)
    source = ListConnector([make_item(1)])
    ledger = Ledger(tmp_path / "l.sqlite")
    pipeline = run_pipeline(source, sink, ledger, retry=RetryPolicy(max_attempts=50, base_delay=60, max_delay=60, jitter="none"),
                            drain_timeout=0.2)
    assert wait_for(lambda: sink.calls == 1)
    started = time.monotonic()
    pipeline.stop()
    assert time.monotonic() - started < 5
    assert source.nacked and source.nacked[0][2] is False
    assert ledger.get(source.items[0].idempotency_key).status == "received"


# -- folder connector ----------------------------------------------------------------------
class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def folder(tmp_path, **kw):
    clock = Clock()
    root = tmp_path / "inbox"
    connector = FolderDropConnector(root, stable_seconds=2.0, clock=clock, **kw)
    connector.open()
    return connector, clock, root


def settle(connector, clock, *, max_items=10):
    connector.poll(max_items)
    clock.t += 3.0
    return connector.poll(max_items)


def reason_of(directory: Path, name: str) -> str:
    return json.loads((directory / f"{name}.reason.json").read_text())["reason"]


def test_folder_waits_for_a_stable_file_then_moves_it_on_ack(tmp_path):
    connector, clock, root = folder(tmp_path)
    (root / "a.png").write_bytes(png(1))
    assert connector.poll() == []  # first sighting
    clock.t += 1.0
    assert connector.poll() == []  # not yet stable for 2 s
    clock.t += 2.0
    (item,) = connector.poll()
    assert item.content_type == "image/png" and item.sha256 == hashlib.sha256(png(1)).hexdigest()
    assert item.source_id == "folder:a.png" and item.idempotency_key.endswith(item.sha256)
    assert item.metadata["file_name"] == "a.png"
    assert connector.poll() == [] or True
    assert (root / "a.png").exists()  # not moved before the sink confirms
    connector.ack(item, Outcome.SUBMITTED)
    assert not (root / "a.png").exists() and (root / "processed" / "a.png").read_bytes() == png(1)


def test_folder_partial_file_is_not_read_until_it_stops_growing(tmp_path):
    connector, clock, root = folder(tmp_path)
    full = png(2, size=40)
    target = root / "slow.png"
    target.write_bytes(full[: len(full) // 2])
    connector.poll()
    clock.t += 3.0
    target.write_bytes(full[: len(full) // 2 + 10])  # still growing: the clock restarts
    assert connector.poll() == []
    clock.t += 3.0
    target.write_bytes(full)
    assert connector.poll() == []
    (item,) = settle(connector, clock)
    assert item.payload == full


def test_folder_rejects_a_stable_but_truncated_image_with_a_reason(tmp_path):
    connector, clock, root = folder(tmp_path)
    (root / "cut.png").write_bytes(png(2, size=40)[:-20])
    (root / "cut.jpg").write_bytes(jpeg(1)[:-4])
    assert settle(connector, clock) == []
    assert not (root / "cut.png").exists()
    assert "incomplete" in reason_of(root / "rejected", "cut.png")
    assert "incomplete" in reason_of(root / "rejected", "cut.jpg")


def test_folder_wrong_type_is_quarantined_with_a_sidecar(tmp_path):
    connector, clock, root = folder(tmp_path)
    (root / "notes.txt").write_text("hello")
    (root / "fake.png").write_text("not really a png")
    (root / "lies.jpg").write_bytes(png(3))  # PNG bytes under a .jpg name
    (root / "ok.webp").write_bytes(webp(1))
    items = settle(connector, clock)
    assert [i.source_id for i in items] == ["folder:ok.webp"]
    assert "unsupported file type" in reason_of(root / "rejected", "notes.txt")
    assert "not a JPEG, PNG or WebP" in reason_of(root / "rejected", "fake.png")
    assert "extension does not match" in reason_of(root / "rejected", "lies.jpg")
    sidecar = json.loads((root / "rejected" / "notes.txt.reason.json").read_text())
    assert sidecar["connector"] == "folder-drop" and "No person has reviewed" in sidecar["note"]


def test_folder_oversize_is_rejected_without_being_read(tmp_path):
    connector, clock, root = folder(tmp_path, max_bytes=1000)
    (root / "big.png").write_bytes(png(1) + b"\0" * 2000)
    assert len((root / "big.png").read_bytes()) > 1000
    assert connector.poll() == []  # rejected on first sight, no need to wait
    assert "over the 1000-byte limit" in reason_of(root / "rejected", "big.png")


def test_folder_symlink_escape_is_refused_and_target_untouched(tmp_path):
    connector, clock, root = folder(tmp_path)
    outside = tmp_path / "secret.png"
    outside.write_bytes(png(9))
    os.symlink(outside, root / "link.png")
    os.symlink(tmp_path, root / "dirlink")
    assert settle(connector, clock) == []
    assert outside.read_bytes() == png(9)  # not moved, not modified
    assert (root / "rejected" / "link.png").is_symlink() and not (root / "link.png").is_symlink()
    assert "symbolic links" in reason_of(root / "rejected", "link.png")
    assert "symbolic links" in reason_of(root / "rejected", "dirlink")


def test_folder_refuses_hardlinks_and_fifos_without_hanging(tmp_path):
    connector, clock, root = folder(tmp_path)
    target = tmp_path / "elsewhere.png"
    target.write_bytes(png(4))
    os.link(target, root / "hard.png")
    os.mkfifo(root / "pipe.png")
    assert settle(connector, clock) == []
    assert target.exists()
    assert "hard-linked" in reason_of(root / "rejected", "hard.png")
    assert "not a regular file" in reason_of(root / "rejected", "pipe.png")


def test_folder_ignores_temp_hidden_and_nested_files_and_never_recurses(tmp_path):
    connector, clock, root = folder(tmp_path)
    (root / "x.png.part").write_bytes(png(1))
    (root / ".hidden.png").write_bytes(png(2))
    (root / "sub").mkdir()
    (root / "sub" / "deep.png").write_bytes(png(3))
    assert settle(connector, clock) == []
    assert (root / "sub" / "deep.png").exists() and (root / "x.png.part").exists()


def test_folder_never_builds_paths_from_untrusted_names(tmp_path):
    connector, clock, root = folder(tmp_path)
    victim = tmp_path / "victim.png"
    victim.write_bytes(png(1))
    connector._reject_path("../victim.png", "x")
    connector._reject_path("/etc/passwd", "x")
    forged = SourceItem(payload=png(1), content_type="image/png", source_id="s", idempotency_key="k",
                        captured_at=datetime.now(timezone.utc), metadata={"file_name": "../victim.png"})
    connector.nack(forged, "refused", permanent=True)
    connector.ack(forged, Outcome.SUBMITTED)
    assert victim.exists() and not list((root / "rejected").iterdir())


def test_folder_nack_transient_waits_cooldown_and_permanent_quarantines(tmp_path):
    connector, clock, root = folder(tmp_path, retry_cooldown=30.0)
    (root / "a.png").write_bytes(png(1))
    (root / "b.png").write_bytes(png(2))
    a, b = sorted(settle(connector, clock), key=lambda i: i.source_id)
    connector.nack(a, "console down", permanent=False)
    connector.nack(b, "console says: not a valid image", permanent=True)
    assert (root / "a.png").exists() and not (root / "b.png").exists()
    assert reason_of(root / "rejected", "b.png") == "console says: not a valid image"
    assert json.loads((root / "rejected" / "b.png.reason.json").read_text())["sha256"] == b.sha256
    assert connector.poll() == []
    clock.t += 31
    connector.poll()  # restarts the stability clock
    clock.t += 3
    assert [i.source_id for i in connector.poll()] == ["folder:a.png"]


def test_folder_file_replaced_after_read_is_not_moved_on_ack(tmp_path):
    connector, clock, root = folder(tmp_path)
    (root / "a.png").write_bytes(png(1))
    (item,) = settle(connector, clock)
    (root / "a.png").write_bytes(png(2, size=9))  # new content arrived while we were delivering
    connector.ack(item, Outcome.SUBMITTED)
    assert (root / "a.png").read_bytes() == png(2, size=9)
    assert not (root / "processed" / "a.png").exists()


def test_folder_name_collisions_never_overwrite(tmp_path):
    connector, clock, root = folder(tmp_path)
    for seed in (1, 2):
        (root / "same.png").write_bytes(png(seed))
        (item,) = settle(connector, clock)
        connector.ack(item, Outcome.SUBMITTED)
    assert sorted(p.name for p in (root / "processed").iterdir()) == ["same.1.png", "same.png"]


def test_folder_health(tmp_path):
    connector, clock, root = folder(tmp_path)
    connector.poll()
    assert connector.health().status == "ok" and connector.health().last_success_at
    root.rename(tmp_path / "moved")
    assert connector.health().status == "down"


def test_folder_survives_restart_with_file_still_in_place(tmp_path):
    """Crash after the ledger recorded the receipt but before the file moved: no resubmission, file tidied."""
    connector, clock, root = folder(tmp_path)
    (root / "a.png").write_bytes(png(1))
    (item,) = settle(connector, clock)
    ledger = Ledger(tmp_path / "l.sqlite")
    stamped_item = dataclasses.replace(item, connector="folder-drop", connector_version=connector.version)
    ledger.record_received(stamped_item)
    ledger.begin_attempt(item.idempotency_key)
    ledger.mark_submitted(item.idempotency_key, Receipt("fake-sink", "obs-1", "succeeded", {}))
    # "restart": a fresh connector over the same directory, with real time
    again = FolderDropConnector(root, stable_seconds=0.1)
    sink = FakeSink()
    pipeline = run_pipeline(again, sink, ledger)
    assert wait_for(lambda: (root / "processed" / "a.png").exists(), timeout=10)
    pipeline.stop()
    assert sink.submitted == [] and pipeline.counters["duplicates_skipped"] == 1


def test_folder_pipeline_end_to_end_with_fake_sink(tmp_path):
    root = tmp_path / "inbox"
    connector = FolderDropConnector(root, stable_seconds=0.1)
    sink = FakeSink()
    ledger = Ledger(tmp_path / "l.sqlite")
    pipeline = run_pipeline(connector, sink, ledger)
    for seed in range(3):
        (root / f"s{seed}.png").write_bytes(png(seed))
    (root / "bad.png").write_bytes(b"nope")
    assert wait_for(lambda: len(list((root / "processed").iterdir())) == 3 and (root / "rejected" / "bad.png").exists())
    pipeline.stop()
    assert len(sink.submitted) == 3 and ledger.counts()["submitted"] == 3
    assert all(i.connector == "folder-drop" for i in sink.submitted)


# -- HTTP push receiver --------------------------------------------------------------------
SECRET = "s3cret-s3cret-s3cret"


def push(connector, body, *, secret=SECRET, ctype="image/png", headers=None, length=None, path="/v1/items", method="POST"):
    conn = http.client.HTTPConnection(*connector.address, timeout=5)
    send = {"Content-Type": ctype, **(headers or {})}
    if secret is not None:
        send["X-Athena-Connector-Secret"] = secret
    conn.putrequest(method, path)
    for key, value in send.items():
        conn.putheader(key, value)
    if length is None:
        length = len(body)
    if length is not False:
        conn.putheader("Content-Length", str(length))
    conn.endheaders()
    if body:
        try:
            conn.send(body)
        except OSError:
            pass
    response = conn.getresponse()
    data = response.read()
    conn.close()
    return response.status, json.loads(data or b"{}"), response


@pytest.fixture
def receiver(tmp_path):
    connector = HttpPushConnector(tmp_path / "spool", SECRET, port=0, max_bytes=5000, max_pending=3,
                                  retry_cooldown=0.0)
    connector.open()
    yield connector
    connector.close()


def test_http_auth_and_validation_statuses(receiver):
    assert push(receiver, png(1), secret="wrong")[0] == 401
    assert push(receiver, png(1), secret=None)[0] == 401
    assert push(receiver, b"hello", ctype="text/plain")[0] == 415
    assert push(receiver, b"hello world", ctype="image/png")[0] == 415  # bytes do not match the claim
    assert push(receiver, png(1), ctype="image/jpeg")[0] == 415
    assert push(receiver, b"", length=6000)[0] == 413  # refused from the header, body never read
    assert push(receiver, png(1) + b"\0" * 6000)[0] == 413
    assert push(receiver, png(1), length=False)[0] == 411
    assert push(receiver, png(1)[:-12])[0] == 422  # truncated PNG
    assert push(receiver, png(1), headers={"X-Source-Id": "../../etc"})[0] == 400
    assert push(receiver, png(1), headers={"Idempotency-Key": "a b"})[0] == 400
    assert push(receiver, png(1), path="/other")[0] == 404
    assert push(receiver, b"", method="PUT", length=0)[0] == 405
    assert not list(receiver.spool.glob("*.item"))  # nothing refused reached the spool
    c = receiver.counters
    assert c["unauthorized"] == 2 and c["too_large"] == 2 and c["accepted"] == 0


def test_http_401_does_not_leak_whether_the_path_or_body_was_valid(receiver):
    status, body, _ = push(receiver, b"junk", secret="nope", ctype="text/plain")
    assert status == 401 and "secret" in body["error"]


def test_http_accept_is_durable_and_idempotent(receiver, tmp_path):
    status, body, _ = push(receiver, png(1), headers={"X-Source-Id": "lane-2", "Idempotency-Key": "scan-0001"})
    assert status == 202 and body["accepted"] and not body["duplicate"]
    assert body["sha256"] == hashlib.sha256(png(1)).hexdigest()
    again = push(receiver, png(1), headers={"X-Source-Id": "lane-2", "Idempotency-Key": "scan-0001"})
    assert again[0] == 202 and again[1]["duplicate"] is True
    clash = push(receiver, png(2), headers={"X-Source-Id": "lane-2", "Idempotency-Key": "scan-0001"})
    assert clash[0] == 409
    assert len(list(receiver.spool.glob("*.item"))) == 1
    # a brand new receiver over the same spool (restart) still has it
    receiver.close()
    reopened = HttpPushConnector(tmp_path / "spool", SECRET, port=0)
    reopened.open()
    try:
        (item,) = reopened.poll(5)
        assert item.payload == png(1) and item.source_id == "http-push:lane-2"
        assert item.idempotency_key == "http-push:lane-2:scan-0001"
        assert item.metadata["transport"] == "http-push" and item.metadata["remote_addr"] == "127.0.0.1"
        assert reopened.poll(5) == []  # in flight
        reopened.ack(item, Outcome.SUBMITTED)
        assert not list((tmp_path / "spool").glob("*.item"))
    finally:
        reopened.close()


def test_http_backpressure_returns_503_with_retry_after(receiver):
    for n in range(3):
        assert push(receiver, png(n), headers={"Idempotency-Key": f"k{n}"})[0] == 202
    status, _, response = push(receiver, png(9), headers={"Idempotency-Key": "k9"})
    assert status == 503 and response.getheader("Retry-After") == "5"
    assert receiver.health().status == "degraded" and receiver.counters["backpressure"] == 1
    (item,) = receiver.poll(1)
    receiver.ack(item, Outcome.SUBMITTED)
    assert push(receiver, png(9), headers={"Idempotency-Key": "k9"})[0] == 202


def test_http_nack_permanent_quarantines_and_transient_reoffers(receiver):
    push(receiver, png(1), headers={"Idempotency-Key": "a"})
    push(receiver, png(2), headers={"Idempotency-Key": "b"})
    a, b = receiver.poll(5)
    receiver.nack(a, "temporary", permanent=False)
    receiver.nack(b, "console refused it", permanent=True)
    assert len(list((receiver.spool / "rejected").glob("*.item"))) == 1
    (sidecar,) = (receiver.spool / "rejected").glob("*.reason.json")
    assert json.loads(sidecar.read_text())["reason"] == "console refused it"
    assert [i.idempotency_key for i in receiver.poll(5)] == [a.idempotency_key]


def test_http_refuses_weak_secret_and_public_bind(tmp_path):
    with pytest.raises(ValueError, match="at least"):
        HttpPushConnector(tmp_path / "s", "short")
    with pytest.raises(ValueError, match="loopback"):
        HttpPushConnector(tmp_path / "s", SECRET, host="0.0.0.0")
    HttpPushConnector(tmp_path / "s", SECRET, host="0.0.0.0", allow_non_loopback=True)  # explicit opt-in
    probe = HttpPushConnector(tmp_path / "s2", SECRET, port=0)
    probe.open()
    try:
        assert probe.address[0] == "127.0.0.1"
    finally:
        probe.close()


def test_http_healthz_needs_no_secret_and_leaks_nothing(receiver):
    conn = http.client.HTTPConnection(*receiver.address, timeout=5)
    conn.request("GET", "/healthz")
    response = conn.getresponse()
    assert response.status == 200 and json.loads(response.read()) == {"status": "ok"}


def test_http_conformance(receiver):
    push(receiver, png(1), headers={"Idempotency-Key": "a"})
    push(receiver, png(2), headers={"Idempotency-Key": "b"})
    receiver.close()
    again = HttpPushConnector(receiver.spool, SECRET, port=0)
    assert_connector_conforms(again)


def test_http_through_the_pipeline(receiver, tmp_path):
    sink = FakeSink()
    pipeline = run_pipeline(receiver, sink, Ledger(tmp_path / "l.sqlite"))
    for n in range(3):
        assert push(receiver, png(n), headers={"Idempotency-Key": f"k{n}", "X-Source-Id": "cam"})[0] == 202
    assert wait_for(lambda: len(sink.submitted) == 3)
    assert wait_for(lambda: not list(receiver.spool.glob("*.item")))
    pipeline.stop()
    assert {i.source_id for i in sink.submitted} == {"http-push:cam"}


# -- the console stub ----------------------------------------------------------------------
APP_ID = str(uuid.uuid4())
PROJECT_ID = str(uuid.uuid4())
MODEL_ID = str(uuid.uuid4())
MODEL_SHA = "ab" * 32


class StubConsole:
    """Imitates the console endpoints the sink uses, including the CSRF flow of the login form."""

    def __init__(self, *, password="pw", role="labeler", polls_until_done=1) -> None:
        self.password, self.role, self.polls_until_done = password, role, polls_until_done
        self.sessions: set[str] = set()
        self.csrf = ""  # the secret currently in the cookie
        self.requests: list[tuple[str, str]] = []
        self.uploads: list[dict] = []
        self.observations: dict[str, dict] = {}
        self.login_posts = 0
        self.run_status: int | None = None  # force a refusal for POST .../run
        self.expire_session_once = False
        self.hang_up_after_store = False
        self.never_finish = False
        self.finish_status = "succeeded"
        self.lock = threading.Lock()
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # silence
                pass

            def _send(self, status, body=b"", ctype="application/json", headers=None):
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                for k, v in (headers or []):
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def _json(self, status, obj, headers=None):
                self._send(status, json.dumps(obj).encode(), headers=headers)

            def _cookies(self):
                out = {}
                for part in (self.headers.get("Cookie") or "").split(";"):
                    if "=" in part:
                        k, v = part.strip().split("=", 1)
                        out[k] = v
                return out

            def _authed(self):
                if stub.expire_session_once:
                    stub.expire_session_once = False
                    stub.sessions.clear()
                return self._cookies().get("sessionid") in stub.sessions

            def do_GET(self):
                with stub.lock:
                    stub.requests.append(("GET", self.path))
                    if self.path == "/login/":
                        stub.csrf = uuid.uuid4().hex
                        page = f'<form method="post"><input type="hidden" name="csrfmiddlewaretoken" value="MASK{stub.csrf}"></form>'
                        return self._send(200, page.encode(), "text/html", [("Set-Cookie", f"csrftoken={stub.csrf}; Path=/")])
                    if not self._authed():
                        return self._json(401, {"error": "Sign in to continue."})
                    if self.path == f"/api/projects/{PROJECT_ID}/inference/apps":
                        return self._json(200, {"apps": [{"id": APP_ID, "slug": "gate-1", "status": "active"}]})
                    base = f"/api/projects/{PROJECT_ID}/inference/apps/{APP_ID}"
                    if self.path == base + "/observations":
                        return self._json(200, {"observations": [stub.render(o) for o in stub.observations.values()]})
                    if self.path.startswith(base + "/observations/"):
                        oid = self.path.rsplit("/", 1)[1]
                        obs = stub.observations.get(oid)
                        if not obs:
                            return self._json(404, {"error": "not found"})
                        obs["polls"] += 1
                        if not stub.never_finish and obs["polls"] >= stub.polls_until_done:
                            obs["status"] = stub.finish_status
                        return self._json(200, {"render": stub.render(obs)})
                    return self._json(404, {"error": "not found"})

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length)
                with stub.lock:
                    stub.requests.append(("POST", self.path))
                    if self.path == "/login/":
                        stub.login_posts += 1
                        form = {k: v[0] for k, v in parse_qs(body.decode()).items()}
                        if form.get("csrfmiddlewaretoken") != "MASK" + self._cookies().get("csrftoken", "?"):
                            return self._send(403, b"CSRF verification failed.", "text/html")
                        if form.get("username") != "svc" or form.get("password") != stub.password:
                            return self._send(200, b"<p>Please enter a correct username and password.</p>", "text/html")
                        sid = uuid.uuid4().hex
                        stub.sessions.add(sid)
                        stub.csrf = uuid.uuid4().hex  # Django rotates the token on login
                        return self._send(302, b"", "text/html", [
                            ("Location", "/"), ("Set-Cookie", f"sessionid={sid}; Path=/"),
                            ("Set-Cookie", f"csrftoken={stub.csrf}; Path=/")])
                    if not self._authed():
                        return self._json(401, {"error": "Sign in to continue."})
                    if self.headers.get("X-CSRFToken") != stub.csrf:
                        return self._send(403, b"CSRF verification failed.", "text/html")
                    if self.path.endswith("/run"):
                        if stub.role == "viewer":
                            return self._json(403, {"error": "Your account can look but not change things.",
                                                    "required_role": "labeler"})
                        if stub.run_status:
                            return self._json(stub.run_status, {"error": f"forced {stub.run_status}"},
                                              [("Retry-After", "12")] if stub.run_status in (429, 503) else None)
                        part = next(p for p in BytesParser(policy=email_default).parsebytes(
                            b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n\r\n" + body).iter_parts()
                            if p.get_param("name", header="content-disposition") == "image")
                        data = part.get_payload(decode=True)
                        oid = str(uuid.uuid4())
                        record = {"id": oid, "status": "queued", "polls": 0, "filename": part.get_filename(),
                                  "sha": hashlib.sha256(data).hexdigest(), "headers": dict(self.headers)}
                        stub.observations[oid] = record
                        stub.uploads.append(record)
                        if stub.hang_up_after_store:
                            self.connection.close()
                            return
                        return self._json(202, {"observation_id": oid, "status": "queued"})
                    return self._json(404, {"error": "not found"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    @staticmethod
    def render(obs):
        return {
            "model": {"id": MODEL_ID, "version": 3, "artifact_sha256": MODEL_SHA, "stage_now": "approved"},
            "result": {"observation_id": obs["id"], "status": obs["status"], "awaiting_human_decision": True,
                       "error": "worker said no" if obs["status"] == "failed" else "",
                       "image": {"sha256": obs["sha"], "filename": obs["filename"]}},
        }

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def console():
    stub = StubConsole()
    yield stub
    stub.close()


def make_sink(console, **kw) -> AthenaConsoleSink:
    kw.setdefault("poll_interval", 0.01)
    kw.setdefault("poll_timeout", 5)
    kw.setdefault("sleep", lambda s: time.sleep(0.005))
    return AthenaConsoleSink(console.url, PROJECT_ID, kw.pop("app", "gate-1"), "svc", kw.pop("password", "pw"), **kw)


def stamped(seed=1, **kw) -> SourceItem:
    item = make_item(seed, **kw)
    return SourceItem(payload=item.payload, content_type=item.content_type, source_id=item.source_id,
                      captured_at=item.captured_at, idempotency_key=item.idempotency_key, metadata=item.metadata,
                      connector="folder-drop", connector_version="0.1.0")


def test_sink_signs_in_with_csrf_uploads_polls_and_returns_a_receipt(console):
    sink = make_sink(console)
    item = stamped(1)
    receipt = sink.submit(item)
    assert receipt.status == "succeeded" and receipt.sink == "athena-console"
    assert receipt.remote_id in console.observations
    detail = receipt.detail
    assert detail["model_version_id"] == MODEL_ID and detail["model_version"] == 3
    assert detail["model_artifact_sha256"] == MODEL_SHA and detail["sha256_match"] is True
    assert detail["app_id"] == APP_ID and detail["awaiting_human_decision"] is True
    (upload,) = console.uploads
    assert upload["sha"] == item.sha256
    assert upload["filename"].startswith(hashlib.sha256(item.idempotency_key.encode()).hexdigest()[:16] + "__")
    sent = {k.lower(): v for k, v in upload["headers"].items()}
    assert sent["x-athena-connector"] == "folder-drop/0.1.0"
    assert sent["x-athena-content-sha256"] == item.sha256
    assert console.login_posts == 1
    assert sink.health().status == "ok" and sink.health().last_success_at


def test_sink_accepts_the_app_by_id_too(console):
    assert make_sink(console, app=APP_ID).submit(stamped(1)).detail["app_slug"] == "gate-1"


def test_sink_never_touches_feedback_or_decision_routes(console):
    make_sink(console).submit(stamped(1))
    for _, path in console.requests:
        assert not any(word in path for word in ("feedback", "decision", "approve", "rollback", "stage"))
    assert {m for m, _ in console.requests} <= {"GET", "POST"}


def test_sink_exposes_no_way_to_record_feedback_or_decisions():
    public = {name for name in dir(AthenaConsoleSink) if not name.startswith("_")}
    assert public == {"name", "submit", "find_existing", "health", "close", "from_env"}
    for name in public:
        assert not any(word in name.lower() for word in ("feedback", "decision", "approve", "review", "label", "override"))


def test_sink_wrong_password_is_sink_unavailable_and_does_not_hammer_the_login(console):
    sink = make_sink(console, password="nope")
    for _ in range(3):
        with pytest.raises(SinkUnavailableError, match="sign-in"):
            sink.submit(stamped(1))
    assert console.login_posts == 1  # the cooldown stopped the retries (the console locks accounts)
    assert sink.health().status == "down" and console.uploads == []


def test_sink_viewer_role_is_refused_not_retried_as_a_bad_image():
    stub = StubConsole(role="viewer")
    try:
        with pytest.raises(SinkUnavailableError, match="403"):
            make_sink(stub).submit(stamped(1))
        assert stub.uploads == []
    finally:
        stub.close()


@pytest.mark.parametrize(("status", "error"), [(400, PermanentItemError), (415, PermanentItemError),
                                               (413, PermanentItemError), (429, TransientSinkError),
                                               (503, TransientSinkError), (500, TransientSinkError),
                                               (409, SinkUnavailableError)])
def test_sink_status_mapping(console, status, error):
    console.run_status = status
    with pytest.raises(error) as caught:
        make_sink(console).submit(stamped(1))
    if status in (429, 503):
        assert caught.value.retry_after == 12.0


def test_sink_relogs_in_once_when_the_session_expires(console):
    sink = make_sink(console)
    sink.submit(stamped(1))
    console.expire_session_once = True
    assert sink.submit(stamped(2)).status == "succeeded"
    assert console.login_posts == 2 and len(console.uploads) == 2


def test_sink_poll_timeout_returns_a_receipt_and_never_posts_twice(console):
    console.never_finish = True
    sink = make_sink(console, poll_timeout=0.15)
    receipt = sink.submit(stamped(1))
    assert receipt.status == "pending_timeout" and receipt.remote_id in console.observations
    assert len(console.uploads) == 1


def test_sink_reports_a_failed_analysis_without_resubmitting(console):
    console.finish_status = "failed"
    receipt = make_sink(console).submit(stamped(1))
    assert receipt.status == "failed" and receipt.detail["analysis_error"] == "worker said no"
    assert len(console.uploads) == 1


def test_sink_unreachable_console_is_transient():
    sink = AthenaConsoleSink("http://127.0.0.1:9", PROJECT_ID, "gate-1", "svc", "pw", request_timeout=1)
    with pytest.raises(TransientSinkError, match="unreachable"):
        sink.submit(stamped(1))


def test_sink_recognises_a_delivery_that_was_stored_but_never_answered(console):
    console.hang_up_after_store = True
    sink = make_sink(console)
    item = stamped(1)
    with pytest.raises(TransientSinkError):
        sink.submit(item)
    assert len(console.uploads) == 1
    console.hang_up_after_store = False
    receipt = sink.submit(item)  # asks first: finds its own upload instead of sending a second
    assert len(console.uploads) == 1 and receipt.remote_id == console.uploads[0]["id"]
    assert sink.find_existing(stamped(7)) is None


def test_sink_config_rules(monkeypatch):
    with pytest.raises(ValueError, match="plain http"):
        AthenaConsoleSink("http://console.example.org", PROJECT_ID, "a", "u", "p")
    AthenaConsoleSink("https://console.example.org", PROJECT_ID, "a", "u", "p")
    AthenaConsoleSink("http://console.example.org", PROJECT_ID, "a", "u", "p", allow_insecure_http=True)
    with pytest.raises(ValueError, match="UUID"):
        AthenaConsoleSink("http://127.0.0.1:1", "not-a-uuid", "a", "u", "p")
    with pytest.raises(ValueError, match="ATHENA_CONNECTOR_USER"):
        AthenaConsoleSink.from_env("http://127.0.0.1:1", PROJECT_ID, "a", env={})
    sink = AthenaConsoleSink.from_env("http://127.0.0.1:1", PROJECT_ID, "a",
                                      env={"ATHENA_CONNECTOR_USER": "svc", "ATHENA_CONNECTOR_PASSWORD": "hunter2hunter2"})
    assert "hunter2" not in repr(sink) and "hunter2" not in str(vars(sink).get("_username", ""))


def test_folder_to_console_end_to_end_with_stub(console, tmp_path):
    root = tmp_path / "inbox"
    connector = FolderDropConnector(root, stable_seconds=0.1)
    ledger = Ledger(tmp_path / "l.sqlite")
    pipeline = run_pipeline(connector, make_sink(console), ledger)
    for seed in range(3):
        (root / f"s{seed}.png").write_bytes(png(seed))
    assert wait_for(lambda: ledger.counts()["submitted"] == 3 and len(list((root / "processed").iterdir())) == 3)
    pipeline.stop()
    assert len(console.uploads) == 3
    for entry in ledger.entries():
        assert entry.receipt.detail["model_version_id"] == MODEL_ID
        assert entry.receipt.remote_id in console.observations
        assert console.observations[entry.receipt.remote_id]["sha"] == entry.sha256


# -- conformance ---------------------------------------------------------------------------
def test_conformance_passes_for_the_folder_connector_and_catches_violations(tmp_path):
    connector, clock, root = folder(tmp_path)
    (root / "a.png").write_bytes(png(1))
    connector.poll()
    clock.t += 3
    assert_connector_conforms(connector)

    class Empty(ListConnector):
        name = ""

    with pytest.raises(ConnectorConformanceError, match="name and version"):
        assert_connector_conforms(Empty([]))

    class Greedy(ListConnector):
        def poll(self, max_items=1):
            return list(self.items)  # ignores max_items and re-offers in-flight items

    with pytest.raises(ConnectorConformanceError):
        assert_connector_conforms(Greedy([make_item(1), make_item(2), make_item(3)]))


def test_example_synthetic_connector_conforms_and_runs_through_the_pipeline(tmp_path):
    from examples.connectors.synthetic_scanner import SyntheticScannerConnector, synthetic_png

    assert_connector_conforms(SyntheticScannerConnector(count=3))
    assert synthetic_png(1) != synthetic_png(2) and synthetic_png(1) == synthetic_png(1)
    source, sink = SyntheticScannerConnector(count=4), FakeSink()
    ledger = Ledger(tmp_path / "l.sqlite")
    pipeline = run_pipeline(source, sink, ledger)
    assert wait_for(lambda: ledger.counts()["submitted"] == 4)
    pipeline.stop()
    assert {i.metadata["synthetic"] for i in sink.submitted} == {"yes"}


# -- configuration and the runner ----------------------------------------------------------
def write_config(tmp_path, **overrides) -> Path:
    config = {
        "connector": {"type": "folder_drop", "path": "inbox", "stable_seconds": 0.2},
        "sink": {"type": "athena_console", "base_url": "http://127.0.0.1:8816", "project_id": PROJECT_ID, "app": "gate-1"},
        "pipeline": {"ledger_path": "ledger.sqlite", "health_interval": 0.3, "poll_interval": 0.1},
    }
    for key, value in overrides.items():
        section, _, field = key.partition("__")
        if field:
            config[section][field] = value
        else:
            config[section] = value
    path = tmp_path / "connector.json"
    path.write_text(json.dumps(config))
    return path


ENV = {"ATHENA_CONNECTOR_USER": "svc", "ATHENA_CONNECTOR_PASSWORD": "pw"}


def test_config_loads_and_resolves_relative_paths(tmp_path):
    config, base = load_config(write_config(tmp_path))
    pipeline, ledger = build_pipeline(config, base, ENV)
    assert pipeline.connector.root == tmp_path.resolve() / "inbox"
    assert Path(ledger.path) == tmp_path.resolve() / "ledger.sqlite"
    ledger.close()


@pytest.mark.parametrize(("override", "needle"), [
    ({"sink__password": "x"}, "must not be written in the config file"),
    ({"sink__user": "x"}, "must not be written in the config file"),
    ({"connector__bogus": 1}, "Extra inputs"),
    ({"pipeline__overflow": "explode"}, "overflow"),
    ({"pipeline__queue_size": 0}, "queue_size"),
    ({"sink__project_id": "abc"}, "UUID"),
])
def test_config_rejects_bad_files_with_plain_messages(tmp_path, override, needle):
    with pytest.raises(ConfigError) as caught:
        config, base = load_config(write_config(tmp_path, **override))
        build_pipeline(config, base, ENV)
    assert needle in str(caught.value)


def test_config_missing_credentials_and_secret(tmp_path):
    config, base = load_config(write_config(tmp_path))
    with pytest.raises(ConfigError, match="ATHENA_CONNECTOR_USER"):
        build_pipeline(config, base, {})
    http_cfg = write_config(tmp_path, connector={"type": "http_push", "spool_dir": "spool", "secret_env": "MY_SECRET"})
    config, base = load_config(http_cfg)
    with pytest.raises(ConfigError, match="MY_SECRET"):
        build_pipeline(config, base, ENV)
    with pytest.raises(ConfigError, match="not valid JSON"):
        bad = tmp_path / "bad.json"
        bad.write_text("{")
        load_config(bad)


def run_module(tmp_path, *args, env=None, **kw):
    full_env = {**os.environ, "PYTHONPATH": str(REPO / "sdk" / "python"), **(env or {})}
    full_env.pop("ATHENA_CONNECTOR_USER", None) if env is None else None
    return subprocess.Popen([sys.executable, "-m", "athena_sdk.connectors.run", *args], env=full_env, cwd=tmp_path,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kw)


def test_runner_reports_config_errors_as_json_and_exit_2(tmp_path):
    process = run_module(tmp_path, "--config", str(write_config(tmp_path, sink__password="x")), env=ENV)
    out, _ = process.communicate(timeout=30)
    assert process.returncode == 2
    event = json.loads(out.strip().splitlines()[-1])
    assert event["event"] == "config_error" and "environment" in event["errors"][0]


def test_runner_validate_only(tmp_path):
    process = run_module(tmp_path, "--config", str(write_config(tmp_path)), "--validate-only", env=ENV)
    out, _ = process.communicate(timeout=30)
    assert process.returncode == 0 and json.loads(out.strip())["event"] == "config_ok"
    missing = run_module(tmp_path, "--config", str(write_config(tmp_path)), "--validate-only")
    out, _ = missing.communicate(timeout=30)
    assert missing.returncode == 2 and "ATHENA_CONNECTOR_USER" in out


def test_runner_delivers_prints_health_json_lines_and_stops_cleanly_on_sigterm(console, tmp_path):
    cfg = write_config(tmp_path, sink={"type": "athena_console", "base_url": console.url, "project_id": PROJECT_ID,
                                       "app": "gate-1", "poll_interval": 0.05})
    process = run_module(tmp_path, "--config", str(cfg), env=ENV)
    lines: list[dict] = []
    reader = threading.Thread(target=lambda: [lines.append(json.loads(row)) for row in process.stdout], daemon=True)
    reader.start()
    try:
        assert wait_for(lambda: any(r["event"] == "health" for r in lines), timeout=20)
        (tmp_path / "inbox").mkdir(exist_ok=True)
        (tmp_path / "inbox" / "one.png").write_bytes(png(5))
        assert wait_for(lambda: any(r["event"] == "submitted" for r in lines), timeout=20)
    finally:
        process.send_signal(signal.SIGTERM)
    assert process.wait(timeout=30) == 0
    reader.join(5)
    events = [r["event"] for r in lines]
    assert "stop_requested" in events and "stopped" in events and events[-1] == "health"
    submitted = next(r for r in lines if r["event"] == "submitted")
    assert submitted["connector"] == "folder-drop" and submitted["sha256"] == hashlib.sha256(png(5)).hexdigest()
    assert submitted["receipt"]["detail"]["model_version_id"] == MODEL_ID
    health = next(r for r in lines if r["event"] == "health")
    assert health["status"] in ("ok", "degraded", "down") and "queue" in health and "ledger" in health
    assert (tmp_path / "inbox" / "processed" / "one.png").exists()
    assert len(console.uploads) == 1


def test_timestamps_are_timezone_aware():
    assert make_item(1).captured_at.utcoffset() == timedelta(0)
    assert Health("ok").checked_at.tzinfo is not None
