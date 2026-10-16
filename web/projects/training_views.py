"""JSON API for training runs, model versions, evaluation reports and staged approval."""

from __future__ import annotations

import uuid
from typing import Any

from django.http import HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods

from . import training
from .api_common import HttpError, image_project, json_errors, json_payload, no_store, reject_unknown
from .datasets.manifest import opaque_user
from .models import DatasetVersion, InferenceApp, ModelVersion, TrainingRun
from .training import lifecycle, report, runner

LIST_LIMIT = 100


def run_data(run: TrainingRun, *, detail: bool = False) -> dict[str, Any]:
    data = {
        "id": str(run.id),
        "project_id": str(run.project_id),
        "dataset": {
            "id": str(run.dataset_version_id),
            "version": run.dataset_version.version,
            "manifest_sha256": run.dataset_manifest_sha256,
        },
        "backend": run.backend,
        "config": run.config,
        "config_sha256": run.config_sha256,
        "status": run.status,
        "cancel_requested": run.cancel_requested,
        "progress": run.progress,
        "error": run.error,
        "created_by": opaque_user(run.created_by_id),
        "created_at": run.created_at.isoformat(),
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "heartbeat_at": run.heartbeat_at.isoformat() if run.heartbeat_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "model_version_ids": [str(pk) for pk in run.model_versions.values_list("id", flat=True)],
    }
    if detail:
        data.update(
            logs=run.logs,
            metrics=run.metrics,
            runtime_info=run.runtime_info,
            events=[event_data(event) for event in run.events.all()],
        )
    return data


def event_data(event) -> dict[str, Any]:
    return {
        "seq": event.id,
        "kind": event.kind,
        "from": event.from_state,
        "to": event.to_state,
        "note": event.note,
        "by": opaque_user(event.actor_id_snapshot),
        "at": event.created_at.isoformat(),
        "model_version_id": str(event.model_version_id) if event.model_version_id else None,
        "training_run_id": str(event.training_run_id) if event.training_run_id else None,
    }


def model_data(model: ModelVersion, *, detail: bool = False) -> dict[str, Any]:
    run = model.training_run
    data = {
        "id": str(model.id),
        "project_id": str(model.project_id),
        "version": model.version,
        "stage": model.stage,
        "backend": model.backend,
        "label_names": model.label_names,
        "artifact_sha256": model.artifact_sha256,
        "artifact_size_bytes": model.artifact_size_bytes,
        "evaluation": report.summary(model.evaluation),
        "lineage": {
            "origin": "training_run" if run else "imported",
            "training_run_id": str(model.training_run_id) if model.training_run_id else None,
            "dataset_version_id": str(run.dataset_version_id) if run else None,
            "dataset_version": run.dataset_version.version if run else None,
            "dataset_manifest_sha256": run.dataset_manifest_sha256 if run else None,
            "config_sha256": run.config_sha256 if run else None,
        },
        "staged_by": opaque_user(model.staged_by_id),
        "staged_at": model.staged_at.isoformat() if model.staged_at else None,
        "approved_by": opaque_user(model.approved_by_id),
        "approved_at": model.approved_at.isoformat() if model.approved_at else None,
        "approval_note": model.approval_note,
        "retired_at": model.retired_at.isoformat() if model.retired_at else None,
        "previous_approved_id": str(model.previous_approved_id) if model.previous_approved_id else None,
        "created_at": model.created_at.isoformat(),
        "in_use_by_apps": [
            {"id": str(app.id), "name": app.name, "slug": app.slug} for app in lifecycle.apps_using(model)
        ],
    }
    if detail:
        data["events"] = [event_data(event) for event in model.events.all()]
    return data


def _get_model(project, model_id) -> ModelVersion:
    return get_object_or_404(
        ModelVersion.objects.select_related("training_run__dataset_version", "project"),
        id=model_id,
        project=project,
    )


def _app_refs(apps) -> list[dict[str, str]]:
    return [{"id": str(app.id), "name": app.name, "slug": app.slug} for app in apps]


@require_http_methods(["GET"])
def training_backends(request: HttpRequest, project_id) -> JsonResponse:
    image_project(project_id)
    worker = lifecycle.worker_status()
    backends = []
    for cls in training.backend_classes().values():
        info = training.describe_backend(cls())
        info["worker_reports_available"] = worker["backends"].get(info["name"])
        backends.append(info)
    return no_store(
        JsonResponse(
            {
                "default": training.training_settings()["DEFAULT_BACKEND"],
                "worker": {"online": worker["online"], "loading": worker["loading"]},
                "backends": backends,
            }
        )
    )


@require_http_methods(["GET"])
def training_readiness(request: HttpRequest, project_id) -> JsonResponse:
    project = image_project(project_id)
    return no_store(JsonResponse(lifecycle.readiness(project, request.GET.get("backend") or None)))


