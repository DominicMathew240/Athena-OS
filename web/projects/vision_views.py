"""JSON API for vision inspections and each person's AI model connection."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

from django.core.cache import cache
from django.core.exceptions import RequestDataTooBig
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Count, Q
from django.http import FileResponse, Http404, HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .models import AIConnection, Inspection, InspectionImage
from .permissions import access
from .views import _safe_filename, _validate_uploaded_image
from .vision import catalog, client, secrets, service

MAX_PICTURES = 6
MAX_BATCH_BYTES = 60 * 1024 * 1024
MAX_JSON_BYTES = 16_384
MAX_INSTRUCTIONS = 2000


def _no_store(response: JsonResponse) -> JsonResponse:
    response["Cache-Control"] = "no-store"
    return response


def _error(message: str, status: int = 400, **extra) -> JsonResponse:
    return _no_store(JsonResponse({"error": message, **extra}, status=status))


def _json_body(request: HttpRequest) -> dict:
    if request.content_type != "application/json":
        raise ValueError("Send the details as JSON.")
    try:
        body = request.body
    except RequestDataTooBig as error:
        raise ValueError("That request is too large.") from error
    if len(body) > MAX_JSON_BYTES:
        raise ValueError("That request is too large.")
    try:
        payload = json.loads(body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError("The details were not valid JSON.") from error
    if not isinstance(payload, dict):
        raise ValueError("The details must be a JSON object.")
    return payload


def _image_url(inspection: Inspection, position: int) -> str:
    return f"/api/inspections/{inspection.id}/images/{position}"


def _inspection_data(inspection: Inspection, *, full: bool = True) -> dict:
    template = catalog.BY_KEY.get(inspection.template, catalog.CUSTOM_TEMPLATE)
    images = list(inspection.images.all())
    result = inspection.result if inspection.status == Inspection.STATUS_DONE else {}
    data = {
        "id": str(inspection.id),
        "title": inspection.title,
        "template": inspection.template,
        "template_name": template["name"],
        "template_icon": template["icon"],
        "status": inspection.status,
        "verdict": inspection.verdict,
        "headline": (result or {}).get("headline", ""),
        "source": inspection.source,
        "image_count": len(images),
        "thumbnail_url": _image_url(inspection, images[0].position) if images else None,
        "provider": inspection.provider,
        "model_label": inspection.model_label,
        "created_at": inspection.created_at.isoformat(),
        "finished_at": inspection.finished_at.isoformat() if inspection.finished_at else None,
        "duration_ms": inspection.duration_ms,
        "url": f"/inspections/{inspection.id}",
    }
    if full:
        data.update({
            "instructions": inspection.instructions,
            "question": template["question"] or inspection.instructions,
            "result": result,
            "error": inspection.error,
            "endpoint_host": inspection.endpoint_host,
            "started_at": inspection.started_at.isoformat() if inspection.started_at else None,
            "feedback": inspection.feedback,
            "feedback_note": inspection.feedback_note,
            "images": [
                {"position": image.position, "url": _image_url(inspection, image.position), "width": image.width,
                 "height": image.height, "filename": image.filename, "frame_time": image.frame_time}
                for image in images
            ],
        })
    return data


def _mine(request: HttpRequest):
    return Inspection.objects.filter(created_by=request.user)


# --- templates and the AI connection --------------------------------------------------------------------

@access()
@require_http_methods(["GET"])
def vision_templates(request: HttpRequest) -> JsonResponse:
    return _no_store(JsonResponse({"templates": catalog.all_public()}))


def _draft_connection(request: HttpRequest, payload: dict) -> client.Connection:
    """The connection described in a form that has not been saved yet (a saved key is reused when none is typed)."""
    base_url = client.validate_base_url(str(payload.get("base_url", "")))
    model = str(payload.get("model", "")).strip()[:160]
    if not model:
        raise client.VisionError("Enter the model name, for example gpt-4o-mini or qwen2.5vl:7b.", "invalid")
    key = str(payload.get("api_key", "")).strip()
    if not key:
        row = AIConnection.objects.filter(user=request.user).first()
        key = (secrets.decrypt(row.api_key_encrypted) or "") if row else ""
    return client.Connection(source="own", label=str(payload.get("label", "")).strip()[:80] or "Your own AI",
                             base_url=base_url, model=model, api_key=key)


@access(PUT="view", DELETE="view")
@require_http_methods(["GET", "PUT", "DELETE"])
def vision_connection(request: HttpRequest) -> JsonResponse:
    if request.method == "DELETE":
        AIConnection.objects.filter(user=request.user).delete()
        return _no_store(JsonResponse(service.settings_payload(request.user)))
    if request.method == "PUT":
        try:
            payload = _json_body(request)
        except ValueError as error:
            return _error(str(error))
        unknown = payload.keys() - {"use_own", "label", "base_url", "model", "api_key", "clear_key"}
        if unknown:
            return _error("Only the AI model settings can be changed here.")
        use_own = payload.get("use_own") is True
        row = AIConnection.objects.filter(user=request.user).first() or AIConnection(user=request.user)
        base_url = str(payload.get("base_url", row.base_url) or "").strip()
        model = str(payload.get("model", row.model) or "").strip()[:160]
        if base_url:
            try:
                base_url = client.validate_base_url(base_url)
            except client.VisionError as error:
                return _error(error.message)
        if use_own and (not base_url or not model):
            return _error("To use your own AI, enter its address and model name.")
        row.use_own = use_own
        row.base_url = base_url
        row.model = model
        row.label = str(payload.get("label", row.label) or "").strip()[:80]
        new_key = str(payload.get("api_key", "") or "").strip()
        if payload.get("clear_key") is True:
            row.api_key_encrypted, row.key_hint = "", ""
        elif new_key:
            if len(new_key) > 400:
                return _error("That API key is too long.")
            row.api_key_encrypted, row.key_hint = secrets.encrypt(new_key), secrets.hint(new_key)
        row.updated_at = timezone.now()
        row.save()
        cache.delete(f"vision-status:{request.user.pk}")
    return _no_store(JsonResponse(service.settings_payload(request.user)))


@access(POST="view")
@require_http_methods(["POST"])
def vision_connection_test(request: HttpRequest) -> JsonResponse:
    try:
        payload = _json_body(request)
    except ValueError as error:
        return _error(str(error))
    target = payload.get("target", "active")
    try:
        if target == "builtin":
            conn = service.builtin_connection()
        elif target == "draft":
            conn = _draft_connection(request, payload)
        else:
            conn = service.active_connection(request.user)
        return _no_store(JsonResponse(client.test_connection(conn)))
    except client.VisionError as error:
        return _no_store(JsonResponse({"ok": False, "message": error.message, "kind": error.kind}))


@access()
@require_http_methods(["GET"])
def vision_status(request: HttpRequest) -> JsonResponse:
    """The model this person's next inspection will use, and whether it answers right now (cached briefly)."""
    conn = service.active_connection(request.user)
    key = f"vision-status:{request.user.pk}"
    cached = cache.get(key)
    if cached is None or cached.get("base_url") != conn.base_url:
        cached = {"base_url": conn.base_url, "reachable": client.ping(conn)}
        cache.set(key, cached, 30)
    return _no_store(JsonResponse({"active": service.describe_connection(conn), "reachable": cached["reachable"]}))


