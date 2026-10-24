from __future__ import annotations

import json
import uuid
from typing import Any

from django.core.exceptions import RequestDataTooBig
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from . import autolabel
from .autolabel import runner
from .models import Asset, AutoLabelJob, BoxAnnotation, Project
from .permissions import access

MAX_REQUEST_BYTES = 16_384
MIN_CONFIDENCE_FLOOR = 0.05
MAX_BOXES_CEILING = 100


def _json_body(request: HttpRequest) -> dict[str, Any]:
    if request.content_type != "application/json":
        raise ValueError("Send the request as JSON.")
    try:
        body = request.body
    except RequestDataTooBig as error:
        raise ValueError("Request is too large.") from error
    if len(body) > MAX_REQUEST_BYTES:
        raise ValueError("Request is too large.")
    try:
        payload = json.loads(body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError("Request body must be valid JSON.") from error
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object.")
    return payload


def _number(payload: dict[str, Any], key: str, default: float, low: float, high: float) -> float:
    value = payload.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} must be a number.")
    if not low <= float(value) <= high:
        raise ValueError(f"{key} must be between {low:g} and {high:g}.")
    return float(value)


def job_data(job: AutoLabelJob) -> dict[str, Any]:
    kept = runner.kept_for_review_count(job)
    return {
        "id": str(job.id),
        "status": job.status,
        "scope": "asset" if job.asset_id else "project",
        "asset_id": str(job.asset_id) if job.asset_id else None,
        "only_unlabeled": job.only_unlabeled,
        "min_confidence": job.min_confidence,
        "max_boxes": job.max_boxes,
        "model_name": job.model_name,
        "total_assets": job.total_assets,
        "processed_assets": job.processed_assets,
        "boxes_proposed": job.boxes_proposed,
        "cancel_requested": job.cancel_requested,
        "error": job.error,
        # Pictures that came from operator feedback are never auto-suggested; this says how many were left out.
        "kept_for_review": kept,
        "kept_for_review_reason": runner.KEPT_REASON if kept else "",
        "created_at": job.created_at.isoformat(),
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


def _no_store(response: JsonResponse) -> JsonResponse:
    response["Cache-Control"] = "no-store"
    return response


@access()
@require_http_methods(["GET"])
def autolabel_status(request: HttpRequest) -> JsonResponse:
    return _no_store(JsonResponse({**autolabel.describe(), "worker_online": runner.worker_online(), "worker_loading": runner.worker_loading()}))


@access(POST="label")
@require_http_methods(["GET", "POST"])
def project_autolabel(request: HttpRequest, project_id) -> JsonResponse:
    project = get_object_or_404(Project, id=project_id, modality="image")
    if request.method == "GET":
        runner.fail_stale_jobs()
        jobs = [job_data(job) for job in project.autolabel_jobs.all()[:5]]
        pending = BoxAnnotation.objects.filter(
            asset__project=project, status=BoxAnnotation.STATUS_SUGGESTED
        ).count()
        return _no_store(
            JsonResponse(
                {
                    **autolabel.describe(),
                    "worker_online": runner.worker_online(),
                    "worker_loading": runner.worker_loading(),
                    "jobs": jobs,
                    "pending_boxes": pending,
                }
            )
        )

    if not autolabel.is_enabled():
        return JsonResponse({"error": "Auto-labelling is turned off for this console."}, status=503)
    if not project.labels:
        return JsonResponse(
            {"error": "Add at least one label to this project before auto-labelling."}, status=400
        )
    try:
        payload = _json_body(request)
        if payload.keys() - {"asset_id", "only_unlabeled", "min_confidence", "max_boxes"}:
            raise ValueError("Request contains an unsupported field.")
        min_confidence = _number(payload, "min_confidence", 0.45, MIN_CONFIDENCE_FLOOR, 0.99)
        max_boxes = int(_number(payload, "max_boxes", 20, 1, MAX_BOXES_CEILING))
        only_unlabeled = payload.get("only_unlabeled", True)
        if not isinstance(only_unlabeled, bool):
            raise ValueError("only_unlabeled must be true or false.")
        asset = None
        if payload.get("asset_id") is not None:
            try:
                asset_id = uuid.UUID(str(payload["asset_id"]))
            except ValueError as error:
                raise ValueError("asset_id is not valid.") from error
            asset = get_object_or_404(Asset, id=asset_id, project=project)
    except ValueError as error:
        return JsonResponse({"error": str(error)}, status=400)

    if not project.assets.exists():
        return JsonResponse({"error": "Add images to this project first."}, status=400)
    runner.fail_stale_jobs()
    try:
        job = runner.enqueue(
            project,
            asset=asset,
            only_unlabeled=only_unlabeled,
            min_confidence=min_confidence,
            max_boxes=max_boxes,
            user=request.user,
        )
    except IntegrityError:
        return JsonResponse(
            {"error": "An auto-label job is already queued or running for this project."}, status=409
        )
    return JsonResponse({"job": job_data(job), "worker_online": runner.worker_online(), "worker_loading": runner.worker_loading()}, status=202)


@access(POST="label")
@require_http_methods(["POST"])
def project_autolabel_cancel(request: HttpRequest, project_id, job_id) -> JsonResponse:
    job = get_object_or_404(AutoLabelJob, id=job_id, project_id=project_id)
    with transaction.atomic():
        if job.status == AutoLabelJob.QUEUED:
            updated = AutoLabelJob.objects.filter(pk=job.pk, status=AutoLabelJob.QUEUED).update(
                status=AutoLabelJob.CANCELLED, finished_at=timezone.now()
            )
            if not updated:  # a worker claimed it between the read and the update
                AutoLabelJob.objects.filter(pk=job.pk).update(cancel_requested=True)
        elif job.status == AutoLabelJob.RUNNING:
            AutoLabelJob.objects.filter(pk=job.pk).update(cancel_requested=True)
    job.refresh_from_db()
    return JsonResponse({"job": job_data(job)})


def _review_counts(boxes) -> dict[str, int]:
    """How many of ``boxes`` there are, how many have no confidence score, how many came from operator feedback."""
    from_feedback = Q(model_name__startswith=runner.FEEDBACK_BOX_SOURCE)
    counts = boxes.aggregate(
        total=Count("pk"),
        without_score=Count("pk", filter=Q(confidence__isnull=True)),
        from_operator_feedback=Count("pk", filter=from_feedback),
    )
    return {key: counts[key] or 0 for key in ("total", "without_score", "from_operator_feedback")}


@access(POST="label")
@require_http_methods(["POST"])
def project_annotation_review(request: HttpRequest, project_id) -> JsonResponse:
    """Accept or reject model proposals in bulk. Only ever touches ``suggested`` boxes.

    Besides ``updated`` and ``pending_boxes`` the answer says (additively) what the action covers:

    * ``preview: true`` in the request changes nothing and only reports the counts below (``updated`` is 0);
    * ``would_update`` - how many suggested boxes the request's filters select (equals ``updated`` on a real run);
    * ``from_operator_feedback`` - how many of those came from a check-screen correction (a reject deletes them and
      the picture can never be pulled in again, so a screen should warn before it);
    * ``left_for_review`` - suggested boxes inside the same picture/id scope that the filters did NOT select, so were
      not changed: ``total``, how many of those ``without_score`` (a box someone drew has no confidence score, so a
      minimum-confidence filter never selects it) and how many are ``from_operator_feedback``. The two sub-counts
      can overlap. This never changes which boxes are accepted or rejected.
    """
    project = get_object_or_404(Project, id=project_id, modality="image")
    try:
        payload = _json_body(request)
        if payload.keys() - {"action", "asset_id", "min_confidence", "ids", "preview"}:
            raise ValueError("Request contains an unsupported field.")
        action = payload.get("action")
        if action not in {"accept", "reject"}:
            raise ValueError("action must be accept or reject.")
        preview = payload.get("preview")
        if preview is not None and not isinstance(preview, bool):
            raise ValueError("preview must be true or false.")
        preview = bool(preview)
        in_scope = BoxAnnotation.objects.filter(
            asset__project=project, status=BoxAnnotation.STATUS_SUGGESTED
        )
        if payload.get("asset_id") is not None:
            try:
                in_scope = in_scope.filter(asset_id=uuid.UUID(str(payload["asset_id"])))
            except ValueError as error:
                raise ValueError("asset_id is not valid.") from error
        if payload.get("ids") is not None:
            ids = payload["ids"]
            if not isinstance(ids, list) or len(ids) > 500:
                raise ValueError("ids must be a list of up to 500 annotation ids.")
            try:
                in_scope = in_scope.filter(id__in=[uuid.UUID(str(item)) for item in ids])
            except ValueError as error:
                raise ValueError("ids contains an invalid annotation id.") from error
        suggestions = in_scope
        if payload.get("min_confidence") is not None:
            suggestions = suggestions.filter(
                confidence__gte=_number(payload, "min_confidence", 0, 0, 1)
            )
    except ValueError as error:
        return JsonResponse({"error": str(error)}, status=400)

    selected = _review_counts(suggestions)
    # Read before anything changes. Only a minimum-confidence filter can select fewer than the scope.
    left = _review_counts(in_scope.exclude(pk__in=suggestions.values("pk")))
    if preview:
        updated = 0
    elif action == "accept":
        updated = suggestions.update(
            status=BoxAnnotation.STATUS_ACCEPTED,
            reviewed_by=request.user,
            reviewed_at=timezone.now(),
        )
    else:
        updated, _ = suggestions.delete()
    remaining = BoxAnnotation.objects.filter(
        asset__project=project, status=BoxAnnotation.STATUS_SUGGESTED
    ).count()
    return JsonResponse(
        {
            "updated": updated,
            "pending_boxes": remaining,
            "preview": preview,
            "would_update": selected["total"] if preview else updated,
            "from_operator_feedback": selected["from_operator_feedback"],
            "left_for_review": left,
        }
    )
