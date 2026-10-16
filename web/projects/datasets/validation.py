"""Pre-export checks. Nothing is coerced: a problem blocks the version and is reported."""

from __future__ import annotations

import hashlib
import os
import threading
from collections import OrderedDict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from django.core.exceptions import SuspiciousOperation
from django.core.files.storage import default_storage

CHUNK = 1024 * 1024
CACHE_MAX_ENTRIES = 100_000
ONE = Decimal(1)


@dataclass(frozen=True)
class Problem:
    code: str
    message: str
    asset_id: str | None = None
    annotation_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "asset_id": self.asset_id,
            "annotation_id": self.annotation_id,
        }


def _decimal(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def geometry_problem(box: Any) -> Problem | None:
    """Return a problem if the normalised box is not strictly inside the image."""
    values = [_decimal(getattr(box, name)) for name in ("x", "y", "width", "height")]
    if any(value is None for value in values):
        return Problem("geometry_out_of_range", "Box coordinates are not finite numbers.")
    x, y, width, height = values
    if width <= 0 or height <= 0:
        return Problem("non_positive_size", "Box width and height must be greater than zero.")
    if x < 0 or y < 0 or x + width > ONE or y + height > ONE:
        return Problem("geometry_out_of_range", "Box extends outside the image (coordinates must stay within 0–1).")
    return None


def label_problem(label: str, project_labels: list[str]) -> Problem | None:
    """Labels must match the project's list exactly, including case."""
    if isinstance(label, str) and label in project_labels:
        return None
    return Problem(
        "label_not_in_project",
        f"Label {label!r} is not in this project's label list (matching is case-sensitive).",
    )


def unsafe_label_problems(project_labels: list[str]) -> list[Problem]:
    return [
        Problem(
            "label_unsupported_characters",
            f"Label {label!r} contains line breaks or control characters, which would corrupt class files.",
        )
        for label in project_labels
        if not isinstance(label, str) or not label.isprintable()
    ]


def storage_sha256(storage_key: str) -> str | None:
    """SHA-256 of a stored file, or ``None`` when it does not exist / cannot be opened."""
    try:
        with default_storage.open(storage_key, "rb") as handle:
            digest = hashlib.sha256()
            for chunk in iter(lambda: handle.read(CHUNK), b""):
                digest.update(chunk)
            return digest.hexdigest()
    except (FileNotFoundError, IsADirectoryError, PermissionError, SuspiciousOperation, ValueError):
        return None


# --- Verification cache (readiness only) -------------------------------------------------------
#
# ``GET .../datasets/readiness`` is called repeatedly by the console and used to re-hash every
# image every time. For readiness we remember "this file, at this size and modification time,
# hashed to X" and only ``stat()`` on repeat calls.
#
# Trade-off, stated plainly: a stat-keyed cache can be fooled by someone who rewrites a file to
# the *same size* and then restores its mtime (``os.utime``). So the cache is advisory and used
# for readiness ONLY. Creating a dataset version and exporting one never read or write this
# cache: they always hash every byte (``file_problem(..., cached=False)``, the default), so a
# tampered file can never be frozen into a version or leave in an export. Keying on ctime as well
# would close most of that gap but also invalidates on harmless chmod/hardlink changes; we
# keep to (storage_key, size, mtime_ns). Storage backends without a local path are not cached.
_VERIFIED: OrderedDict[tuple[str, int, int], str] = OrderedDict()
_VERIFIED_LOCK = threading.Lock()


def clear_verification_cache() -> None:
    with _VERIFIED_LOCK:
        _VERIFIED.clear()


def _stat_key(storage_key: str) -> tuple[str, int, int] | None:
    try:
        info = os.stat(default_storage.path(storage_key))
    except (NotImplementedError, SuspiciousOperation, ValueError, OSError):
        return None
    return (storage_key, info.st_size, info.st_mtime_ns)


def _cache_get(key: tuple[str, int, int]) -> str | None:
    with _VERIFIED_LOCK:
        value = _VERIFIED.get(key)
        if value is not None:
            _VERIFIED.move_to_end(key)
        return value


def _cache_put(key: tuple[str, int, int], digest: str) -> None:
    with _VERIFIED_LOCK:
        _VERIFIED[key] = digest
        _VERIFIED.move_to_end(key)
        while len(_VERIFIED) > CACHE_MAX_ENTRIES:
            _VERIFIED.popitem(last=False)


def file_problem(storage_key: str, expected_sha256: str, asset_id: str, *, cached: bool = False) -> Problem | None:
    """Check a stored file against its recorded SHA-256.

    ``cached=True`` (readiness only) may answer from the stat-keyed cache. The stat is taken
    *before* hashing, so a file that changes mid-hash is stored under its old key and misses
    on the next call.
    """
    key = _stat_key(storage_key) if cached else None
    actual = _cache_get(key) if key else None
    if actual is None:
        actual = storage_sha256(storage_key)
        if actual is not None and key:
            _cache_put(key, actual)
    if actual is None:
        return Problem("file_missing", "The image file is missing from storage.", asset_id)
    if actual != expected_sha256:
        return Problem(
            "hash_mismatch",
            "The image file no longer matches the checksum recorded when it was uploaded.",
            asset_id,
        )
    return None
