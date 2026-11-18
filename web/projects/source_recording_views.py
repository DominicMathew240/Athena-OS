"""Durable recorded-video imports for one operation source (T09).

A source recording is source material, not a draft test clip: the whole file is
kept privately with its checksum, dimensions, duration, the container's own
creation time and the ingestion moment. Failed checks are reported at import
time; a rejected file is never stored. Nothing extracts stills — the recording
is preserved as-is, and producing events from it is later, separate work.
"""

from __future__ import annotations

import re
import uuid

from django.core.files.storage import default_storage
from django.http import FileResponse, Http404, HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods

from .models import OperationSource, OperationSourceRecording
from .operations import recordings
from .permissions import access
from .views import _safe_filename
from .vision_views import _error, _no_store


def _recording_data(row: OperationSourceRecording) -> dict:
    return {
        "id": str(row.id),
        "source_id": str(row.source_id),
        "filename": row.filename,
        "sha256": row.sha256,
        "size_bytes": row.size_bytes,
        "width": row.width,
        "height": row.height,
        "duration_seconds": row.duration_seconds,
        # The recording's own clock when the container carries it; null when absent.
        "captured_at": row.captured_at.isoformat() if row.captured_at else None,
        # When Athena ingested the file; both clocks stay visible so a
        # mislabelled recording can be told apart from a late import.
        "ingested_at": row.created_at.isoformat(),
        "content_url": f"/api/operation-sources/{row.source_id}/recordings/{row.id}/content",
    }


def _owned_source(source_id, user):
    return get_object_or_404(OperationSource, id=source_id, site__created_by=user)


@access(POST="label", GET="view")
@require_http_methods(["GET", "POST"])
def source_recordings(request, source_id):
    source = _owned_source(source_id, request.user)
    if request.method == "GET":
        rows = source.recordings.all()
        return _no_store(JsonResponse({"recordings": [_recording_data(row) for row in rows]}))
    if source.kind != "recorded_video":
        return _error("Only recorded-video sources can hold recordings.")
    uploads = request.FILES.getlist("video")
    if len(uploads) != 1 or len(request.FILES) != 1:
        return _error("Choose one MP4 recording.")
    upload = uploads[0]
    try:
        metadata = recordings.inspect_upload(upload)
    except recordings.RecordingError as error:
        return _error(str(error), 503 if error.unavailable else 400)
    key = default_storage.save(f"operation-source-recordings/{source_id}/{uuid.uuid4()}.mp4", upload)
    try:
        row = OperationSourceRecording.objects.create(
            source=source, created_by=request.user, storage_key=key,
            filename=_safe_filename(upload.name), **metadata,
        )
    except Exception:
        recordings.queue_cleanup(key)
        raise
    return _no_store(JsonResponse({"recording": _recording_data(row)}, status=201))


@access(GET="view")
@require_http_methods(["GET"])
def source_recording_content(request, source_id, recording_id):
    video = get_object_or_404(
        OperationSourceRecording, id=recording_id, source_id=source_id, source__site__created_by=request.user
    )
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
