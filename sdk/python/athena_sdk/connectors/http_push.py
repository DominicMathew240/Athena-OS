# SPDX-License-Identifier: Apache-2.0
"""HttpPushConnector: a reference connector that accepts images POSTed to a tiny receiver.

Reference implementation only; no real scanner has been tested against it.

Request
-------
``POST /v1/items`` with the image as the raw body and these headers::

    X-Athena-Connector-Secret: <shared secret>        required
    Content-Type: image/jpeg | image/png | image/webp  required
    Content-Length: <bytes>                            required (no chunked uploads)
    X-Source-Id: <letters, digits, . _ : @ ->          optional, names the device
    Idempotency-Key: <letters, digits, . _ : @ = ->    optional, makes a retry safe
    X-Captured-At: <ISO-8601 with offset>              optional, defaults to receive time

Replies: 202 accepted (also for a repeat of a key already held), 401 wrong or missing
secret, 404 other paths, 405 other methods, 411/400 bad length, 413 too large, 415 not an
accepted image type (or the bytes do not match the declared type), 422 image looks
incomplete, 409 key reused with different content, 503 + ``Retry-After`` when the spool is
full (backpressure travels to the sender).

Durability: a 202 is only sent after the item is written (fsync + atomic rename) to the
spool directory, so a crash after the reply cannot lose it. The pipeline reads the spool.

Security: binds to loopback by default; binding elsewhere needs ``allow_non_loopback=True``.
The receiver speaks plain HTTP and the secret travels in a header, so anything beyond
loopback **must** sit behind a TLS-terminating reverse proxy. TLS is not implemented here
and has not been tested.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from athena_sdk.connectors import imagecheck
from athena_sdk.connectors.base import ACCEPTED_CONTENT_TYPES, DataSourceConnector, Health, Outcome, SourceItem, utcnow

logger = logging.getLogger("athena_sdk.connectors.http_push")
SECRET_HEADER = "X-Athena-Connector-Secret"
MIN_SECRET_LENGTH = 16
_SOURCE_ID = re.compile(r"^[A-Za-z0-9._:@-]{1,128}$")
_KEY = re.compile(r"^[A-Za-z0-9._:@=-]{1,128}$")


def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


class HttpPushConnector(DataSourceConnector):
    name = "http-push"
    version = "0.1.0"

    def __init__(
        self,
        spool_dir: str | Path,
        secret: str,
        *,
        host: str = "127.0.0.1",
        port: int = 8820,
        allow_non_loopback: bool = False,
        max_bytes: int = 20 * 1024 * 1024,
        max_pending: int = 64,
        source_label: str = "http-push",
        retry_cooldown: float = 30.0,
        read_timeout: float = 10.0,
    ) -> None:
        if not isinstance(secret, str) or len(secret) < MIN_SECRET_LENGTH:
            raise ValueError(f"The shared secret must be at least {MIN_SECRET_LENGTH} characters.")
        if not _is_loopback(host) and not allow_non_loopback:
            raise ValueError(
                f"{host!r} is not a loopback address. Bind to 127.0.0.1 behind a TLS-terminating proxy, "
                "or pass allow_non_loopback=True if you accept plain HTTP on that interface."
            )
        self.spool = Path(spool_dir)
        self.rejected = self.spool / "rejected"
        self._secret = secret.encode()
        self.host, self.port = host, port
        self.max_bytes = max_bytes
        self.max_pending = max_pending
        self.source_label = source_label
        self.retry_cooldown = retry_cooldown
        self.read_timeout = read_timeout
        self._lock = threading.Lock()
        self._known: dict[str, str] = {}  # spool name -> sha256 (queued on disk or in flight)
        self._inflight: dict[str, Path] = {}
        self._cooldown: dict[str, float] = {}
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._last_accept_at: datetime | None = None
        self.counters = {"accepted": 0, "unauthorized": 0, "bad_type": 0, "too_large": 0, "backpressure": 0, "duplicates": 0}

    # -- lifecycle -----------------------------------------------------------------------
    @property
    def address(self) -> tuple[str, int]:
        assert self._server is not None, "open() has not been called"
        return self._server.server_address[0], self._server.server_address[1]  # type: ignore[return-value]

    def open(self) -> None:
        self.spool.mkdir(parents=True, exist_ok=True)
        self.rejected.mkdir(parents=True, exist_ok=True)
        with self._lock:
            for path in self.spool.glob("*.item"):
                header = self._read_header(path)
                if header:
                    self._known[path.name] = header["sha256"]
        for stray in self.spool.glob(".tmp-*"):  # half-written spool files from a crash
            stray.unlink(missing_ok=True)
        connector = self

        class Handler(_Handler):
            owner = connector

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, name="http-push", daemon=True)
        self._thread.start()

    def close(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.shutdown()
            server.server_close()

    def health(self) -> Health:
        if self._server is None or self._thread is None or not self._thread.is_alive():
            return Health("down", "receiver is not running", self._last_accept_at)
        if not (self.spool.is_dir() and os.access(self.spool, os.W_OK)):
            return Health("down", f"spool {self.spool} is not writable", self._last_accept_at)
        with self._lock:
            pending = len(self._known)
        if pending >= self.max_pending:
            return Health("degraded", f"spool full ({pending}/{self.max_pending}); senders are receiving 503", self._last_accept_at)
        return Health("ok", f"listening on {self.host}:{self.address[1]}, {pending} pending", self._last_accept_at)

    # -- receiving (called from handler threads) -----------------------------------------
    def authorized(self, presented: str | None) -> bool:
        return bool(presented) and hmac.compare_digest(presented.encode("utf-8", "replace"), self._secret)

    def spool_item(self, payload: bytes, content_type: str, source_id: str, key: str, captured_at: datetime,
                   remote_addr: str) -> tuple[int, dict[str, Any]]:
        sha = hashlib.sha256(payload).hexdigest()
        name = hashlib.sha256(key.encode()).hexdigest()[:40] + ".item"
        with self._lock:
            if name in self._known:
                if self._known[name] != sha:
                    return 409, {"error": "This Idempotency-Key was already used for different content."}
                self.counters["duplicates"] += 1
                return 202, {"accepted": True, "duplicate": True, "idempotency_key": key, "sha256": sha}
            if len(self._known) >= self.max_pending:
                self.counters["backpressure"] += 1
                return 503, {"error": "Too many images are waiting. Retry shortly."}
            header = {
                "idempotency_key": key, "source_id": source_id, "content_type": content_type, "sha256": sha,
                "captured_at": captured_at.astimezone(timezone.utc).isoformat(),
                "received_at": utcnow().isoformat(), "remote_addr": remote_addr,
            }
            tmp = self.spool / f".tmp-{secrets.token_hex(8)}"
            with open(tmp, "wb") as handle:
                handle.write(json.dumps(header, sort_keys=True).encode() + b"\n" + payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.spool / name)
            self._fsync_dir()
            self._known[name] = sha
            self._last_accept_at = utcnow()
            self.counters["accepted"] += 1
        return 202, {"accepted": True, "duplicate": False, "idempotency_key": key, "sha256": sha}

    def _fsync_dir(self) -> None:
        try:
            fd = os.open(self.spool, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(fd)
        except OSError:
            pass
        finally:
            os.close(fd)

    @staticmethod
    def _read_header(path: Path) -> dict[str, Any] | None:
        try:
            with open(path, "rb") as handle:
                return json.loads(handle.readline())
        except (OSError, ValueError):
            return None

    # -- polling -------------------------------------------------------------------------
    def poll(self, max_items: int = 1) -> list[SourceItem]:
        now = time.monotonic()
        try:
            paths = sorted(self.spool.glob("*.item"), key=lambda p: p.stat().st_mtime_ns)
        except OSError:
            return []
        items: list[SourceItem] = []
        for path in paths:
            if len(items) >= max_items:
                break
            with self._lock:
                if path.name in self._inflight or self._cooldown.get(path.name, 0) > now:
                    continue
            try:
                with open(path, "rb") as handle:
                    header = json.loads(handle.readline())
                    payload = handle.read()
                if hashlib.sha256(payload).hexdigest() != header["sha256"]:
                    raise ValueError("spool file does not match its recorded SHA-256")
                item = SourceItem(
                    payload=payload,
                    content_type=header["content_type"],
                    source_id=header["source_id"],
                    captured_at=datetime.fromisoformat(header["captured_at"]),
                    idempotency_key=header["idempotency_key"],
                    metadata={"remote_addr": header.get("remote_addr", ""), "transport": "http-push",
                              "spool_received_at": header.get("received_at", "")},
                )
            except (OSError, ValueError, KeyError) as error:
                self._quarantine(path, f"unreadable spool entry: {error}")
                continue
            with self._lock:
                self._inflight[path.name] = path
            items.append(item)
        return items

    def _spool_name(self, item: SourceItem) -> str:
        return hashlib.sha256(item.idempotency_key.encode()).hexdigest()[:40] + ".item"

    def ack(self, item: SourceItem, outcome: Outcome) -> None:
        name = self._spool_name(item)
        with self._lock:
            self._inflight.pop(name, None)
            self._known.pop(name, None)
        (self.spool / name).unlink(missing_ok=True)

    def nack(self, item: SourceItem, reason: str, *, permanent: bool = False) -> None:
        name = self._spool_name(item)
        with self._lock:
            self._inflight.pop(name, None)
            if not permanent:
                self._cooldown[name] = time.monotonic() + self.retry_cooldown
                return
            self._known.pop(name, None)
        self._quarantine(self.spool / name, reason, sha256=item.sha256)

    def _quarantine(self, path: Path, reason: str, *, sha256: str | None = None) -> None:
        target = self.rejected / path.name
        try:
            os.replace(path, target)
        except OSError:
            return
        with self._lock:
            self._known.pop(path.name, None)
            self._inflight.pop(path.name, None)
        sidecar = {"file": path.name, "reason": reason, "rejected_at": utcnow().isoformat(), "sha256": sha256,
                   "connector": self.name, "connector_version": self.version,
                   "note": "Refused by the sink or damaged in the spool. No person has reviewed this item."}
        Path(f"{target}.reason.json").write_text(json.dumps(sidecar, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class _Handler(BaseHTTPRequestHandler):
    owner: HttpPushConnector
    server_version = "AthenaConnector/0.1"
    sys_version = ""
    protocol_version = "HTTP/1.0"  # one request per connection: no body desync after an early error

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(self.owner.read_timeout)

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("%s - %s", self.address_string(), format % args)

    def _reply(self, status: int, body: dict[str, Any], headers: dict[str, str] | None = None) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path == "/healthz":
            health = self.owner.health()
            self._reply(200 if health.status != "down" else 503, {"status": health.status})
        else:
            self._reply(404, {"error": "Not found."})

    def _not_allowed(self) -> None:
        self._reply(405, {"error": "Use POST /v1/items."}, {"Allow": "POST"})

    do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _not_allowed

    def do_POST(self) -> None:
        owner = self.owner
        if self.path != "/v1/items":
            return self._reply(404, {"error": "Not found."})
        if not owner.authorized(self.headers.get(SECRET_HEADER)):
            owner.counters["unauthorized"] += 1
            return self._reply(401, {"error": "Missing or wrong shared secret."})
        declared = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if declared not in ACCEPTED_CONTENT_TYPES:
            owner.counters["bad_type"] += 1
            return self._reply(415, {"error": f"Content-Type must be one of {', '.join(ACCEPTED_CONTENT_TYPES)}."})
        if self.headers.get("Transfer-Encoding"):
            return self._reply(411, {"error": "Chunked uploads are not supported; send Content-Length."})
        length_header = self.headers.get("Content-Length")
        if length_header is None:
            return self._reply(411, {"error": "Content-Length is required."})
        try:
            length = int(length_header)
        except ValueError:
            return self._reply(400, {"error": "Content-Length is not a number."})
        if length <= 0:
            return self._reply(400, {"error": "The body is empty."})
        if length > owner.max_bytes:
            owner.counters["too_large"] += 1
            return self._reply(413, {"error": f"The body is over the {owner.max_bytes}-byte limit."})
        label = owner.source_label
        raw_source = self.headers.get("X-Source-Id")
        if raw_source is not None and not _SOURCE_ID.match(raw_source):
            return self._reply(400, {"error": "X-Source-Id may use letters, digits and . _ : @ - (max 128)."})
        remote = self.client_address[0]
        source_id = f"{label}:{raw_source or remote}"
        raw_key = self.headers.get("Idempotency-Key")
        if raw_key is not None and not _KEY.match(raw_key):
            return self._reply(400, {"error": "Idempotency-Key may use letters, digits and . _ : @ = - (max 128)."})
        captured = utcnow()
        if self.headers.get("X-Captured-At"):
            try:
                captured = datetime.fromisoformat(self.headers["X-Captured-At"])
            except ValueError:
                return self._reply(400, {"error": "X-Captured-At must be ISO-8601 with a UTC offset."})
            if captured.tzinfo is None:
                return self._reply(400, {"error": "X-Captured-At must include a UTC offset."})
        try:
            payload = self.rfile.read(length)
        except (OSError, TimeoutError):
            return self._reply(408, {"error": "Timed out reading the body."})
        if len(payload) != length:
            return self._reply(400, {"error": "The body was shorter than Content-Length."})
        if imagecheck.sniff(payload) != declared:
            owner.counters["bad_type"] += 1
            return self._reply(415, {"error": "The bytes are not the image type declared in Content-Type."})
        problem = imagecheck.truncation_problem(payload, declared)
        if problem:
            return self._reply(422, {"error": problem})
        key = f"{source_id}:{raw_key}" if raw_key else f"{source_id}:{hashlib.sha256(payload).hexdigest()}"
        status, body = owner.spool_item(payload, declared, source_id, key, captured, remote)
        self._reply(status, body, {"Retry-After": "5"} if status == 503 else None)
