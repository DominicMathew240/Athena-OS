# SPDX-License-Identifier: Apache-2.0
"""A bounded in-memory queue with explicit backpressure policies and counters."""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")
POLICIES = ("block", "drop_oldest", "reject_new")


@dataclass(frozen=True)
class PutResult(Generic[T]):
    accepted: bool
    dropped: T | None = None  # the oldest entry evicted to make room (drop_oldest only)
    waited: bool = False


class BoundedQueue(Generic[T]):
    """``block``: put waits for space (pushes back on the producer).
    ``drop_oldest``: put never waits; the oldest queued entry is evicted and returned.
    ``reject_new``: put never waits; the new entry is refused.

    Counters are plain integers readable through :meth:`stats`.
    """

    def __init__(self, maxsize: int, policy: str = "block") -> None:
        if maxsize < 1:
            raise ValueError("maxsize must be at least 1.")
        if policy not in POLICIES:
            raise ValueError(f"policy must be one of {', '.join(POLICIES)}.")
        self.maxsize = maxsize
        self.policy = policy
        self._items: deque[T] = deque()
        self._lock = threading.Lock()
        self._not_full = threading.Condition(self._lock)
        self._not_empty = threading.Condition(self._lock)
        self._closed = False
        self._counters = {"enqueued": 0, "dequeued": 0, "dropped": 0, "rejected": 0, "blocked_puts": 0, "high_watermark": 0}

    def put(self, item: T, timeout: float | None = None) -> PutResult[T]:
        with self._not_full:
            if self._closed:
                return PutResult(False)
            waited = False
            if len(self._items) >= self.maxsize:
                if self.policy == "reject_new":
                    self._counters["rejected"] += 1
                    return PutResult(False)
                if self.policy == "drop_oldest":
                    evicted = self._items.popleft()
                    self._counters["dropped"] += 1
                    self._items.append(item)
                    self._counters["enqueued"] += 1
                    self._not_empty.notify()
                    return PutResult(True, dropped=evicted)
                waited = True
                self._counters["blocked_puts"] += 1
                if not self._not_full.wait_for(lambda: self._closed or len(self._items) < self.maxsize, timeout):
                    self._counters["rejected"] += 1
                    return PutResult(False, waited=True)
                if self._closed:
                    return PutResult(False, waited=True)
            self._items.append(item)
            self._counters["enqueued"] += 1
            self._counters["high_watermark"] = max(self._counters["high_watermark"], len(self._items))
            self._not_empty.notify()
            return PutResult(True, waited=waited)

    def get(self, timeout: float | None = None) -> T | None:
        """Next entry, or ``None`` on timeout / when closed and empty."""
        with self._not_empty:
            self._not_empty.wait_for(lambda: self._items or self._closed, timeout)
            if not self._items:
                return None
            item = self._items.popleft()
            self._counters["dequeued"] += 1
            self._not_full.notify()
            return item

    def drain(self) -> list[T]:
        """Remove and return everything still queued (used at shutdown)."""
        with self._lock:
            items = list(self._items)
            self._items.clear()
            self._not_full.notify_all()
            return items

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._not_full.notify_all()
            self._not_empty.notify_all()

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def stats(self) -> dict[str, int | str]:
        with self._lock:
            return {"policy": self.policy, "maxsize": self.maxsize, "depth": len(self._items), **self._counters}
