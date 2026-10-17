# SPDX-License-Identifier: Apache-2.0
"""FolderDropConnector: a reference connector that watches one directory.

Reference implementation only. It exists to prove the connector contract and to give a
scanner whose software can "save to a folder" a path to the console; no real scanner has
been tested with it.

Behaviour
---------
* Scans the top level of ``root`` (never recurses) for ``.jpg/.jpeg/.png/.webp`` files.
* A file is offered only after its size and mtime have been unchanged for
  ``stable_seconds`` (so half-written files are never read), and it must look complete.
* Success -> moved to ``processed/``. Permanently bad (wrong type, too large, symlink,
  refused by the console) -> moved to ``rejected/`` with ``<name>.reason.json`` beside it.
  Temporary failure -> left in place and offered again after ``retry_cooldown`` seconds.
* Safety: symlinks, hard-linked files and non-regular files are never read; files are
  opened with ``O_NOFOLLOW`` where the OS has it and re-checked with ``fstat`` after opening;
  only plain base names are ever used for destinations.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import stat
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from athena_sdk.connectors import imagecheck
from athena_sdk.connectors.base import DataSourceConnector, Health, Outcome, SourceItem, utcnow

DEFAULT_MAX_BYTES = 20 * 1024 * 1024  # the console's own per-image limit
IGNORED_SUFFIXES = (".part", ".tmp", ".crdownload", ".partial", ".swp")
ALLOWED_EXTENSIONS = tuple(ext for exts in imagecheck.EXTENSIONS.values() for ext in exts)


class FolderDropConnector(DataSourceConnector):
    name = "folder-drop"
    version = "0.1.0"

    def __init__(
        self,
        root: str | Path,
        *,
        source_label: str = "folder",
        stable_seconds: float = 2.0,
        max_bytes: int = DEFAULT_MAX_BYTES,
        retry_cooldown: float = 30.0,
        reject_hardlinks: bool = True,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.root = Path(root)
        self.processed_dir = self.root / "processed"
        self.rejected_dir = self.root / "rejected"
        self.source_label = source_label
        self.stable_seconds = stable_seconds
        self.max_bytes = max_bytes
        self.retry_cooldown = retry_cooldown
        self.reject_hardlinks = reject_hardlinks
        self._clock = clock
        self._lock = threading.Lock()
        self._seen: dict[str, tuple[int, int, float]] = {}  # name -> (size, mtime_ns, first time at this state)
        self._inflight: dict[str, tuple[str, int, int, int]] = {}  # key -> (name, size, mtime_ns, inode)
        self._cooldown: dict[str, float] = {}  # name -> monotonic time before which not to re-offer
        self._last_scan_ok: datetime | None = None
        self._last_error = ""
        self.counters = {"offered": 0, "rejected_at_edge": 0, "processed": 0, "rejected_after_sink": 0}

    # -- lifecycle -----------------------------------------------------------------------
    def open(self) -> None:
        for directory in (self.root, self.processed_dir, self.rejected_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def health(self) -> Health:
        if not self.root.is_dir():
            return Health("down", f"watch directory {self.root} does not exist", self._last_scan_ok)
        problems = [
            f"{d.name}/ is not writable" for d in (self.root, self.processed_dir, self.rejected_dir)
            if not (d.is_dir() and os.access(d, os.W_OK | os.R_OK | os.X_OK))
        ]
        if problems:
            return Health("down", "; ".join(problems), self._last_scan_ok)
        if self._last_error:
            return Health("degraded", self._last_error, self._last_scan_ok)
        return Health("ok", f"watching {self.root}", self._last_scan_ok)

    # -- polling -------------------------------------------------------------------------
    def poll(self, max_items: int = 1) -> list[SourceItem]:
        now = self._clock()
        try:
            entries = sorted(os.scandir(self.root), key=lambda e: e.name)
            self._last_error = ""
            self._last_scan_ok = utcnow()
        except OSError as error:
            self._last_error = f"cannot scan {self.root}: {error.strerror or error}"
            return []
        items: list[SourceItem] = []
        present: set[str] = set()
        for entry in entries:
            name = entry.name
            if name in (self.processed_dir.name, self.rejected_dir.name) and entry.is_dir(follow_symlinks=False):
                continue
            if name.startswith(".") or name.endswith(IGNORED_SUFFIXES) or name.endswith(".reason.json"):
                continue
            present.add(name)
            if len(items) >= max_items:
                continue
            item = self._consider(entry, now)
            if item is not None:
                items.append(item)
        with self._lock:
            for gone in set(self._seen) - present:
                del self._seen[gone]
            for gone in set(self._cooldown) - present:
                del self._cooldown[gone]
        self.counters["offered"] += len(items)
        return items

    def _consider(self, entry: os.DirEntry, now: float) -> SourceItem | None:
        name = entry.name
        try:
            info = entry.stat(follow_symlinks=False)
        except OSError:
            return None
        if stat.S_ISLNK(info.st_mode):
            self._reject_path(name, "symbolic links are not accepted (file never read)")
            return None
        if stat.S_ISDIR(info.st_mode):
            return None
        if not stat.S_ISREG(info.st_mode):
            self._reject_path(name, "not a regular file (file never read)")
            return None
        if self.reject_hardlinks and info.st_nlink > 1:
            self._reject_path(name, "hard-linked files are not accepted (file never read)")
            return None
        if info.st_size > self.max_bytes:
            self._reject_path(name, f"file is {info.st_size} bytes, over the {self.max_bytes}-byte limit")
            return None
        with self._lock:
            if self._cooldown.get(name, 0) > now or any(rec[0] == name for rec in self._inflight.values()):
                return None  # cooling down after a failed delivery, or already offered and not yet answered
            state = (info.st_size, info.st_mtime_ns)
            seen = self._seen.get(name)
            if seen is None or seen[:2] != state:
                self._seen[name] = (*state, now)  # new or changed: restart the stability clock
                return None
            if now - seen[2] < self.stable_seconds:
                return None
        return self._read(name, info)

    def _read(self, name: str, info: os.stat_result) -> SourceItem | None:
        if not name.lower().endswith(ALLOWED_EXTENSIONS):  # judged only once the file has stopped changing
            self._reject_path(name, "unsupported file type: only .jpg, .jpeg, .png and .webp are accepted")
            return None
        path = self.root / name
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        try:
            fd = os.open(path, flags)
        except OSError as error:
            if error.errno in (errno.ELOOP, errno.EMLINK):
                self._reject_path(name, "symbolic links are not accepted (file never read)")
            return None
        try:
            opened = os.fstat(fd)
            if not stat.S_ISREG(opened.st_mode) or (opened.st_ino, opened.st_dev) != (info.st_ino, info.st_dev):
                self._reject_path(name, "file changed type while being opened (file never read)")
                return None
            if opened.st_size > self.max_bytes:
                self._reject_path(name, f"file is {opened.st_size} bytes, over the {self.max_bytes}-byte limit")
                return None
            with os.fdopen(fd, "rb", closefd=False) as handle:
                payload = handle.read(self.max_bytes + 1)
        finally:
            os.close(fd)
        if len(payload) > self.max_bytes:
            self._reject_path(name, f"file is over the {self.max_bytes}-byte limit")
            return None
        if (len(payload), opened.st_mtime_ns) != (info.st_size, info.st_mtime_ns):
            with self._lock:
                self._seen.pop(name, None)  # changed while reading; start over
            return None
        content_type = imagecheck.sniff(payload)
        if content_type is None:
            self._reject_path(name, "contents are not a JPEG, PNG or WebP image")
            return None
        if not imagecheck.extension_matches(name, content_type):
            self._reject_path(name, f"extension does not match the contents ({content_type})")
            return None
        problem = imagecheck.truncation_problem(payload, content_type)
        if problem:
            self._reject_path(name, problem)
            return None
        digest = hashlib.sha256(payload).hexdigest()
        item = SourceItem(
            payload=payload,
            content_type=content_type,
            source_id=f"{self.source_label}:{name}",
            captured_at=datetime.fromtimestamp(opened.st_mtime, tz=timezone.utc),
            idempotency_key=f"{self.source_label}:{name}:{digest}",
            metadata={"file_name": name, "captured_at_basis": "file modification time"},
        )
        with self._lock:
            self._inflight[item.idempotency_key] = (name, len(payload), opened.st_mtime_ns, opened.st_ino)
        return item

    # -- ack / nack ----------------------------------------------------------------------
    def ack(self, item: SourceItem, outcome: Outcome) -> None:
        with self._lock:
            record = self._inflight.pop(item.idempotency_key, None)
        if record is None:
            return
        name = record[0]
        if not self._unchanged(name, record):
            # The file was replaced after we read it: leave it so the new content is picked up.
            with self._lock:
                self._seen.pop(name, None)
            return
        self._move(self.root / name, self.processed_dir, name)
        self.counters["processed"] += 1

    def nack(self, item: SourceItem, reason: str, *, permanent: bool = False) -> None:
        with self._lock:
            record = self._inflight.pop(item.idempotency_key, None)
        name = record[0] if record else item.metadata.get("file_name", "")
        if not name or os.path.basename(name) != name:
            return
        if permanent:
            if record is not None and not self._unchanged(name, record):
                with self._lock:
                    self._seen.pop(name, None)
                return  # replaced since we read it: the new content gets its own turn
            self.counters["rejected_after_sink"] += 1
            self._reject_path(name, reason, sha256=item.sha256)
        else:
            with self._lock:
                self._cooldown[name] = self._clock() + self.retry_cooldown
                self._seen.pop(name, None)

    # -- file helpers --------------------------------------------------------------------
    def _unchanged(self, name: str, record: tuple[str, int, int, int]) -> bool:
        try:
            info = (self.root / name).lstat()
        except FileNotFoundError:
            return False
        return (
            not stat.S_ISLNK(info.st_mode)
            and (info.st_size, info.st_mtime_ns, info.st_ino) == (record[1], record[2], record[3])
        )

    def _unique(self, directory: Path, name: str) -> Path:
        target = directory / name
        counter = 0
        while target.exists() or target.is_symlink():
            counter += 1
            stem, dot, ext = name.rpartition(".")
            target = directory / (f"{stem}.{counter}.{ext}" if dot else f"{name}.{counter}")
        return target

    def _move(self, source: Path, directory: Path, name: str) -> Path | None:
        target = self._unique(directory, name)
        try:
            os.rename(source, target)  # renames a symlink itself; never follows it
        except FileNotFoundError:
            return None
        except OSError:
            self._last_error = f"could not move {name} to {directory.name}/"
            return None
        return target

    def _reject_path(self, name: str, reason: str, *, sha256: str | None = None) -> None:
        """Quarantine ``name`` (without reading it) and write ``<name>.reason.json``."""
        if os.path.basename(name) != name or name in ("", ".", ".."):
            return
        target = self._move(self.root / name, self.rejected_dir, name)
        if target is None:
            return
        sidecar = {
            "file": name,
            "reason": reason,
            "rejected_at": utcnow().isoformat(),
            "connector": self.name,
            "connector_version": self.version,
            "sha256": sha256,
            "note": "Rejected by the connector at the edge or refused by the sink. No person has reviewed this file.",
        }
        Path(f"{target}.reason.json").write_text(json.dumps(sidecar, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if sha256 is None:
            self.counters["rejected_at_edge"] += 1
        with self._lock:
            self._seen.pop(name, None)
            self._cooldown.pop(name, None)
