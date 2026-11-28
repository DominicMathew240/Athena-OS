"""Owner-scoped API for operation learning examples (T33)."""

from __future__ import annotations

import uuid

from django.http import HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods

from .models import Operation, OperationExecution, OperationLearningExample, OperationSourceRecording
from .operation_views import _error, _json_body, _no_store
from .operations import examples as learning_examples
from .permissions import access


def _owner_operation(request, operation_id) -> Operation:
    return get_object_or_404(Operation, id=operation_id, created_by=request.user)


@access()
@require_http_methods(["GET", "POST"])
def operation_learning_examples(request: HttpRequest, operation_id):
    """List or create learning examples for one owned operation."""
    operation = _owner_operation(request, operation_id)
    if request.method == "GET":
        status = (request.GET.get("status") or "").strip() or None
        polarity = (request.GET.get("polarity") or "").strip() or None
        rows = learning_examples.list_examples(operation=operation, status=status, polarity=polarity)
        return _no_store(
            JsonResponse(
                {
                    "schema": "t33.learning_examples.v1",
                    "examples": [learning_examples.serialize_example(r) for r in rows],
                    "count": len(rows),
                }
            )
        )

    try:
        payload = _json_body(request)
        if not isinstance(payload, dict):
            raise ValueError("Send a JSON object.")
        time_pts = float(payload["time_pts"])
        polarity = str(payload.get("polarity") or "")
        selection_kind = str(payload.get("selection_kind") or OperationLearningExample.KIND_OPERATOR)
        # Prefer verified_negative when polarity is negative and caller asks for it.
        if payload.get("verified_negative") is True:
            selection_kind = OperationLearningExample.KIND_VERIFIED_NEGATIVE
            polarity = OperationLearningExample.POLARITY_NEGATIVE
        recording = None
        recording_id = payload.get("recording_id")
        if recording_id:
            try:
                rid = uuid.UUID(str(recording_id))
            except (ValueError, AttributeError, TypeError) as error:
                raise ValueError("Provide a valid source recording.") from error
            recording = OperationSourceRecording.objects.filter(id=rid, source=operation.source).first()
            if recording is None:
                raise ValueError("Choose a recording that belongs to this operation's source.")
        execution = None
        if payload.get("execution_id"):
            try:
                eid = uuid.UUID(str(payload["execution_id"]))
            except (ValueError, AttributeError, TypeError) as error:
                raise ValueError("Provide a valid operation run.") from error
            execution = OperationExecution.objects.filter(
                id=eid, bundle__operation=operation, created_by=request.user
            ).first()
            if execution is None:
                raise ValueError("That operation run was not found.")
        row, created = learning_examples.add_example(
            operation=operation,
            user=request.user,
            time_pts=time_pts,
            polarity=polarity,
            selection_kind=selection_kind,
            recording=recording,
            recording_sha256=(payload.get("recording_sha256") or None),
            execution=execution,
            event_identity=payload.get("event_identity") if isinstance(payload.get("event_identity"), dict) else {},
            evidence=payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {},
            note=str(payload.get("note") or ""),
        )
    except KeyError:
        return _error("Provide time_pts and polarity.")
    except (TypeError, ValueError) as error:
        return _error(str(error) or "Could not save that example.")

    return _no_store(
        JsonResponse(
            {"example": learning_examples.serialize_example(row), "created": created},
            status=201 if created else 200,
        )
    )


@access(POST="label")
@require_http_methods(["POST"])
def execution_capture_examples(request: HttpRequest, execution_id):
    """Capture diagnosis-linked example candidates from one owned succeeded run."""
    execution = get_object_or_404(
        OperationExecution.objects.select_related("bundle__operation__site", "recording"),
        id=execution_id,
        created_by=request.user,
    )
    try:
        result = learning_examples.capture_from_execution(execution=execution, user=request.user)
    except learning_examples.ExampleError as error:
        return _error(str(error))
    return _no_store(JsonResponse(result.as_dict(), status=201 if result.created else 200))


@access(POST="train")
@require_http_methods(["POST"])
def learning_example_status(request: HttpRequest, example_id):
    """Verify or reject one owned learning example."""
    example = get_object_or_404(
        OperationLearningExample.objects.select_related("operation"),
        id=example_id,
        operation__created_by=request.user,
    )
    try:
        payload = _json_body(request)
        status = str(payload.get("status") or "")
        note = payload.get("note")
        if note is not None:
            note = str(note)
        row = learning_examples.set_example_status(example=example, user=request.user, status=status, note=note)
    except (TypeError, ValueError) as error:
        return _error(str(error) or "Could not update that example.")
    return _no_store(JsonResponse({"example": learning_examples.serialize_example(row)}))
