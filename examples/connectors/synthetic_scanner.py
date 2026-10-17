# SPDX-License-Identifier: Apache-2.0
"""A third-party-style connector written only against the public `athena_sdk.connectors` API.

``SyntheticScannerConnector`` pretends to be a device: it "captures" small generated PNG
images on request. It exists so a contributor can see the smallest complete
`DataSourceConnector` and run it end to end with no hardware. The images are synthetic
gradients - they are not scanner output, no real scanner protocol is involved, and nothing
here says anything about how a real device behaves.

Run it against a printing sink (no console needed)::

    PYTHONPATH=sdk/python python -m examples.connectors.synthetic_scanner
"""

from __future__ import annotations

import struct
import threading
import zlib
from datetime import datetime, timezone

from athena_sdk.connectors import (
    DataSourceConnector,
    Health,
    Ledger,
    Outcome,
    Pipeline,
    Receipt,
    Sink,
    SourceItem,
)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def synthetic_png(seed: int, size: int = 16) -> bytes:
    """A valid size x size RGB gradient PNG. Deterministic for a given seed."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    rows = b"".join(
        b"\x00" + b"".join(bytes(((seed * 37 + x * 9) % 256, (seed * 11 + y * 13) % 256, (x + y) * 4 % 256))
                           for x in range(size))
        for y in range(size)
    )
    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return PNG_MAGIC + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


class SyntheticScannerConnector(DataSourceConnector):
    name = "synthetic-scanner"
    version = "0.1.0"

    def __init__(self, count: int = 5, device_id: str = "synthetic-01") -> None:
        self.device_id = device_id
        self._remaining = list(range(count))
        self._inflight: dict[str, int] = {}
        self._lock = threading.Lock()
        self._last_ok: datetime | None = None

    def health(self) -> Health:
        return Health("ok", f"{len(self._remaining)} synthetic captures left", self._last_ok)

    def poll(self, max_items: int = 1) -> list[SourceItem]:
        with self._lock:
            taken, self._remaining = self._remaining[:max_items], self._remaining[max_items:]
        items = []
        for seq in taken:
            payload = synthetic_png(seq)
            item = SourceItem(
                payload=payload,
                content_type="image/png",
                source_id=f"{self.device_id}:capture-{seq:04d}",
                captured_at=datetime.now(timezone.utc),
                # Stable per capture: if the same capture is offered again it carries the same key.
                idempotency_key=f"{self.device_id}:{seq}:{len(payload)}",
                metadata={"device": self.device_id, "synthetic": "yes"},
            )
            with self._lock:
                self._inflight[item.idempotency_key] = seq
            items.append(item)
        self._last_ok = datetime.now(timezone.utc)
        return items

    def ack(self, item: SourceItem, outcome: Outcome) -> None:
        with self._lock:
            self._inflight.pop(item.idempotency_key, None)

    def nack(self, item: SourceItem, reason: str, *, permanent: bool = False) -> None:
        with self._lock:
            seq = self._inflight.pop(item.idempotency_key, None)
            if seq is not None and not permanent:
                self._remaining.append(seq)  # a real device would be re-read or re-requested here


class PrintSink(Sink):
    """Prints what it would send. Stands in for the console so the example needs nothing running."""

    name = "print"

    def submit(self, item: SourceItem) -> Receipt:
        print(f"would submit {item.source_id} sha256={item.sha256[:12]}… via {item.connector}/{item.connector_version}")
        return Receipt(self.name, item.sha256[:12], "printed", {"sha256_match": True})


def main() -> None:
    connector = SyntheticScannerConnector(count=5)
    ledger = Ledger(":memory:")
    pipeline = Pipeline(connector, PrintSink(), ledger, poll_interval=0.05)
    pipeline.start()
    pipeline.wait_until_idle(5)
    while ledger.counts()["submitted"] < 5:
        threading.Event().wait(0.05)
    pipeline.stop()
    print("ledger:", ledger.counts())


if __name__ == "__main__":
    main()
