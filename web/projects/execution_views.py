"""Owner-scoped API for operation executions (T19): queue a run, cancel it, read it.

The web process never executes anything. POST only freezes provenance onto a
queued ``OperationExecution`` row and returns 202; worker processes (see
``projects.operations.executions``) claim and run it. Existing draft, bundle,
and recording routes are unchanged.
"""

from __future__ import annotations

import uuid

from django.http import HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods

from .models import OperationExecution, OperationReleaseBundle, OperationSourceRecording
from .operation_views import _error, _json_body, _no_store
from .permissions import access


def _execution_data(row):
    return {
        "id": str(row.id),
        "bundle_id": str(row.bundle_id),
        "recording_id": str(row.recording_id),
        "bundle_sha256": row.bundle_sha256,
        "recording_sha256": row.recording_sha256,
        "status": row.status,
        "cancel_requested": row.cancel_requested,
        "result": row.result,
        "error": row.error,
        "created_at": row.created_at.isoformat(),
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }


@access(POST="label")
@require_http_methods(["GET", "POST"])
def bundle_executions(request: HttpRequest, bundle_id):
    """List a bundle's executions, or queue a new one against one of its source recordings."""
    bundle = get_object_or_404(
        OperationReleaseBundle.objects.select_related("operation", "draft"),
        id=bundle_id,
        operation__created_by=request.user,
    )
    if request.method == "GET":
        rows = bundle.executions.order_by("-created_at", "-id")
        return _no_store(JsonResponse({"executions": [_execution_data(row) for row in rows]}))
    try:
        payload = _json_body(request)
        if payload.keys() != {"recording_id"}:
            raise ValueError("Provide only the source recording to run the bundle against.")
        try:
            recording_id = uuid.UUID(payload["recording_id"])
        except (ValueError, AttributeError, TypeError) as error:
            raise ValueError("Provide a valid source recording.") from error
    except ValueError as error:
        return _error(str(error))
    operation = bundle.operation
    recording = OperationSourceRecording.objects.filter(id=recording_id, source=operation.source).first()
    if recording is None:
        return _error("Choose a recording that belongs to this bundle's source.")
    snapshot = bundle.snapshot or {}
    pins = snapshot.get("component_pins") or {}
    row = OperationExecution.objects.create(
        bundle=bundle,
        recording=recording,
        created_by=request.user,
        bundle_sha256=bundle.snapshot_sha256,
        recording_sha256=recording.sha256,
        pins=pins,
    )
    return _no_store(JsonResponse({"execution": _execution_data(row)}, status=202))


@access()
@require_http_methods(["GET"])
def execution_detail(request: HttpRequest, execution_id):
    row = get_object_or_404(
        OperationExecution.objects.select_related("bundle"), id=execution_id, created_by=request.user
    )
    return _no_store(JsonResponse({"execution": _execution_data(row)}))


@access(POST="label")
@require_http_methods(["POST"])
def execution_cancel(request: HttpRequest, execution_id):
    """Ask a queued or running execution to stop. The worker confirms at its next checkpoint."""
    row = get_object_or_404(OperationExecution, id=execution_id, created_by=request.user)
    if row.status in (OperationExecution.SUCCEEDED, OperationExecution.FAILED, OperationExecution.CANCELLED):
        return _error("This run already finished, so it can no longer be cancelled.", 409)
    OperationExecution.objects.filter(pk=row.pk).update(cancel_requested=True)
    row.refresh_from_db()
    return _no_store(JsonResponse({"execution": _execution_data(row)}))
