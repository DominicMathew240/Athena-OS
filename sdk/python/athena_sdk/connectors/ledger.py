# SPDX-License-Identifier: Apache-2.0
"""Durable submission ledger (sqlite) providing idempotency and provenance.

One row per idempotency key. The row is written *before* the first delivery attempt and
updated after, so a crash at any point leaves enough to recover without losing track of
where an item came from:

``received`` -> ``submitting`` -> ``submitted``   (or ``rejected`` for a permanent refusal)

A row left in ``submitting`` means the process died during a delivery: the outcome is
*in doubt*, and the pipeline asks the sink (``Sink.find_existing``) before sending again.
Delivery is therefore at-least-once with duplicate suppression, not exactly-once magic.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from athena_sdk.connectors.base import Receipt, SourceItem

RECEIVED, SUBMITTING, SUBMITTED, REJECTED = "received", "submitting", "submitted", "rejected"
STATUSES = (RECEIVED, SUBMITTING, SUBMITTED, REJECTED)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    idempotency_key   TEXT PRIMARY KEY,
    source_id         TEXT NOT NULL,
    sha256            TEXT NOT NULL,
    size_bytes        INTEGER NOT NULL,
    content_type      TEXT NOT NULL,
    connector         TEXT NOT NULL,
    connector_version TEXT NOT NULL,
    captured_at       TEXT NOT NULL,
    received_at       TEXT NOT NULL,
    submitted_at      TEXT,
    status            TEXT NOT NULL,
    attempts          INTEGER NOT NULL DEFAULT 0,
    receipt           TEXT,
    last_error        TEXT
);
CREATE INDEX IF NOT EXISTS items_status ON items(status);
CREATE INDEX IF NOT EXISTS items_sha ON items(sha256);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class LedgerEntry:
    idempotency_key: str
    source_id: str
    sha256: str
    size_bytes: int
    content_type: str
    connector: str
    connector_version: str
    captured_at: str
    received_at: str
    submitted_at: str | None
    status: str
    attempts: int
    receipt: Receipt | None
    last_error: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "idempotency_key": self.idempotency_key,
            "source_id": self.source_id,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "content_type": self.content_type,
            "connector": self.connector,
            "connector_version": self.connector_version,
            "captured_at": self.captured_at,
            "received_at": self.received_at,
            "submitted_at": self.submitted_at,
            "status": self.status,
            "attempts": self.attempts,
            "receipt": self.receipt.to_dict() if self.receipt else None,
            "last_error": self.last_error,
        }


class LedgerConflict(Exception):
    """The same idempotency key was offered with different content."""


class Ledger:
    """Thread-safe sqlite ledger. Pass ``":memory:"`` only in tests; use a real file otherwise."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        if self.path != ":memory:":
            self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")  # a receipt must survive power loss
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- reads ---------------------------------------------------------------------------
    @staticmethod
    def _entry(row: sqlite3.Row | None) -> LedgerEntry | None:
        if row is None:
            return None
        receipt = Receipt.from_dict(json.loads(row["receipt"])) if row["receipt"] else None
        return LedgerEntry(
            idempotency_key=row["idempotency_key"], source_id=row["source_id"], sha256=row["sha256"],
            size_bytes=row["size_bytes"], content_type=row["content_type"], connector=row["connector"],
            connector_version=row["connector_version"], captured_at=row["captured_at"],
            received_at=row["received_at"], submitted_at=row["submitted_at"], status=row["status"],
            attempts=row["attempts"], receipt=receipt, last_error=row["last_error"],
        )

    def get(self, key: str) -> LedgerEntry | None:
        with self._lock:
            return self._entry(self._db.execute("SELECT * FROM items WHERE idempotency_key=?", (key,)).fetchone())

    def counts(self) -> dict[str, int]:
        with self._lock:
            rows = self._db.execute("SELECT status, COUNT(*) AS n FROM items GROUP BY status").fetchall()
        counts = dict.fromkeys(STATUSES, 0)
        counts.update({row["status"]: row["n"] for row in rows})
        return counts

    def entries(self, status: str | None = None) -> list[LedgerEntry]:
        with self._lock:
            if status:
                rows = self._db.execute("SELECT * FROM items WHERE status=? ORDER BY received_at", (status,))
            else:
                rows = self._db.execute("SELECT * FROM items ORDER BY received_at")
            return [self._entry(row) for row in rows.fetchall()]  # type: ignore[misc]

    # -- writes --------------------------------------------------------------------------
    def record_received(self, item: SourceItem) -> LedgerEntry:
        """Record the item the first time it is seen; return the (possibly existing) row.

        Raises :class:`LedgerConflict` if the key exists with a different SHA-256.
        """
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO items (idempotency_key, source_id, sha256, size_bytes, content_type, "
                "connector, connector_version, captured_at, received_at, status) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (item.idempotency_key, item.source_id, item.sha256, item.size_bytes, item.content_type,
                 item.connector, item.connector_version, item.captured_at.astimezone(timezone.utc).isoformat(),
                 _now(), RECEIVED),
            )
            entry = self.get(item.idempotency_key)
        assert entry is not None
        if entry.sha256 != item.sha256:
            raise LedgerConflict(
                f"idempotency key {item.idempotency_key!r} was already used for different content "
                f"({entry.sha256[:12]}… vs {item.sha256[:12]}…)"
            )
        return entry

    def begin_attempt(self, key: str) -> int:
        with self._lock:
            self._db.execute(
                "UPDATE items SET status=?, attempts=attempts+1 WHERE idempotency_key=? AND status!=?",
                (SUBMITTING, key, SUBMITTED),
            )
            entry = self.get(key)
        return entry.attempts if entry else 0

    def attempt_failed(self, key: str, error: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE items SET status=?, last_error=? WHERE idempotency_key=? AND status=?",
                (RECEIVED, error[:500], key, SUBMITTING),
            )

    def mark_submitted(self, key: str, receipt: Receipt) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE items SET status=?, submitted_at=?, receipt=?, last_error=NULL WHERE idempotency_key=?",
                (SUBMITTED, _now(), json.dumps(receipt.to_dict(), sort_keys=True), key),
            )

    def mark_rejected(self, key: str, reason: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE items SET status=?, last_error=? WHERE idempotency_key=? AND status!=?",
                (REJECTED, reason[:500], key, SUBMITTED),
            )

    def note_error(self, key: str, error: str) -> None:
        with self._lock:
            self._db.execute("UPDATE items SET last_error=? WHERE idempotency_key=?", (error[:500], key))
