# SPDX-License-Identifier: Apache-2.0
"""AthenaConsoleSink: deliver connector items to the Athena console's check screen.

Standard library only (``urllib`` + ``http.cookiejar``). It does what a person does in a
browser, nothing more:

1. open ``/login/``, read the CSRF token, sign in as a dedicated service account;
2. ``POST /api/projects/<project>/inference/apps/<app>/run`` with one multipart ``image``;
3. poll ``.../observations/<id>`` until the console's own worker reports ``succeeded`` or
   ``failed`` (or the poll window ends) and return a :class:`Receipt`.

What it deliberately does **not** do: post feedback, record a review decision, change an
app, model or role. Those are human actions in the console. The class has no method for
them and a test keeps it that way.

Credentials come from the environment only (``ATHENA_CONNECTOR_USER`` and
``ATHENA_CONNECTOR_PASSWORD``); see :meth:`AthenaConsoleSink.from_env`. The minimum role
that can run a check screen is **Labeler** (``POST .../run`` is a ``label`` action); a
Viewer is refused with 403. A Labeler account can also post feedback in the console, so
treat the password like any other write credential.
"""

from __future__ import annotations

import hashlib
import http.cookiejar
import ipaddress
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from athena_sdk.connectors.base import (
    Health,
    PermanentItemError,
    Receipt,
    Sink,
    SinkUnavailableError,
    SourceItem,
    TransientSinkError,
    utcnow,
)

USER_ENV = "ATHENA_CONNECTOR_USER"
PASSWORD_ENV = "ATHENA_CONNECTOR_PASSWORD"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_CSRF_FIELD = re.compile(r'name=["\']csrfmiddlewaretoken["\']\s+value=["\']([^"\']+)["\']')
_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def upload_filename(item: SourceItem) -> str:
    """``<16 hex of the idempotency key>__<safe source name><ext>``.

    The console stores this as the observation's filename, so a delivery can be matched to the
    ledger later (and a delivery that was in doubt after a crash can be recognised).
    """
    prefix = hashlib.sha256(item.idempotency_key.encode()).hexdigest()[:16]
    stem = os.path.splitext(item.metadata.get("file_name") or item.source_id.rsplit(":", 1)[-1])[0]
    stem = _FILENAME_SAFE.sub("_", stem).strip("._") or "item"
    return f"{prefix}__{stem[:60]}{_EXT[item.content_type]}"


