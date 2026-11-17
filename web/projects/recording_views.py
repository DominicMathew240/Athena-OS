"""Private, bounded recorded-video intake for an operation draft."""

from __future__ import annotations

import re
import uuid

from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import F
from django.http import FileResponse, Http404, HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .models import OperationDraft, OperationRecording
from .operation_views import _data, _record_draft_version
from .operations import recordings
from .permissions import access
from .views import _safe_filename
from .vision_views import _error, _json_body, _no_store


def _revision(value):
    if not isinstance(value, str) or not value.isascii() or not value.isdigit() or len(value) > 9 or int(value) < 1:
        raise ValueError("Provide the current draft revision.")
    return int(value)


@access(POST="label", DELETE="label")
@require_http_methods(["POST", "DELETE"])
def operation_recording(request, draft_id):
    rows = OperationDraft.objects.filter(id=draft_id, created_by=request.user)
    draft = get_object_or_404(rows)
    try:
        if request.method == "POST":
            raw_revision = request.POST.get("revision")
        elif request.content_type == "application/json":
            payload = _json_body(request)
            if set(payload) != {"revision"} or type(payload["revision"]) is not int:
                raise ValueError("Provide the current draft revision.")
            raw_revision = str(payload["revision"])
        else:
            raw_revision = request.headers.get("X-Draft-Revision")
        revision = _revision(raw_revision)
    except ValueError as error:
        return _error(str(error))
    if revision != draft.revision:
        return _error("This draft changed elsewhere. Reload it before saving.", 409)
    if request.method == "DELETE":
        with transaction.atomic():
            changed = rows.filter(revision=revision, recording__isnull=False).update(
                revision=F("revision") + 1,
                updated_at=timezone.now(),
                line=None,
                reference_events=[],
                geometry_version=None,
            )
            if not changed:
                return _error("The recording or draft changed elsewhere. Reload it before continuing.", 409)
            OperationRecording.objects.filter(draft_id=draft_id).delete()
            _record_draft_version(rows.get())
        return _no_store(JsonResponse({"draft": _data(rows.get())}))
    if OperationRecording.objects.filter(draft=draft).exists():
        return _error("Remove the current recording before adding another.", 409)
    uploads = request.FILES.getlist("video")
    if len(uploads) != 1 or len(request.FILES) != 1:
        return _error("Choose one MP4 recording.")
    upload = uploads[0]
    try:
        metadata = recordings.inspect_upload(upload)
    except recordings.RecordingError as error:
        return _error(str(error), 503 if error.unavailable else 400)
    key = default_storage.save(f"operation-recordings/{draft_id}/{uuid.uuid4()}.mp4", upload)
    try:
        with transaction.atomic():
            changed = rows.filter(revision=revision, recording__isnull=True).update(
                revision=F("revision") + 1, updated_at=timezone.now(), line=None, geometry_version=None
            )
            if changed:
                OperationRecording.objects.create(
                    draft_id=draft_id, storage_key=key, filename=_safe_filename(upload.name), **metadata
                )
                _record_draft_version(rows.get())
        if not changed:
            recordings.queue_cleanup(key)
            return _error("This draft changed elsewhere. Reload it before saving.", 409)
    except Exception:
        recordings.queue_cleanup(key)
        raise
    return _no_store(JsonResponse({"draft": _data(rows.get())}, status=201))


def _bounded_chunks(file, length):
    try:
        while length:
            chunk = file.read(min(length, 64 * 1024))
            if not chunk:
                break
            length -= len(chunk)
            yield chunk
    finally:
        file.close()


def _invalid_range(file, size):
    file.close()
    response = HttpResponse(status=416)
    response["Content-Range"] = f"bytes */{size}"
    response["Cache-Control"] = "no-store"
    return response


@access()
@require_http_methods(["GET"])
def operation_recording_content(request, draft_id):
    video = get_object_or_404(OperationRecording, draft_id=draft_id, draft__created_by=request.user)
    try:
        file = default_storage.open(video.storage_key, "rb")
    except FileNotFoundError as error:
        raise Http404("Recording not found.") from error
    range_header = request.headers.get("Range")
    if not range_header:
        response = FileResponse(file, content_type="video/mp4")
    else:
        match = re.fullmatch(r"bytes=(\d{0,19})-(\d{0,19})", range_header)
        if not match or not any(match.groups()):
            return _invalid_range(file, video.size_bytes)
        left, right = match.groups()
        if left:
            start = int(left)
            end = min(int(right), video.size_bytes - 1) if right else video.size_bytes - 1
        else:
            suffix = int(right)
            start = max(0, video.size_bytes - suffix)
            end = video.size_bytes - 1
        if start >= video.size_bytes or start > end:
            return _invalid_range(file, video.size_bytes)
        file.seek(start)
        response = StreamingHttpResponse(_bounded_chunks(file, end - start + 1), status=206, content_type="video/mp4")
        response._resource_closers.append(file.close)
        response["Content-Length"] = str(end - start + 1)
        response["Content-Range"] = f"bytes {start}-{end}/{video.size_bytes}"
    response["Accept-Ranges"] = "bytes"
    response["Cache-Control"] = "no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response
