"""JSON API for dataset versions: readiness, create/list, detail and zip export."""

from __future__ import annotations

import json
from typing import Any

from django.core.exceptions import RequestDataTooBig
from django.http import HttpRequest, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods

from .datasets import FORMATS, export_max_bytes, manifest as manifest_lib, service, splits, writers
from .models import DatasetVersion, ExportEvent, Project
from .permissions import access

MAX_REQUEST_BYTES = 16_384
EXPORT_LOG_LIMIT = 200


def _no_store(response):
    response["Cache-Control"] = "no-store"
    return response


def _image_project(project_id) -> Project:
    return get_object_or_404(Project, id=project_id, modality="image")


def _json_payload(request: HttpRequest) -> dict[str, Any]:
    """Parse an optional JSON object body. Raises ``_HttpError`` with the status to return."""
    if request.content_type != "application/json":
        raise _HttpError("Send the request as JSON.", 415)
    try:
        body = request.body
    except RequestDataTooBig as error:
        raise _HttpError("Request is too large.", 413) from error
    if len(body) > MAX_REQUEST_BYTES:
        raise _HttpError("Request is too large.", 413)
    try:
        payload = json.loads(body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise _HttpError("Request body must be valid JSON.", 400) from error
    if not isinstance(payload, dict):
        raise _HttpError("Request body must be a JSON object.", 400)
    return payload


class _HttpError(Exception):
    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


def _config_from_query(query) -> splits.SplitConfig:
    payload: dict[str, Any] = {}
    try:
        if "seed" in query:
            payload["seed"] = int(query["seed"])
        names = [name for name in splits.SPLIT_NAMES if name in query]
        if names:
            payload["split"] = {name: float(query[name]) for name in names}
    except ValueError as error:
        raise ValueError("seed must be a whole number and split fractions must be numbers.") from error
    return splits.SplitConfig.from_payload(payload)


def _problems(problems, limit=service.PROBLEM_LIMIT) -> dict[str, Any]:
    return {
        "problems": [problem.as_dict() for problem in problems[:limit]],
        "problem_count": len(problems),
        "problems_truncated": len(problems) > limit,
    }


def dataset_data(version: DatasetVersion, *, include_manifest: bool) -> dict[str, Any]:
    data = {
        "id": str(version.id),
        "version": version.version,
        "project_id": str(version.project_id),
        "status": version.status,
        "created_at": version.created_at.isoformat(),
        "created_by": manifest_lib.opaque_user(version.created_by_id),
        "label_names": version.label_names,
        "counts": {"images": version.image_count, "boxes": version.box_count},
        "excluded": {
            "suggested_boxes": version.excluded_suggested_count,
            "images_without_accepted_boxes": version.excluded_image_count,
            "reason": manifest_lib.EXCLUDED_REASON,
        },
        "split_config": version.split_config,
        "split_algorithm": splits.algorithm_of(version.split_config),
        "manifest_sha256": version.manifest_sha256,
        "export_formats": list(FORMATS),
        "is_incomplete": version.is_incomplete,
        "incomplete_reason": version.incomplete_reason,
    }
    if version.is_incomplete:
        data["missing_pictures"] = version.images.filter(asset__isnull=True).count()
    if include_manifest:
        data["manifest"] = version.manifest
    return data


@access(POST="label")
@require_http_methods(["GET", "POST"])
def project_datasets(request: HttpRequest, project_id) -> JsonResponse:
    project = _image_project(project_id)
    if request.method == "GET":
        versions = project.dataset_versions.all()
        return _no_store(
            JsonResponse({"datasets": [dataset_data(v, include_manifest=False) for v in versions]})
        )

    try:
        config = splits.SplitConfig.from_payload(_json_payload(request))
    except _HttpError as error:
        return JsonResponse({"error": str(error)}, status=error.status)
    except ValueError as error:
        return JsonResponse({"error": str(error)}, status=400)

    analysis = service.analyse(project, config)
    if not analysis.accepted:
        return JsonResponse(
            {"error": service.NO_ACCEPTED_MESSAGE, "excluded": analysis.excluded}, status=400
        )
    if not analysis.ready:
        return JsonResponse(
            {
                "error": "Fix the problems below before creating a dataset version. Nothing was created.",
                "blockers": analysis.blockers,
                "excluded": analysis.excluded,
                **_problems(analysis.problems),
            },
            status=422,
        )
    try:
        version = service.create_version(analysis, request.user)
    except service.VersionConflict as error:
        return JsonResponse({"error": str(error)}, status=409)
    return _no_store(JsonResponse({"dataset": dataset_data(version, include_manifest=True)}, status=201))


@access()
@require_http_methods(["GET"])
def project_dataset_readiness(request: HttpRequest, project_id) -> JsonResponse:
    """Explain what would be exported and what blocks it. Creates nothing."""
    project = _image_project(project_id)
    try:
        config = _config_from_query(request.GET)
    except ValueError as error:
        return JsonResponse({"error": str(error)}, status=400)
    # Readiness is polled; let it use the stat-keyed cache. Version creation never does.
    analysis = service.analyse(project, config, use_cache=True)
    return _no_store(
        JsonResponse(
            {
                "ready": analysis.ready,
                "blockers": analysis.blockers,
                "counts": {
                    "images_total": analysis.images_total,
                    "images": len(analysis.assets),
                    "accepted_boxes": len(analysis.accepted),
                    "suggested_boxes": analysis.suggested_boxes,
                },
                "excluded": analysis.excluded,
                "split_config": {**config.as_dict(), "split_algorithm": splits.ALGORITHM},
                "label_names": project.labels,
                "preview": service.preview(analysis),
                **_problems(analysis.problems),
            }
        )
    )


@access()
@require_http_methods(["GET"])
def project_dataset_detail(request: HttpRequest, project_id, dataset_id) -> JsonResponse:
    version = get_object_or_404(DatasetVersion, id=dataset_id, project_id=project_id, project__modality="image")
    return _no_store(JsonResponse({"dataset": dataset_data(version, include_manifest=True)}))


def _record_export(request, version, fmt, *, success, status, sent=0, error=""):
    user = request.user if request.user.is_authenticated else None
    ExportEvent.objects.create(
        version=version,
        user=user,
        user_id_snapshot=user.pk if user else None,
        format=str(fmt)[:16],
        bytes_sent=sent,
        success=success,
        status_code=status,
        error=error[:300],
    )


def _audited(chunks, request, version, fmt):
    """Pass chunks through and write exactly one ExportEvent when the download ends."""
    sent = 0
    try:
        for chunk in chunks:
            sent += len(chunk)
            yield chunk
    except GeneratorExit:
        _record_export(request, version, fmt, success=False, status=200, sent=sent,
                       error="The download stopped before the whole archive was sent.")
        raise
    except Exception as error:
        _record_export(request, version, fmt, success=False, status=500, sent=sent,
                       error=f"The archive could not be completed: {error}")
        raise
    _record_export(request, version, fmt, success=True, status=200, sent=sent)


@access(GET="label")
@require_http_methods(["GET"])
def project_dataset_export(request: HttpRequest, project_id, dataset_id):
    version = get_object_or_404(
        DatasetVersion.objects.select_related("project"),
        id=dataset_id,
        project_id=project_id,
        project__modality="image",
    )
    fmt = request.GET.get("format", "")
    response = _export(request, version, fmt)
    if not response.streaming:
        message = ""
        try:
            message = json.loads(response.content).get("error", "")
        except (ValueError, AttributeError):
            pass
        _record_export(request, version, fmt, success=False, status=response.status_code, error=str(message))
    return response


@access()
@require_http_methods(["GET"])
def project_dataset_exports(request: HttpRequest, project_id, dataset_id) -> JsonResponse:
    """Audit trail of export attempts for one version, newest first (append-only)."""
    version = get_object_or_404(DatasetVersion, id=dataset_id, project_id=project_id, project__modality="image")
    events = version.export_events.all()
    return _no_store(
        JsonResponse(
            {
                "exports": [
                    {
                        "id": event.id,
                        "format": event.format,
                        "bytes": event.bytes_sent,
                        "success": event.success,
                        "status": event.status_code,
                        "error": event.error,
                        "user": manifest_lib.opaque_user(event.user_id_snapshot),
                        "created_at": event.created_at.isoformat(),
                    }
                    for event in events[:EXPORT_LOG_LIMIT]
                ],
                "total": events.count(),
            }
        )
    )


def _export(request: HttpRequest, version: DatasetVersion, fmt: str):
    if fmt not in FORMATS:
        return JsonResponse({"error": f"format must be one of: {', '.join(FORMATS)}."}, status=400)
    try:
        images = service.verify_integrity(version)
    except ValueError as error:
        return JsonResponse({"error": str(error)}, status=409)

    if version.is_incomplete:
        missing = version.images.filter(asset__isnull=True).count()
        return JsonResponse(
            {
                "error": (
                    f"This dataset version is incomplete and cannot be exported: {missing} of "
                    f"{version.image_count} picture(s) were permanently deleted from the project after it was created. "
                    "Its manifest and checksum are still intact; create a new version to export current data."
                ),
                "incomplete": True,
                "missing_pictures": missing,
                "reason": version.incomplete_reason,
                "manifest_verified": True,
            },
            status=409,
        )
    total = sum(image["size_bytes"] for image in images)
    if total > export_max_bytes():
        return JsonResponse(
            {"error": f"This export would hold {total} bytes of images; the limit is {export_max_bytes()}."},
            status=413,
        )
    problems = service.file_problems(version)  # full hash of every file, never cached
    if problems:
        return JsonResponse(
            {
                "error": "Some images in this version are missing or changed on disk, so it cannot be exported.",
                **_problems(problems),
            },
            status=409,
        )

    storage_keys = {str(row.asset_uuid): row.storage_key for row in version.images.all()}
    files = writers.build_files(fmt, version.manifest, version.manifest_sha256, storage_keys)
    response = StreamingHttpResponse(
        _audited(writers.stream_zip(fmt, version.manifest, version.manifest_sha256, files), request, version, fmt),
        content_type="application/zip",
    )
    filename = f"athena-{str(version.project_id)[:8]}-v{version.version}-{fmt}.zip"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["X-Content-Type-Options"] = "nosniff"
    return _no_store(response)
