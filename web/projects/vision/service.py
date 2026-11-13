"""Which model a person uses, and running an inspection in the background."""

from __future__ import annotations

import logging
import threading
import time
from datetime import timedelta

from django.conf import settings
from django.core.files.storage import default_storage
from django.db import close_old_connections
from django.db.models import Q
from django.utils import timezone

from ..models import AIConnection, Inspection
from . import catalog, client, secrets

logger = logging.getLogger(__name__)
STALE_AFTER_SECONDS = 120  # beyond the model timeout: a run still "running" then was lost with a server restart


def builtin_connection() -> client.Connection:
    vision = settings.VISION
    return client.Connection(
        source="builtin",
        label=vision["LABEL"] or "Qwen vision (built-in)",
        base_url=client.normalise_base_url(vision["BASE_URL"]),
        model=vision["MODEL"],
        api_key=vision["API_KEY"],
    )


def own_connection(user) -> tuple[AIConnection | None, client.Connection | None]:
    row = AIConnection.objects.filter(user=user).first()
    if row is None or not row.base_url:
        return row, None
    key = secrets.decrypt(row.api_key_encrypted) or ""
    return row, client.Connection(
        source="own", label=row.label or "Your own AI", base_url=row.base_url, model=row.model, api_key=key
    )


def active_connection(user) -> client.Connection:
    row, own = own_connection(user)
    if row is not None and row.use_own and own is not None:
        return own
    return builtin_connection()


def describe_connection(conn: client.Connection | None) -> dict | None:
    if conn is None:
        return None
    return {"source": conn.source, "label": conn.label, "model": conn.model, "host": conn.host, "configured": conn.configured}


def settings_payload(user) -> dict:
    row, own = own_connection(user)
    builtin = builtin_connection()
    active = own if (row is not None and row.use_own and own is not None) else builtin
    key_unreadable = bool(row and row.api_key_encrypted and secrets.decrypt(row.api_key_encrypted) is None)
    return {
        "active": describe_connection(active),
        "builtin": describe_connection(builtin),
        "own": {
            "use_own": bool(row and row.use_own),
            "label": row.label if row else "",
            "base_url": row.base_url if row else "",
            "model": row.model if row else "",
            "has_key": bool(row and row.api_key_encrypted) and not key_unreadable,
            "key_hint": row.key_hint if row and not key_unreadable else "",
            "key_unreadable": key_unreadable,
            "updated_at": row.updated_at.isoformat() if row else None,
        },
    }


# --- running -------------------------------------------------------------------------------------------

def start(inspection: Inspection) -> None:
    worker = threading.Thread(target=run, args=(inspection.id,), name=f"inspection-{inspection.id}", daemon=True)
    worker.start()


def run(inspection_id) -> None:
    close_old_connections()
    try:
        _run(inspection_id)
    except Exception:  # noqa: BLE001 - never leave an inspection "running" because of a bug
        logger.exception("Inspection %s failed unexpectedly", inspection_id)
        Inspection.objects.filter(id=inspection_id, status__in=Inspection.ACTIVE_STATUSES).update(
            status=Inspection.STATUS_FAILED,
            error="Something went wrong inside Athena while reading the answer. Try again; if it keeps happening, tell your administrator.",
            finished_at=timezone.now(),
        )
    finally:
        close_old_connections()


def _run(inspection_id) -> None:
    inspection = Inspection.objects.select_related("created_by").get(id=inspection_id)
    conn = active_connection(inspection.created_by)
    started = time.monotonic()
    Inspection.objects.filter(id=inspection.id).update(
        status=Inspection.STATUS_RUNNING, started_at=timezone.now(), provider=conn.source,
        model_label=f"{conn.label} · {conn.model}"[:200], endpoint_host=conn.host[:200], error="",
    )
    template = catalog.BY_KEY.get(inspection.template, catalog.CUSTOM_TEMPLATE)
    pictures = []
    for image in inspection.images.all():
        with default_storage.open(image.storage_key, "rb") as handle:
            data_url, width, height = client.prepare_image(handle.read())
        pictures.append({"data_url": data_url, "width": width, "height": height, "frame_time": image.frame_time})
    messages = client.build_messages(template, inspection.instructions, pictures, inspection.source)
    try:
        text = client.chat(conn, messages, max_tokens=settings.VISION["MAX_TOKENS"], timeout=settings.VISION["TIMEOUT_SECONDS"])
        result = client.parse_result(text, [(p["width"], p["height"]) for p in pictures])
    except client.VisionError as error:
        Inspection.objects.filter(id=inspection.id).update(
            status=Inspection.STATUS_FAILED, error=error.message, result={"error_kind": error.kind},
            finished_at=timezone.now(), duration_ms=int((time.monotonic() - started) * 1000),
        )
        return
    Inspection.objects.filter(id=inspection.id).update(
        status=Inspection.STATUS_DONE, verdict=result["verdict"], result=result, error="",
        finished_at=timezone.now(), duration_ms=int((time.monotonic() - started) * 1000),
    )


def expire_lost_runs(queryset) -> None:
    """Runs that were still going when the server restarted can never finish: say so instead of spinning forever."""
    cutoff = timezone.now() - timedelta(seconds=settings.VISION["TIMEOUT_SECONDS"] + STALE_AFTER_SECONDS)
    lost = Q(started_at__lt=cutoff) | Q(started_at__isnull=True, created_at__lt=cutoff)
    queryset.filter(lost, status__in=Inspection.ACTIVE_STATUSES).update(
        status=Inspection.STATUS_FAILED,
        error="This inspection stopped before it finished (the server may have restarted). Press Run again.",
        finished_at=timezone.now(),
    )
