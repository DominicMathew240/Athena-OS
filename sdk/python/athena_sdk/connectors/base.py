# SPDX-License-Identifier: Apache-2.0
"""Vendor-neutral contracts for scanner / operational-data-source connectors.

A *connector* moves bytes from some source (a folder a scanner writes into, an HTTP
push, a future vendor protocol) towards a *sink* (normally the Athena console). The
contract is deliberately small and says nothing about any vendor protocol: no real
scanner has been selected or tested, and none is implied by this module.

Two rules are built into the types so a connector cannot break them by accident:

* **Provenance travels with the bytes.** Every :class:`SourceItem` carries its
  SHA-256, ``source_id`` and an idempotency key; the pipeline stamps the connector
  name and version before anything is submitted.
* **A connector never decides outcomes.** An item has no verdict, label or decision
  field and metadata keys that look like one are refused. Whether an image is
  acceptable is decided by a person in the console, never here.
"""

from __future__ import annotations

import hashlib
import re
import threading
from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any

ACCEPTED_CONTENT_TYPES = ("image/jpeg", "image/png", "image/webp")
MAX_ID_LENGTH = 256
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
# Metadata that would smuggle a verdict past the human reviewer is refused outright.
FORBIDDEN_METADATA_KEYS = frozenset(
    {"decision", "outcome", "verdict", "label", "labels", "result", "prediction", "approved",
     "feedback", "review", "status"}
)


class ConnectorError(Exception):
    """Base class for connector-side failures."""


class ItemError(ConnectorError, ValueError):
    """A :class:`SourceItem` violates the contract."""


class Outcome(str, Enum):
    """What happened to an item that a connector is told about through :meth:`ack`.

    These describe *delivery*, never the meaning of the image.
    """

    SUBMITTED = "submitted"  # the sink accepted the bytes and returned a receipt
    DUPLICATE = "duplicate"  # the ledger already holds a receipt for this idempotency key