# --- inspections ----------------------------------------------------------------------------------------

NOT_CONNECTED = ("Connect an AI model first. Open the AI model page to add your organisation's Qwen server "
                 "or another OpenAI-compatible AI; it takes about a minute.")


def _create_inspection(request: HttpRequest) -> JsonResponse:
    if not service.active_connection(request.user).configured:
        return _error(NOT_CONNECTED, 409, kind="not_configured")
    template_key = request.POST.get("template", catalog.CUSTOM)
    if template_key not in catalog.BY_KEY:
        return _error("Choose one of the inspection types.")
    template = catalog.BY_KEY[template_key]
    instructions = (request.POST.get("instructions") or "").strip()
    if len(instructions) > MAX_INSTRUCTIONS:
        return _error(f"Keep the description under {MAX_INSTRUCTIONS} characters.")
    if template_key == catalog.CUSTOM and len(instructions) < 4:
        return _error("Describe what you want Athena to look for.")
    source = "video" if request.POST.get("source") == "video" else "photo"
    try:
        frame_times = json.loads(request.POST.get("frame_times") or "[]")
        frame_times = [float(t) if isinstance(t, (int, float)) else None for t in frame_times] if isinstance(frame_times, list) else []
    except (json.JSONDecodeError, TypeError, ValueError):
        frame_times = []
    uploads = request.FILES.getlist("images")
    if not uploads:
        return _error("Add at least one picture.")
    if len(uploads) > MAX_PICTURES:
        return _error(f"Add no more than {MAX_PICTURES} pictures at a time.")
    if sum(upload.size for upload in uploads) > MAX_BATCH_BYTES:
        return _error("The pictures are too large together. Keep them under 60 MB in total.")
    validated = []
    for upload in uploads:
        try:
            validated.append((upload, _validate_uploaded_image(upload)))
        except ValueError as error:
            return _error(f"“{_safe_filename(upload.name)}”: {error} Nothing was added; take that file out and try again.")
    title = (request.POST.get("title") or "").strip()[:120]
    if not title:
        title = template["name"] if template_key != catalog.CUSTOM else (instructions[:80] + ("…" if len(instructions) > 80 else ""))
    saved_keys: list[str] = []
    try:
        with transaction.atomic():
            inspection = Inspection.objects.create(
                created_by=request.user, template=template_key, title=title, instructions=instructions, source=source,
            )
            for position, (upload, meta) in enumerate(validated, 1):
                key = default_storage.save(f"inspections/{inspection.id}/{uuid.uuid4()}.{meta['extension']}", upload)
                saved_keys.append(key)
                InspectionImage.objects.create(
                    inspection=inspection, position=position, filename=meta["filename"], storage_key=key,
                    content_type=meta["content_type"], sha256=meta["sha256"], size_bytes=meta["size_bytes"],
                    width=meta["width"], height=meta["height"],
                    frame_time=frame_times[position - 1] if source == "video" and position - 1 < len(frame_times) else None,
                )
    except Exception:
        for key in saved_keys:
            default_storage.delete(key)
        raise
    transaction.on_commit(lambda: service.start(inspection))
    return _no_store(JsonResponse({"inspection": _inspection_data(inspection)}, status=201))


