"""HTTP surface for T10 live RTSP connect + installer preview."""
from __future__ import annotations

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods

from . import credentials as credential_store
from . import live_rtsp
from .models import OperationSource
from .permissions import access
from .vision_views import _error, _json_body, _no_store


def _owned_source(source_id, user):
    return get_object_or_404(OperationSource, id=source_id, site__created_by=user)


def _error_with_category(error: live_rtsp.LiveRtspError, *, status: int = 400):
    response = JsonResponse({"error": str(error), "category": error.category}, status=status)
    response["Cache-Control"] = "no-store"
    return response


@access()
@require_http_methods(["GET"])
def live_rtsp_support(request):
    """What this build supports for live RTSP — no secrets, no host list contents."""
    return _no_store(JsonResponse({"live_rtsp": live_rtsp.supported_live_rtsp_summary()}))


@access(POST="label")
@require_http_methods(["POST"])
def source_live_rtsp_preview(request, source_id):
    """Connect to an approved RTSP URL for one camera source and return a preview frame.

    Body: ``{"url": "rtsp://host/path", "username": "optional"}``. Password comes
    only from the source's stored credential when one is set. Categories on
    failure: invalid_url, not_approved, credential, network, unsupported_codec,
    unavailable, source_kind.
    """
    source = _owned_source(source_id, request.user)
    if source.kind != "camera":
        return _error_with_category(
            live_rtsp.LiveRtspError(
                "Live RTSP preview is only available on a camera source.",
                category=live_rtsp.CATEGORY_SOURCE_KIND,
            )
        )

    try:
        payload = _json_body(request)
    except ValueError as error:
        return _error(str(error))

    if not isinstance(payload, dict):
        return _error("Provide a JSON object with the RTSP URL.")
    allowed = {"url", "username", "include_preview"}
    extra = set(payload.keys()) - allowed
    if extra:
        return _error("Provide only the RTSP URL and optional username.")
    url = payload.get("url")
    username = payload.get("username")
    if username is not None and not isinstance(username, str):
        return _error("Username must be text when provided.")
    if username is not None and len(username) > 128:
        return _error("Username is too long.")
    include_preview = payload.get("include_preview", True)
    if not isinstance(include_preview, bool):
        return _error("include_preview must be true or false when provided.")

    password = None
    row = getattr(source, "credential", None)
    if row is not None:
        password = credential_store.reveal(row.secret_encrypted)
        if password is None:
            return _error_with_category(
                live_rtsp.LiveRtspError(
                    "The stored camera password could not be read. Set the source credential again.",
                    category=live_rtsp.CATEGORY_CREDENTIAL,
                )
            )

    try:
        result = live_rtsp.probe_rtsp(
            url=url if isinstance(url, str) else "",
            username=username,
            password=password,
            include_preview=include_preview,
        )
    except live_rtsp.LiveRtspError as error:
        status = 503 if error.category == live_rtsp.CATEGORY_UNAVAILABLE else 400
        return _error_with_category(error, status=status)

    body = {
        "ok": True,
        "category": result["category"],
        "message": result["message"],
        "source_id": str(source.id),
        "stream": result["stream"],
        "preview_jpeg_base64": result["preview_jpeg_base64"],
        "credential_used": row is not None,
    }
    return _no_store(JsonResponse(body))