@require_http_methods(["GET", "POST"])
@json_errors
def training_runs(request: HttpRequest, project_id) -> JsonResponse:
    project = image_project(project_id)
    if request.method == "GET":
        runs = project.training_runs.select_related("dataset_version")[:LIST_LIMIT]
        return no_store(JsonResponse({"runs": [run_data(run) for run in runs]}))

    payload = json_payload(request, allow_empty=True)
    reject_unknown(payload, {"dataset_id", "backend", "config"})
    config = payload.get("config", {})
    if not isinstance(config, dict):
        raise HttpError("config must be an object of training settings.")
    backend_name = payload.get("backend") or training.training_settings()["DEFAULT_BACKEND"]
    if not isinstance(backend_name, str):
        raise HttpError("backend must be the name of an installed backend.")
    if payload.get("dataset_id") is not None:
        try:
            dataset_id = uuid.UUID(str(payload["dataset_id"]))
        except ValueError:
            raise HttpError("dataset_id is not valid.") from None
        dataset = get_object_or_404(DatasetVersion, id=dataset_id, project=project)
    else:
        dataset = lifecycle.latest_dataset(project)
        if dataset is None:
            raise HttpError("There is no dataset version yet. Create one from reviewed boxes, then start training.", 400)
    run = lifecycle.create_run(project, request.user, dataset=dataset, backend_name=backend_name, config=config)
    worker = lifecycle.worker_status()
    return no_store(
        JsonResponse({"run": run_data(run, detail=True), "worker_online": worker["online"]}, status=202)
    )


@require_http_methods(["GET"])
def training_run_detail(request: HttpRequest, project_id, run_id) -> JsonResponse:
    project = image_project(project_id)
    run = get_object_or_404(TrainingRun.objects.select_related("dataset_version"), id=run_id, project=project)
    runner.fail_stale_runs()
    run.refresh_from_db()
    return no_store(JsonResponse({"run": run_data(run, detail=True)}))


@require_http_methods(["POST"])
@json_errors
def training_run_cancel(request: HttpRequest, project_id, run_id) -> JsonResponse:
    project = image_project(project_id)
    run = get_object_or_404(TrainingRun.objects.select_related("dataset_version"), id=run_id, project=project)
    if run.status not in TrainingRun.ACTIVE_STATUSES:
        raise lifecycle.LifecycleError(f"This run already {run.get_status_display().lower()}; there is nothing to cancel.", 409)
    run = lifecycle.request_cancel(run, request.user)
    return no_store(JsonResponse({"run": run_data(run, detail=True)}))


@require_http_methods(["GET"])
def model_collection(request: HttpRequest, project_id) -> JsonResponse:
    project = image_project(project_id)
    models = project.model_versions.select_related("training_run__dataset_version")[:LIST_LIMIT]
    approved = next((m for m in models if m.stage == ModelVersion.APPROVED), None)
    return no_store(
        JsonResponse(
            {
                "models": [model_data(m) for m in models],
                "approved_model_id": str(approved.id) if approved else None,
            }
        )
    )


@require_http_methods(["GET"])
def model_detail(request: HttpRequest, project_id, model_id) -> JsonResponse:
    project = image_project(project_id)
    return no_store(JsonResponse({"model": model_data(_get_model(project, model_id), detail=True)}))


@require_http_methods(["GET"])
def model_evaluation(request: HttpRequest, project_id, model_id) -> JsonResponse:
    project = image_project(project_id)
    model = _get_model(project, model_id)
    result = report.build_report(model.evaluation)
    return no_store(
        JsonResponse(
            {
                "report": result,
                "model": {
                    "id": str(model.id),
                    "version": model.version,
                    "stage": model.stage,
                    "artifact_sha256": model.artifact_sha256,
                    "lineage": model_data(model)["lineage"],
                },
                "can_stage": model.stage == ModelVersion.CANDIDATE and result["available"],
            }
        )
    )


@require_http_methods(["POST"])
@json_errors
def model_stage(request: HttpRequest, project_id, model_id) -> JsonResponse:
    project = image_project(project_id)
    model = _get_model(project, model_id)
    payload = json_payload(request, allow_empty=True)
    reject_unknown(payload, {"note"})
    model = lifecycle.stage_model(model, request.user, payload.get("note", ""))
    return no_store(JsonResponse({"model": model_data(_get_model(project, model.id), detail=True)}))


@require_http_methods(["POST"])
@json_errors
def model_approve(request: HttpRequest, project_id, model_id) -> JsonResponse:
    project = image_project(project_id)
    model = _get_model(project, model_id)
    payload = json_payload(request)
    reject_unknown(payload, {"note"})
    model, previous = lifecycle.approve_model(model, request.user, payload.get("note"))
    return no_store(
        JsonResponse(
            {
                "model": model_data(_get_model(project, model.id), detail=True),
                "retired_model_id": str(previous.id) if previous else None,
                "apps_still_on_retired_model": _app_refs(lifecycle.apps_using(previous)) if previous else [],
                "message": (
                    "Approved. Inference apps keep using the exact model version they were set up with; "
                    "re-point an app explicitly to use this one."
                ),
            }
        )
    )


@require_http_methods(["POST"])
@json_errors
def model_rollback(request: HttpRequest, project_id, model_id) -> JsonResponse:
    project = image_project(project_id)
    model = _get_model(project, model_id)
    payload = json_payload(request)
    reject_unknown(payload, {"note"})
    retired, restored = lifecycle.rollback_model(model, request.user, payload.get("note"))
    return no_store(
        JsonResponse(
            {
                "model": model_data(_get_model(project, restored.id), detail=True),
                "retired_model_id": str(retired.id),
                "apps_still_on_retired_model": _app_refs(lifecycle.apps_using(retired)),
                "message": "Rolled back. Apps that were using the retired model will not run until re-pointed.",
            }
        )
    )