@access(POST="label")
@require_http_methods(["GET", "POST"])
def inspection_collection(request: HttpRequest) -> JsonResponse:
    if request.method == "POST":
        return _create_inspection(request)
    service.expire_lost_runs(_mine(request))
    rows = _mine(request).prefetch_related("images")
    verdict = request.GET.get("verdict", "")
    if verdict in ("clear", "attention", "critical"):
        rows = rows.filter(verdict=verdict, status=Inspection.STATUS_DONE)
    elif verdict == "failed":
        rows = rows.filter(status=Inspection.STATUS_FAILED)
    template = request.GET.get("template", "")
    if template in catalog.BY_KEY:
        rows = rows.filter(template=template)
    query = (request.GET.get("q") or "").strip()[:80]
    if query:
        rows = rows.filter(Q(title__icontains=query) | Q(instructions__icontains=query))
    try:
        limit = max(1, min(200, int(request.GET.get("limit", "100"))))
    except ValueError:
        limit = 100
    return _no_store(JsonResponse({"inspections": [_inspection_data(row, full=False) for row in rows[:limit]]}))


@access()
@require_http_methods(["GET"])
def inspection_summary(request: HttpRequest) -> JsonResponse:
    service.expire_lost_runs(_mine(request))
    mine = _mine(request)
    week = timezone.now() - timedelta(days=7)
    totals = mine.aggregate(
        total=Count("id"),
        this_week=Count("id", filter=Q(created_at__gte=week)),
        attention=Count("id", filter=Q(status=Inspection.STATUS_DONE, verdict="attention")),
        critical=Count("id", filter=Q(status=Inspection.STATUS_DONE, verdict="critical")),
        clear=Count("id", filter=Q(status=Inspection.STATUS_DONE, verdict="clear")),
        running=Count("id", filter=Q(status__in=Inspection.ACTIVE_STATUSES)),
    )
    recent = [_inspection_data(row, full=False) for row in mine.prefetch_related("images")[:6]]
    return _no_store(JsonResponse({**totals, "recent": recent}))


@access(DELETE="label")
@require_http_methods(["GET", "DELETE"])
def inspection_detail(request: HttpRequest, inspection_id) -> JsonResponse:
    service.expire_lost_runs(_mine(request).filter(id=inspection_id))
    inspection = get_object_or_404(_mine(request).prefetch_related("images"), id=inspection_id)
    if request.method == "DELETE":
        keys = [image.storage_key for image in inspection.images.all()]
        inspection.delete()
        for key in keys:
            default_storage.delete(key)
        return _no_store(JsonResponse({"deleted": True}))
    return _no_store(JsonResponse({"inspection": _inspection_data(inspection)}))


@access(POST="label")
@require_http_methods(["POST"])
def inspection_rerun(request: HttpRequest, inspection_id) -> JsonResponse:
    inspection = get_object_or_404(_mine(request), id=inspection_id)
    if inspection.status in Inspection.ACTIVE_STATUSES:
        return _error("Athena is still looking at these pictures. Wait for the answer first.", 409)
    if not service.active_connection(request.user).configured:
        return _error(NOT_CONNECTED, 409, kind="not_configured")
    Inspection.objects.filter(id=inspection.id).update(
        status=Inspection.STATUS_QUEUED, verdict="", result={}, error="", feedback="", feedback_note="",
        started_at=timezone.now(), finished_at=None, duration_ms=None,
    )
    inspection.refresh_from_db()
    service.start(inspection)
    return _no_store(JsonResponse({"inspection": _inspection_data(inspection)}))


@access(POST="label")
@require_http_methods(["POST"])
def inspection_feedback(request: HttpRequest, inspection_id) -> JsonResponse:
    inspection = get_object_or_404(_mine(request), id=inspection_id)
    try:
        payload = _json_body(request)
    except ValueError as error:
        return _error(str(error))
    rating = payload.get("rating", "")
    if rating not in ("right", "wrong", ""):
        return _error("Say whether the answer was right or wrong.")
    if inspection.status != Inspection.STATUS_DONE:
        return _error("You can rate an answer once Athena has finished.", 409)
    note = str(payload.get("note", "") or "").strip()[:1000]
    Inspection.objects.filter(id=inspection.id).update(feedback=rating, feedback_note=note)
    inspection.refresh_from_db()
    return _no_store(JsonResponse({"inspection": _inspection_data(inspection)}))


@access()
@require_http_methods(["GET"])
def inspection_image(request: HttpRequest, inspection_id, position: int) -> FileResponse:
    image = get_object_or_404(InspectionImage, inspection__created_by=request.user, inspection_id=inspection_id, position=position)
    try:
        response = FileResponse(default_storage.open(image.storage_key, "rb"), content_type=image.content_type)
    except FileNotFoundError as error:
        raise Http404("Picture not found.") from error
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, max-age=3600"
    return response
