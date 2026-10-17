"""Deterministic, leak-free train/val/test assignment.

The bucket depends only on ``seed`` and the image's SHA-256, never on the asset id,
filename or upload order. Byte-identical images therefore always share a split.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

SPLIT_NAMES = ("train", "val", "test")
# Recorded in the split_config of every new version. Versions saved before this field existed
# carry no name; they are read as LEGACY_ALGORITHM and their stored rows are never recomputed.
ALGORITHM = "hash-bucket-min1-v2"
LEGACY_ALGORITHM = "hash-bucket-v1"
MAX_SEED = 2**31 - 1
SUM_TOLERANCE = 1e-6


def _fraction(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"split.{name} must be a number between 0 and 1.")
    value = float(value)
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"split.{name} must be a number between 0 and 1.")
    return value


@dataclass(frozen=True)
class SplitConfig:
    seed: int
    train: float
    val: float
    test: float

    @classmethod
    def default(cls) -> "SplitConfig":
        return cls(seed=0, train=0.8, val=0.1, test=0.1)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "SplitConfig":
        if payload.keys() - {"seed", "split"}:
            raise ValueError("Request contains an unsupported field.")
        default = cls.default()
        seed = payload.get("seed", default.seed)
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= MAX_SEED:
            raise ValueError(f"seed must be a whole number between 0 and {MAX_SEED}.")
        split = payload.get("split")
        if split is None:
            return cls(seed, default.train, default.val, default.test)
        if not isinstance(split, dict) or set(split) != set(SPLIT_NAMES):
            raise ValueError("split must give train, val and test fractions.")
        fractions = {name: _fraction(split[name], name) for name in SPLIT_NAMES}
        if abs(sum(fractions.values()) - 1) > SUM_TOLERANCE:
            raise ValueError("split fractions must add up to 1.")
        return cls(seed, **fractions)

    def as_dict(self) -> dict[str, Any]:
        return {"seed": self.seed, "train": self.train, "val": self.val, "test": self.test}


def algorithm_of(split_config: dict[str, Any] | None) -> str:
    """Name of the split algorithm a stored version used (old versions have no field)."""
    name = (split_config or {}).get("split_algorithm")
    return name if isinstance(name, str) and name else LEGACY_ALGORITHM


def shortfall_notes(distinct_pictures: int, config: SplitConfig) -> list[str]:
    """Plain-language notice when too few different pictures exist to fill every requested group."""
    wanted = sum(1 for name in SPLIT_NAMES if getattr(config, name) > 0)
    if distinct_pictures >= wanted:
        return []
    return [
        f"Only {distinct_pictures} different {'picture has' if distinct_pictures == 1 else 'pictures have'} "
        f"accepted boxes, and the {wanted} groups you asked for (Training, Checking, Final test) need at least "
        f"{wanted}, so at least one group is empty. Add more pictures with boxes."
    ]


def bucket(sha256: str, seed: int) -> float:
    digest = hashlib.sha256(f"{seed}:{sha256}".encode("ascii", "replace")).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def assign_split(sha256: str, config: SplitConfig) -> str:
    position = bucket(sha256, config.seed)
    active = [(name, getattr(config, name)) for name in SPLIT_NAMES if getattr(config, name) > 0]
    cumulative = 0.0
    for name, fraction in active:
        cumulative += fraction
        if position < cumulative:
            return name
    return active[-1][0]  # floating point remainder goes to the last non-empty split


def assign_splits(shas: Iterable[str], config: SplitConfig) -> dict[str, str]:
    """Assign every distinct image hash to a split (algorithm ``ALGORITHM``).

    Guarantee: with at least as many distinct hashes as requested splits (3 for the default
    80/10/10) every requested split gets at least one picture, so there is always a Checking and
    a Final-test group from 3 different pictures on. Below that nothing is invented: some split
    stays empty and :func:`shortfall_notes` says so.

    Starts from the per-image hash assignment (:func:`assign_split`). With few pictures the
    hash can leave a split that the operator asked for (fraction > 0) with nothing in it, e.g.
    a 10% final-test share of 15 pictures. Then one whole hash-group (so byte-identical
    duplicates still move together and never straddle splits) is moved from the fullest split
    into the empty one, picking the group with the lowest bucket value so the choice is
    deterministic. This needs at least as many distinct pictures as requested splits; below
    that some splits stay empty and the manifest warns about it. Unlike the plain hash rule,
    the result for one picture can therefore depend on which other pictures are present, but
    only in the case where a split would otherwise be empty.
    """
    unique = sorted(set(shas))
    result = {sha: assign_split(sha, config) for sha in unique}
    active = [name for name in SPLIT_NAMES if getattr(config, name) > 0]
    if len(unique) < len(active):
        return result
    for target in reversed(active):  # fill test, then val, so train stays the biggest donor
        members = {name: [sha for sha in unique if result[sha] == name] for name in active}
        if members[target]:
            continue
        donor = max(active, key=lambda name: (len(members[name]), name == "train"))
        if len(members[donor]) < 2:
            continue
        moved = min(members[donor], key=lambda sha: (bucket(sha, config.seed), sha))
        result[moved] = target
    return result