class AthenaConsoleSink(Sink):
    name = "athena-console"

    def __init__(
        self,
        base_url: str,
        project_id: str,
        app: str,
        username: str,
        password: str,
        *,
        poll_timeout: float = 60.0,
        poll_interval: float = 1.0,
        request_timeout: float = 30.0,
        allow_insecure_http: bool = False,
        auth_cooldown: float = 300.0,
        sleep: Callable[[float], Any] | None = None,
    ) -> None:
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("base_url must look like https://console.example.org or http://127.0.0.1:8000")
        if parsed.scheme == "http" and not _is_loopback(parsed.hostname) and not allow_insecure_http:
            raise ValueError(
                "Refusing to send a password over plain http to a non-loopback host. Use https, or set "
                "allow_insecure_http only on a network you fully control."
            )
        try:
            self.project_id = str(uuid.UUID(str(project_id)))
        except ValueError:
            raise ValueError("project_id must be the project's UUID.") from None
        if not app or not str(app).strip():
            raise ValueError("app must be the check screen's slug or UUID.")
        if not username or not password:
            raise ValueError("A username and password are required.")
        self.base_url = f"{parsed.scheme}://{parsed.netloc}"
        self.app_ref = str(app).strip()
        self.poll_timeout = poll_timeout
        self.poll_interval = poll_interval
        self.request_timeout = request_timeout
        self.auth_cooldown = auth_cooldown
        self._username = username
        self.__password = password
        self._sleep = sleep
        self._stop = threading.Event()
        self._jar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self._jar), _NoRedirect())
        self._signed_in = False
        self._app_id: str | None = None
        self._app_slug: str | None = None
        self._auth_blocked_until = 0.0
        self._uncertain: set[str] = set()
        self._last_success_at: datetime | None = None
        self._last_error = ""
        self._unavailable = False

    def __repr__(self) -> str:
        return f"AthenaConsoleSink(base_url={self.base_url!r}, project_id={self.project_id!r}, app={self.app_ref!r})"

    @classmethod
    def from_env(cls, base_url: str, project_id: str, app: str, env: Mapping[str, str] | None = None, **kwargs: Any
                 ) -> AthenaConsoleSink:
        """Build the sink with credentials from ``ATHENA_CONNECTOR_USER/PASSWORD`` (never CLI args)."""
        env = os.environ if env is None else env
        user, password = env.get(USER_ENV, ""), env.get(PASSWORD_ENV, "")
        if not user or not password:
            raise ValueError(f"Set {USER_ENV} and {PASSWORD_ENV} in the environment (a dedicated service account).")
        return cls(base_url, project_id, app, user, password, **kwargs)

    # -- HTTP plumbing -------------------------------------------------------------------
    def _open(self, method: str, path: str, *, data: bytes | None = None, headers: dict[str, str] | None = None
              ) -> tuple[int, Mapping[str, str], bytes]:
        request = urllib.request.Request(self.base_url + path, data=data, method=method)
        request.add_header("User-Agent", "athena-connector/0.1")
        request.add_header("Accept", "application/json")
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with self._opener.open(request, timeout=self.request_timeout) as response:
                return response.status, dict(response.headers), response.read(MAX_RESPONSE_BYTES)
        except urllib.error.HTTPError as error:  # 3xx (not followed) and 4xx/5xx are answers, not failures
            try:
                body = error.read(MAX_RESPONSE_BYTES)
            finally:
                error.close()
            return error.code, dict(error.headers), body
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as error:
            reason = getattr(error, "reason", error)
            raise TransientSinkError(f"console unreachable: {reason}") from None

    def _cookie(self, name: str) -> str | None:
        for cookie in self._jar:
            if cookie.name == name:
                return cookie.value
        return None

    @staticmethod
    def _json(body: bytes) -> dict[str, Any]:
        try:
            data = json.loads(body.decode("utf-8", "replace"))
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _message(body: bytes, fallback: str) -> str:
        return str(AthenaConsoleSink._json(body).get("error") or fallback)[:300]

    @staticmethod
    def _retry_after(headers: Mapping[str, str], default: float) -> float:
        for key, value in headers.items():
            if key.lower() == "retry-after":
                try:
                    return max(0.0, float(value))
                except ValueError:
                    break
        return default

    # -- sign-in -------------------------------------------------------------------------
    def _login(self) -> None:
        now = time.monotonic()
        if now < self._auth_blocked_until:
            raise SinkUnavailableError(f"sign-in is paused after a refusal ({self._last_error})")
        status, _, page = self._open("GET", "/login/")
        if status in (301, 302, 303) and self._cookie("sessionid"):
            self._signed_in = True  # already holds a session
            return
        if status != 200:
            raise TransientSinkError(f"the sign-in page answered HTTP {status}")
        found = _CSRF_FIELD.search(page.decode("utf-8", "replace"))
        token = found.group(1) if found else self._cookie("csrftoken")
        if not token:
            raise TransientSinkError("the sign-in page gave no CSRF token")
        form = urllib.parse.urlencode(
            {"username": self._username, "password": self.__password, "csrfmiddlewaretoken": token, "next": "/"}
        ).encode()
        status, headers, body = self._open(
            "POST", "/login/", data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded", "Referer": self.base_url + "/login/"},
        )
        if status in (301, 302, 303) and self._cookie("sessionid"):
            self._signed_in = True
            self._last_error = ""
            return
        self._signed_in = False
        if status == 429:
            self._auth_blocked_until = now + max(self.auth_cooldown, self._retry_after(headers, 0))
            self._last_error = "the console locked this account or address out after failed sign-ins"
        elif status == 200:
            self._auth_blocked_until = now + self.auth_cooldown  # never hammer a wrong password
            self._last_error = f"sign-in refused: check {USER_ENV} and {PASSWORD_ENV}"
        else:
            raise TransientSinkError(f"sign-in answered HTTP {status}")
        self._unavailable = True
        raise SinkUnavailableError(self._last_error)

    def _ensure_session(self) -> None:
        if not self._signed_in:
            self._login()

    def _drop_session(self) -> None:
        self._signed_in = False
        self._jar.clear()

    def _api(self, method: str, path: str, *, data: bytes | None = None, headers: dict[str, str] | None = None
             ) -> tuple[int, Mapping[str, str], bytes]:
        """Authenticated request; re-signs in once if the session expired or the CSRF token went stale."""
        for attempt in (1, 2):
            self._ensure_session()
            send = dict(headers or {})
            if method != "GET":
                send["X-CSRFToken"] = self._cookie("csrftoken") or ""
                send["Referer"] = self.base_url + "/"
            status, resp_headers, body = self._open(method, path, data=data, headers=send)
            expired = status == 401 or status in (301, 302, 303)
            stale_csrf = status == 403 and "required_role" not in self._json(body) and b"CSRF" in body
            if (expired or stale_csrf) and attempt == 1:
                self._drop_session()
                continue
            if expired or stale_csrf:
                raise SinkUnavailableError("the console keeps refusing this session; check the service account")
            return status, resp_headers, body
        raise AssertionError("unreachable")  # pragma: no cover

    # -- app lookup ----------------------------------------------------------------------
    def _resolve_app(self) -> str:
        if self._app_id:
            return self._app_id
        status, _, body = self._api("GET", f"/api/projects/{self.project_id}/inference/apps")
        if status == 403:
            raise SinkUnavailableError("the service account may not look at this project (needs at least Viewer)")
        if status == 404:
            raise SinkUnavailableError("the project was not found; check project_id")
        if status != 200:
            raise TransientSinkError(f"listing check screens answered HTTP {status}")
        wanted = self.app_ref.lower()
        for entry in self._json(body).get("apps", []):
            if wanted in (str(entry.get("id", "")).lower(), str(entry.get("slug", "")).lower()):
                self._app_id, self._app_slug = str(entry["id"]), str(entry.get("slug", ""))
                return self._app_id
        raise SinkUnavailableError(f"no check screen {self.app_ref!r} in this project; check the slug or id")

    def _base(self) -> str:
        return f"/api/projects/{self.project_id}/inference/apps/{self._resolve_app()}"

    # -- Sink interface ------------------------------------------------------------------
    def submit(self, item: SourceItem) -> Receipt:
        try:
            receipt = self._submit(item)
        except SinkUnavailableError as error:
            self._unavailable, self._last_error = True, str(error)
            raise
        except TransientSinkError as error:
            self._last_error = str(error)
            raise
        self._unavailable, self._last_error, self._last_success_at = False, "", utcnow()
        return receipt

    def _submit(self, item: SourceItem) -> Receipt:
        base = self._base()
        key = item.idempotency_key
        if key in self._uncertain:
            existing = self.find_existing(item)
            if existing is not None:
                self._uncertain.discard(key)
                return existing
        boundary = "----athena" + secrets.token_hex(12)
        filename = upload_filename(item)
        body = b"".join(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'.encode(),
                f"Content-Type: {item.content_type}\r\n\r\n".encode(),
                item.payload,
                f"\r\n--{boundary}--\r\n".encode(),
            ]
        )
        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "X-Athena-Connector": f"{item.connector}/{item.connector_version}",
            "X-Athena-Source-Id": _FILENAME_SAFE.sub("_", item.source_id)[:200],
            "X-Athena-Idempotency-Key": hashlib.sha256(key.encode()).hexdigest(),
            "X-Athena-Content-SHA256": item.sha256,
        }
        self._uncertain.add(key)  # until the console answers, we cannot know whether it stored the image
        status, resp_headers, resp = self._api("POST", base + "/run", data=body, headers=headers)
        text = self._message(resp, f"HTTP {status}")
        if status == 202:
            payload = self._json(resp)
            observation_id = str(payload.get("observation_id", ""))
            try:
                uuid.UUID(observation_id)
            except ValueError:
                raise TransientSinkError("the console accepted the image but returned no observation id") from None
            self._uncertain.discard(key)
            return self._wait_for_result(item, observation_id)
        self._uncertain.discard(key)  # the console answered with a refusal, so nothing was stored
        if status in (400, 413, 415, 422):
            raise PermanentItemError(text)
        if status == 429:
            raise TransientSinkError(text, retry_after=self._retry_after(resp_headers, 10.0))
        if status in (403, 404, 409):
            raise SinkUnavailableError(f"the console refused the run (HTTP {status}): {text}")
        if status == 503:
            raise TransientSinkError(text, retry_after=self._retry_after(resp_headers, 30.0))
        raise TransientSinkError(f"the console answered HTTP {status}: {text}")

    def _wait_for_result(self, item: SourceItem, observation_id: str) -> Receipt:
        path = f"{self._base()}/observations/{observation_id}"
        deadline = time.monotonic() + self.poll_timeout
        render: dict[str, Any] = {}
        final = ""
        while True:
            try:
                status, _, body = self._api("GET", path)
                if status == 200:
                    render = self._json(body).get("render", {}) or render
                    final = str((render.get("result") or {}).get("status", ""))
                    if final in ("succeeded", "failed"):
                        break
            except (TransientSinkError, SinkUnavailableError):
                pass  # the observation already exists; keep trying until the window ends
            if time.monotonic() >= deadline or self._stop.is_set():
                break
            (self._sleep or self._stop.wait)(self.poll_interval)
        return self._receipt(item, observation_id, render, final)

    def _receipt(self, item: SourceItem, observation_id: str, render: Mapping[str, Any], final: str) -> Receipt:
        result = render.get("result") or {}
        model = render.get("model") or {}
        console_sha = (result.get("image") or {}).get("sha256")
        detail = {
            "observation_id": observation_id,
            "observation_status": final or "unconfirmed",
            "app_id": self._app_id,
            "app_slug": self._app_slug,
            "project_id": self.project_id,
            "model_version_id": model.get("id"),
            "model_version": model.get("version"),
            "model_artifact_sha256": model.get("artifact_sha256"),
            "console_image_sha256": console_sha,
            "sha256_match": (console_sha == item.sha256) if console_sha else None,
            "awaiting_human_decision": result.get("awaiting_human_decision"),
            "analysis_error": result.get("error") or None,
            "upload_filename": upload_filename(item),
        }
        status = final if final in ("succeeded", "failed") else "pending_timeout"
        return Receipt(sink=self.name, remote_id=observation_id, status=status, detail=detail)

    def find_existing(self, item: SourceItem) -> Receipt | None:
        """Recognise an earlier delivery of ``item`` (same SHA-256 and our key-derived filename)
        among the check screen's 100 most recent observations."""
        status, _, body = self._api("GET", f"{self._base()}/observations")
        if status != 200:
            raise TransientSinkError(f"listing observations answered HTTP {status}")
        prefix = upload_filename(item).split("__", 1)[0] + "__"
        for render in self._json(body).get("observations", []):
            result = render.get("result") or {}
            image = result.get("image") or {}
            if image.get("sha256") == item.sha256 and str(image.get("filename", "")).startswith(prefix):
                final = str(result.get("status", ""))
                return self._receipt(item, str(result.get("observation_id")), render, final)
        return None

    def health(self) -> Health:
        """Last known state; makes no network call."""
        if self._unavailable:
            return Health("down", self._last_error or "console unavailable", self._last_success_at)
        if self._last_error:
            return Health("degraded", self._last_error, self._last_success_at)
        return Health("ok", "no problems seen" if self._last_success_at else "not contacted yet", self._last_success_at)

    def close(self) -> None:
        self._stop.set()
