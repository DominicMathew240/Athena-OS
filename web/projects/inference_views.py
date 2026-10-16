"""JSON API for inference apps, image runs, operator decisions and feedback export."""

from __future__ import annotations

import json
import uuid
from typing import Any

from django.core.files.storage import default_storage
from django.http import FileResponse, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods

from .api_common import HttpError, image_project, json_errors, json_payload, no_store, reject_unknown
from .inference import service
from .models import InferenceApp, ModelVersion, ObservationRecord
from .training.lifecycle import LifecycleError

LIST_LIMIT = 100


def app_data(app: InferenceApp) -> dict[str, Any]:
    model = app.model_version
    return {
        "id": str(app.id),
        "project_id": str(app.project_id),
        "name": app.name,
        "slug": app.slug,
        "status": app.status,
        "model_version": {
            "id": str(model.id),
            "version": model.version,
            "stage_now": model.stage,
            "artifact_sha256": model.artifact_sha256,
        },
        "model_in_use": model.stage == ModelVersion.APPROVED,
        "config": app.config,
        "created_at": app.created_at.isoformat(),
        "updated_at": app.updated_at.isoformat(),
    }


def _app(project, app_id) -> InferenceApp:
    return get_object_or_404(InferenceApp.objects.select_related("model_version", "project"), id=app_id, project=project)


def _observation(app, observation_id) -> ObservationRecord:
    return get_object_or_404(
        ObservationRecord.objects.select_related("app", "model_version"), id=observation_id, app=app
    )


def _model(project, value) -> ModelVersion:
    try:
        model_id = uuid.UUID(str(value))
    except ValueError:
        raise HttpError("model_version_id is not valid.") from None
    return get_object_or_404(ModelVersion, id=model_id, project=project)


@require_http_methods(["GET", "POST"])
@json_errors
def inference_apps(request: HttpRequest, project_id) -> JsonResponse:
    project = image_project(project_id)
    if request.method == "GET":
        apps = InferenceApp.objects.filter(project=project).select_related("model_version")[:LIST_LIMIT]
        return no_store(JsonResponse({"apps": [app_data(app) for app in apps]}))
    payload = json_payload(request)
    reject_unknown(payload, {"name", "slug", "model_version_id", "config"})
    if payload.get("model_version_id") is None:
        raise HttpError("model_version_id is required: pick an approved model version.")
    config = payload.get("config", {})
    if not isinstance(config, dict):
        raise HttpError("config must be an object.")
    app = service.create_app(
        project, request.user, name=payload.get("name"), slug=payload.get("slug"),
        model=_model(project, payload["model_version_id"]), config=config,
    )
    return no_store(JsonResponse({"app": app_data(app), "preview": service.render_preview(app)}, status=201))


@require_http_methods(["GET", "PATCH", "DELETE"])
@json_errors
def inference_app_detail(request: HttpRequest, project_id, app_id) -> JsonResponse:
    project = image_project(project_id)
    app = _app(project, app_id)
    if request.method == "GET":
        return no_store(JsonResponse({"app": app_data(app)}))
    if request.method == "DELETE":
        if app.observations.exists():
            raise LifecycleError(
                "This app has recorded images and feedback, which are kept as evidence, so it cannot be deleted. "
                'Archive it instead: PATCH {"status": "archived"}.',
                409,
            )
        app.delete()
        return no_store(JsonResponse({"deleted": True}))
    payload = json_payload(request)
    reject_unknown(payload, {"name", "model_version_id", "config", "status"})
    config = payload.get("config")
    if config is not None and not isinstance(config, dict):
        raise HttpError("config must be an object.")
    model = _model(project, payload["model_version_id"]) if payload.get("model_version_id") is not None else None
    app = service.update_app(app, name=payload.get("name"), model=model, config=config, status=payload.get("status"))
    return no_store(JsonResponse({"app": app_data(app), "preview": service.render_preview(app)}))


@require_http_methods(["GET"])
def inference_app_preview(request: HttpRequest, project_id, app_id) -> JsonResponse:
    app = _app(image_project(project_id), app_id)
    return no_store(JsonResponse({"preview": service.render_preview(app)}))


