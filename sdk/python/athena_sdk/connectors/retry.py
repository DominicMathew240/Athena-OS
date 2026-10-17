# SPDX-License-Identifier: Apache-2.0
"""Retry policy: exponential backoff with jitter."""

from __future__ import annotations

import random
from dataclasses import dataclass

JITTER_MODES = ("full", "equal", "none")


@dataclass(frozen=True)
class RetryPolicy:
    """``delay(n)`` is the wait after the n-th failed attempt (n starts at 1).

    The ceiling grows as ``base_delay * multiplier**(n-1)`` up to ``max_delay``.
    ``full`` jitter draws uniformly from ``[0, ceiling]`` (best at spreading load),
    ``equal`` from ``[ceiling/2, ceiling]``, ``none`` uses the ceiling.
    ``max_attempts`` counts the first try, so 5 means one try plus four retries.
    """

    max_attempts: int = 5
    base_delay: float = 0.5
    max_delay: float = 30.0
    multiplier: float = 2.0
    jitter: str = "full"

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1.")
        if self.base_delay < 0 or self.max_delay < self.base_delay:
            raise ValueError("Need 0 <= base_delay <= max_delay.")
        if self.multiplier < 1:
            raise ValueError("multiplier must be at least 1.")
        if self.jitter not in JITTER_MODES:
            raise ValueError(f"jitter must be one of {', '.join(JITTER_MODES)}.")

    def ceiling(self, attempt: int) -> float:
        exponent = max(attempt - 1, 0)
        try:
            raw = self.base_delay * (self.multiplier**exponent)
        except OverflowError:
            raw = self.max_delay
        return min(raw, self.max_delay)

    def delay(self, attempt: int, rng: random.Random | None = None, *, floor: float | None = None) -> float:
        """Seconds to wait after failed attempt ``attempt``; ``floor`` is a server's Retry-After."""
        rng = rng or random
        top = self.ceiling(attempt)
        if self.jitter == "full":
            value = rng.uniform(0, top)
        elif self.jitter == "equal":
            value = rng.uniform(top / 2, top)
        else:
            value = top
        if floor is not None:
            value = max(value, min(floor, 3600.0))
        return value
