"""Environment and dependency preflight for operation drafts (T08).

One read-only endpoint answers "can this console actually run the work an
operation needs?" before anyone queues any: video checking, private storage,
the teaching helper, the check-screen helper, and the picture-reading model.
Every failed check names the person responsible for fixing it and one concrete
recovery step, so missing setup can never look like a quietly queued test.
"""

from __future__ import annotations

import shutil

from django.conf import settings
from django.core.cache import cache
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods

from .permissions import access
from .training import lifecycle
from .vision import client, service
from .vision_views import _no_store

ADMIN = "administrator"
SERVER_RUNNER = "whoever runs this Athena server"

# The checks are polled, so each one may be cached briefly; none of them is a
# prerequisite for writing anything, and every result stays a plain fact.
CACHE_SECONDS = 30
CACHE_KEY = "operation-preflight:v1"


def _check(key: str, title: str, ok: bool, detail: str, owner: str, recovery: str) -> dict:
    """One preflight row; ``detail`` explains the state, ``recovery`` the way out when blocked."""
    return {"id": key, "title": title, "ok": bool(ok), "detail": detail, "owner": owner, "recovery": recovery}


def _video_check() -> dict:
    installed = shutil.which("ffprobe") is not None
    return _check(
        "video_checking",
        "Video checking",
        installed,
        "Recordings are checked with ffprobe before they are accepted." if installed
        else "Recordings cannot be accepted: the video checking tool (ffprobe) is not installed.",
        ADMIN,
        "Install ffprobe on the server that runs Athena, then reload this page.",
    )


def _storage_check() -> dict:
    if settings.STORAGE_KIND == "s3":
        return _check(
            "storage",
            "Private storage",
            True,
            "Recordings and evidence are kept in the configured private bucket.",
            ADMIN,
            "",
        )
    from pathlib import Path

    media_root = Path(settings.MEDIA_ROOT)
    try:
        media_root.mkdir(parents=True, exist_ok=True)
        probe = media_root / ".athena-preflight"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        writable = True
    except OSError:
        writable = False
    return _check(
        "storage",
        "Private storage",
        writable,
        "Recordings and evidence are kept on this server's disk." if writable
        else "Recordings cannot be kept: the server's storage folder is not writable.",
        ADMIN,
        "Ask an administrator to make the Athena data folder writable (or configure private bucket storage), then reload this page.",
    )


def _teaching_check() -> dict:
    runnable, info = lifecycle.backend_runnable("torchvision-fasterrcnn")
    online = info["worker_online"]
    ok = runnable or (online and info["worker_reports_available"] is not False)
    if ok:
        detail = "The teaching helper can run: a worker is up." if online else "Teaching can run inside this Athena server."
    else:
        detail = "Teaching cannot run: no teaching helper is running and the model packages are not installed here."
    return _check(
        "teaching",
        "Teaching helper",
        ok,
        detail,
        SERVER_RUNNER,
        info.get("how_to_enable") or "Start the teaching helper (manage.py train_worker) on the server, then reload this page.",
    )


def _checkscreen_check() -> dict:
    worker = lifecycle.worker_status(lifecycle.INFERENCE_WORKER_KEY)
    ok = worker["online"]
    return _check(
        "checkscreen",
        "Check-screen helper",
        ok,
        "The check-screen helper is running." if ok
        else "Check screens cannot run their model: the check-screen helper is not running.",
        SERVER_RUNNER,
        "Start the check-screen helper (manage.py inference_worker) on the server, then reload this page.",
    )


def _vision_check(user) -> dict:
    conn = service.active_connection(user)
    if not conn.configured:
        return _check(
            "vision",
            "Picture-reading model",
            False,
            "Photo inspections have no model connected.",
            "person who manages Athena",
            "Connect a model on the AI model page (Settings), or ask the person who manages Athena to.",
        )
    reachable = client.ping(conn)
    return _check(
        "vision",
        "Picture-reading model",
        reachable,
        f"{conn.label} is answering." if reachable else f"{conn.label} is not answering right now.",
        "person who manages Athena",
        f"Check that the model server at {conn.host} is running, or review the connection on the AI model page (Settings).",
    )


def _build_checks(user) -> dict:
    checks = [_video_check(), _storage_check(), _teaching_check(), _checkscreen_check(), _vision_check(user)]
    return {
        "ready": all(c["ok"] for c in checks),
        "checks": checks,
        # Blockers are the failed checks, unchanged, with their owner and recovery text.
        "blockers": [c for c in checks if not c["ok"]],
    }


@access()
@require_http_methods(["GET"])
def operation_preflight(request: HttpRequest) -> JsonResponse:
    """One read-only answer: which parts of the environment are ready, and who
    fixes each one that is not. Nothing here queues or changes anything."""
    cached = cache.get(CACHE_KEY)
    if cached is None:
        cached = _build_checks(request.user)
        cache.set(CACHE_KEY, cached, CACHE_SECONDS)
    return _no_store(JsonResponse(cached))