@require_http_methods(["POST"])
@json_errors
def inference_app_run(request: HttpRequest, project_id, app_id) -> JsonResponse:
    app = _app(image_project(project_id), app_id)
    uploads = request.FILES.getlist("image")
    if len(uploads) != 1 or len(request.FILES) != 1:
        raise HttpError("Send exactly one image as multipart form data in the field named `image`.")
    observation = service.submit_image(app, request.user, uploads[0])
    base = f"/api/projects/{app.project_id}/inference/apps/{app.id}/observations/{observation.id}"
    return no_store(
        JsonResponse(
            {
                "observation_id": str(observation.id),
                "status": observation.status,
                "poll_url": base,
                **service.worker_hint(),
                "message": "The image is queued. Poll `poll_url` until its status is succeeded or failed.",
            },
            status=202,
        )
    )


@require_http_methods(["GET"])
def inference_observations(request: HttpRequest, project_id, app_id) -> JsonResponse:
    app = _app(image_project(project_id), app_id)
    rows = app.observations.select_related("app", "model_version")[:LIST_LIMIT]
    return no_store(JsonResponse({"observations": [service.render_observation(o) for o in rows]}))


@require_http_methods(["GET"])
def inference_observation_detail(request: HttpRequest, project_id, app_id, observation_id) -> JsonResponse:
    app = _app(image_project(project_id), app_id)
    return no_store(JsonResponse({"render": service.render_observation(_observation(app, observation_id))}))


@require_http_methods(["GET"])
def inference_observation_image(request: HttpRequest, project_id, app_id, observation_id):
    app = _app(image_project(project_id), app_id)
    observation = _observation(app, observation_id)
    response = FileResponse(default_storage.open(observation.image_key, "rb"), content_type=observation.content_type)
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Disposition"] = 'inline; filename="observation"'
    return no_store(response)


@require_http_methods(["POST"])
@json_errors
def inference_observation_feedback(request: HttpRequest, project_id, app_id, observation_id) -> JsonResponse:
    app = _app(image_project(project_id), app_id)
    observation = _observation(app, observation_id)
    record = service.add_feedback(observation, request.user, json_payload(request))
    return no_store(
        JsonResponse(
            {
                "feedback": service.feedback_data(record),
                "message": "Recorded. This does not change the model; it is kept as a reviewed training candidate.",
            },
            status=201,
        )
    )


@require_http_methods(["POST"])
@json_errors
def inference_observation_decision(request: HttpRequest, project_id, app_id, observation_id) -> JsonResponse:
    app = _app(image_project(project_id), app_id)
    observation = _observation(app, observation_id)
    payload = json_payload(request)
    reject_unknown(payload, {"decision", "note"})
    service.add_decision(observation, request.user, payload.get("decision"), payload.get("note"))
    return no_store(JsonResponse({"render": service.render_observation(_observation(app, observation_id))}, status=201))


@require_http_methods(["GET"])
def inference_feedback_export(request: HttpRequest, project_id, app_id) -> HttpResponse:
    app = _app(image_project(project_id), app_id)
    fmt = request.GET.get("format", "json")
    if fmt not in ("json", "jsonl"):
        return no_store(JsonResponse({"error": "format must be json or jsonl."}, status=400))
    rows, truncated = service.export_rows(app, include_superseded=request.GET.get("include_superseded") == "1")
    if fmt == "jsonl":
        body = "".join(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n" for row in rows)
        response = HttpResponse(body, content_type="application/x-ndjson; charset=utf-8")
    else:
        response = JsonResponse(
            {"notice": service.EXPORT_NOTICE, "count": len(rows), "truncated": truncated, "candidates": rows},
            json_dumps_params={"ensure_ascii": False},
        )
    response["Content-Disposition"] = f'attachment; filename="athena-feedback-{app.slug}.{fmt}"'
    response["X-Content-Type-Options"] = "nosniff"
    return no_store(response)