def _clean_id(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ItemError(f"{field_name} must be a non-empty string.")
    if len(value) > MAX_ID_LENGTH or _CONTROL.search(value):
        raise ItemError(f"{field_name} must be at most {MAX_ID_LENGTH} printable characters.")
    return value


@dataclass(frozen=True)
class SourceItem:
    """One piece of captured data and the facts needed to trace it.

    ``idempotency_key`` must be stable: the same capture offered twice (after a restart,
    a retry, a re-scan) must carry the same key, and different captures must differ.
    """

    payload: bytes
    content_type: str
    source_id: str
    captured_at: datetime
    idempotency_key: str
    metadata: Mapping[str, str] = field(default_factory=dict)
    connector: str = ""  # stamped by the pipeline from the connector's name/version
    connector_version: str = ""
    sha256: str = field(init=False, default="")

    def __post_init__(self) -> None:
        if not isinstance(self.payload, (bytes, bytearray)) or not self.payload:
            raise ItemError("payload must be non-empty bytes.")
        object.__setattr__(self, "payload", bytes(self.payload))
        if self.content_type not in ACCEPTED_CONTENT_TYPES:
            raise ItemError(f"content_type must be one of {', '.join(ACCEPTED_CONTENT_TYPES)}.")
        _clean_id(self.source_id, "source_id")
        _clean_id(self.idempotency_key, "idempotency_key")
        if not isinstance(self.captured_at, datetime) or self.captured_at.tzinfo is None:
            raise ItemError("captured_at must be a timezone-aware datetime.")
        meta = dict(self.metadata)
        for key, value in meta.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise ItemError("metadata must map strings to strings.")
            if key.strip().lower() in FORBIDDEN_METADATA_KEYS:
                raise ItemError(
                    f"metadata key {key!r} is reserved: a connector never supplies outcomes; "
                    "people decide in the console."
                )
        object.__setattr__(self, "metadata", MappingProxyType(meta))
        object.__setattr__(self, "sha256", hashlib.sha256(self.payload).hexdigest())

    @property
    def size_bytes(self) -> int:
        return len(self.payload)

    def provenance(self) -> dict[str, Any]:
        """The facts that must accompany every submission of this item."""
        return {
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "content_type": self.content_type,
            "source_id": self.source_id,
            "idempotency_key": self.idempotency_key,
            "captured_at": self.captured_at.astimezone(timezone.utc).isoformat(),
            "connector": self.connector,
            "connector_version": self.connector_version,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class Health:
    """A connector's (or sink's) own view of whether it is working."""

    status: str  # "ok" | "degraded" | "down"
    detail: str = ""
    last_success_at: datetime | None = None
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    STATUSES = ("ok", "degraded", "down")

    def __post_init__(self) -> None:
        if self.status not in self.STATUSES:
            raise ValueError(f"status must be one of {', '.join(self.STATUSES)}.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "detail": self.detail,
            "last_success_at": self.last_success_at.isoformat() if self.last_success_at else None,
            "checked_at": self.checked_at.isoformat(),
        }


@dataclass(frozen=True)
class Receipt:
    """Proof from a sink that it took custody of an item.

    ``remote_id`` is the sink's own identifier (the console's observation id).
    ``detail`` is free-form sink data (model version id, observation status, ...). A
    receipt says the bytes were delivered; it says nothing about what the image means.
    """

    sink: str
    remote_id: str
    status: str
    detail: Mapping[str, Any] = field(default_factory=dict)
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "sink": self.sink,
            "remote_id": self.remote_id,
            "status": self.status,
            "detail": dict(self.detail),
            "received_at": self.received_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Receipt:
        return cls(
            sink=str(data["sink"]),
            remote_id=str(data["remote_id"]),
            status=str(data["status"]),
            detail=dict(data.get("detail", {})),
            received_at=datetime.fromisoformat(str(data["received_at"])),
        )


class SinkError(ConnectorError):
    """A sink could not take an item. ``retry_after`` (seconds) is a floor for the next delay."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class TransientSinkError(SinkError):
    """Worth retrying with backoff (network, 429, 5xx, a restarting console)."""


class SinkUnavailableError(TransientSinkError):
    """The sink itself is unusable (bad credentials, missing role, app archived).

    The *item* is fine, so it is never rejected for this; the pipeline waits at the
    maximum backoff and reports health ``down`` until an operator fixes the sink.
    """


class PermanentItemError(SinkError):
    """The sink refused this specific item and always will (not an image, too large)."""


class Sink(ABC):
    """Where items go. Implementations must be safe to call from one worker thread."""

    name: str = ""

    @abstractmethod
    def submit(self, item: SourceItem) -> Receipt:
        """Deliver ``item`` and return a receipt.

        Raise :class:`TransientSinkError`, :class:`SinkUnavailableError` or
        :class:`PermanentItemError`. Never decide what the item means.
        """

    def find_existing(self, item: SourceItem) -> Receipt | None:
        """Optional: after a crash in the middle of ``submit``, report a delivery that already
        happened so the item is not delivered twice. Return ``None`` when unknown."""
        return None

    def health(self) -> Health:
        return Health("ok", "no health probe implemented")

    def close(self) -> None:  # noqa: B027 - optional hook
        """Release resources."""


class DataSourceConnector(ABC):
    """Where items come from.

    Subclasses set ``name`` and ``version`` and implement :meth:`health`, :meth:`poll`,
    :meth:`ack` and :meth:`nack`. The pipeline calls ``poll`` (via :meth:`stream`) from one
    thread and ``ack``/``nack`` from another, so shared state needs a lock.
    """

    name: str = ""
    version: str = ""

    def open(self) -> None:  # noqa: B027 - optional hook
        """Acquire resources (sockets, directories). Called once before polling."""

    def close(self) -> None:  # noqa: B027 - optional hook
        """Release resources. Must be safe to call twice."""

    @abstractmethod
    def health(self) -> Health:
        """Cheap, non-blocking self-check. Never raise; report ``down`` instead."""

    @abstractmethod
    def poll(self, max_items: int = 1) -> list[SourceItem]:
        """Return up to ``max_items`` items that are ready now (empty list when idle).

        Must not return an item that is still in flight (offered, not yet acked/nacked),
        and must be non-blocking apart from short I/O.
        """

    def stream(self, stop: threading.Event, interval: float = 1.0) -> Iterator[SourceItem]:
        """Yield items until ``stop`` is set. The default loops over :meth:`poll`."""
        while not stop.is_set():
            batch = self.poll(max_items=1)
            if not batch:
                stop.wait(interval)
                continue
            yield from batch

    @abstractmethod
    def ack(self, item: SourceItem, outcome: Outcome) -> None:
        """The item is safely in the sink's custody (or already was): release it at the source."""

    @abstractmethod
    def nack(self, item: SourceItem, reason: str, *, permanent: bool = False) -> None:
        """The item was not delivered. ``permanent`` means it never will be (quarantine it);
        otherwise keep it and offer it again later."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "ACCEPTED_CONTENT_TYPES",
    "FORBIDDEN_METADATA_KEYS",
    "ConnectorError",
    "DataSourceConnector",
    "Health",
    "ItemError",
    "Outcome",
    "PermanentItemError",
    "Receipt",
    "Sink",
    "SinkError",
    "SinkUnavailableError",
    "SourceItem",
    "TransientSinkError",
    "utcnow",
]
