"""JSON API for training runs, model versions, evaluation reports and staged approval."""

from __future__ import annotations

import uuid
from typing import Any

from django.http import Http404, HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods

from . import training
from .api_common import HttpError, json_errors, json_payload, no_store, reject_unknown
from .annotations import image_project_uses_boxes
from .datasets.manifest import opaque_user
from .models import DatasetVersion, ModelVersion, Project, TrainingRun
from .training import lifecycle, report, runner
from .permissions import access

LIST_LIMIT = 100

#: The only backend a handwriting-OCR (transcript) project may run; imported lazily-free (the
#: name is a plain string; the module itself imports nothing heavy at import time anyway).
OCR_BACKEND = "ocr-trocr"
#: The backend each recipe runs on when the caller did not pick one (the Teach panel's one-click
#: start never names a backend). Only recipes whose backend is not the box default need an entry.
#: A recipe with NO entry has no teaching backend of its own (document-fields: rule-based
#: extraction, no training), so it must not be defaulted onto the console's box backend - see
#: ``_default_backend_for``.
RECIPE_DEFAULT_BACKENDS = {
    "handwriting-ocr": OCR_BACKEND,
    "audio-classification": "audio-wav-cnn",
    "text-classification": "text-sklearn",
    "signal-classification": "signal-sklearn",
}

#: Recipes that never train (document-fields: rule-based field validation plus the
#: version-to-version table comparison, no model). A training run on such a project is refused
#: plainly at the door rather than routed to some unrelated backend.
NON_TRAINING_RECIPES = ("document-fields",)


def _default_backend_for(project: Project, payload: dict) -> str:
    """The backend a run starts on: the request's own choice, else the recipe's, else the console
    default. A recipe whose backend differs from the box default (handwriting OCR) also refuses a
    request that names a different one, in plain words, rather than failing deep inside training."""
    from .tasks import recipe_for_project

    backend_name = payload.get("backend") or training.training_settings()["DEFAULT_BACKEND"]
    if not isinstance(backend_name, str):
        raise HttpError("backend must be the name of an installed backend.")
    recipe = recipe_for_project(project)
    expected = RECIPE_DEFAULT_BACKENDS.get(recipe.key) if recipe else None
    if expected is None:
        return backend_name
    if "backend" not in payload:
        return expected
    if backend_name != expected:
        what = "reads handwriting" if recipe.key == "handwriting-ocr" else f"teaches {recipe.title.lower()}"
        raise HttpError(
            f"This project {what}, so its runs use the {expected} backend. "
            "Leave backend out of the request or send that name.",
            400,
        )
    return backend_name


def teachable_project(project_id) -> Project:
    """The project a training run or model belongs to: image projects (unchanged) plus every
    project whose data type can be trained today — a registered modality plugin with an
    annotation kind (audio clips, tables) or an available recipe (handwriting OCR, whose
    plugin is recipe-dispatched). Others 404 exactly as before, so the page shows its
    "not available yet" refusal instead of an error. Image projects whose recipe is not
    box-shaped (handwriting-OCR transcripts) train through the generic-snapshot path with
    their own backend; the API stays honest: a run on an OCR project must use its recipe's
    backend (``ocr-trocr``), not a box backend.

    A recipe that never trains (document-fields, whose work is rule-based field validation
    and the version-to-version comparison) is treated as unteachable here: its runs/models
    endpoints answer the same honest 404, because there is genuinely nothing to run."""
    project = get_object_or_404(Project, id=project_id)
    if project.modality == "image":
        return project
    from .modalities import get_plugin
    from .tasks import recipe_for_project

    if getattr(get_plugin(project.modality), "annotation_kind", ""):
        return project
    recipe = recipe_for_project(project)
    if recipe is not None and recipe.available and recipe.key not in NON_TRAINING_RECIPES:
        return project
    raise Http404("This project's task has no teaching tools yet.")


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
        "task": run.task,
        "annotation_kind": run.annotation_kind,
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


def snapshot_data(version: DatasetVersion, *, newly_created: bool) -> dict[str, Any]:
    """Which frozen copy of the accepted work a run was started on, and whether this very request froze it.

    A generic (non-box recipe) version has no images or boxes; its counts come from the manifest
    (``entry_count``), so the answer names what it actually holds for both kinds."""
    if version.entries.exists():
        counts = dict(version.manifest.get("counts", {}))
        return {
            "id": str(version.id),
            "version": version.version,
            "newly_created": newly_created,
            "entry_count": counts.get("entry_count", version.entries.count()),
            "split_counts": lifecycle.split_counts(version),
            "created_at": version.created_at.isoformat(),
        }
    return {
        "id": str(version.id),
        "version": version.version,
        "newly_created": newly_created,
        "image_count": version.image_count,
        "box_count": version.box_count,
        "split_counts": lifecycle.split_counts(version),
        "created_at": version.created_at.isoformat(),
    }


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
        "task": model.task,
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
            # Opaque id of whoever started the run; the console uses it when approval needs a second person.
            "run_started_by": opaque_user(run.created_by_id) if run else None,
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
        # Read-only: for a staged model, the check screens approving it could move (see ``model_approve``).
        data["would_repoint"] = lifecycle.would_repoint(model)
    return data


def _repoint_request(payload: dict[str, Any]) -> dict[str, Any]:
    """The optional, explicit ``repoint_apps`` (exactly ``true``) / ``repoint_app_ids`` (a list of screen ids) of a decision."""
    flag = payload.get("repoint_apps", False)
    if not isinstance(flag, bool):
        raise HttpError("repoint_apps must be true or false.", 400)
    ids = payload.get("repoint_app_ids")
    if ids is None:
        return {"repoint_apps": flag, "repoint_app_ids": None}
    if not isinstance(ids, list) or not all(isinstance(item, str) for item in ids):
        raise HttpError("repoint_app_ids must be a list of check screen ids.", 400)
    if flag:
        raise HttpError("Send either repoint_apps: true (every screen on the model being replaced) or repoint_app_ids, not both.", 400)
    if len(ids) > lifecycle.MAX_REPOINT_IDS:
        raise HttpError(f"repoint_app_ids can list at most {lifecycle.MAX_REPOINT_IDS} check screens.", 400)
    parsed: list[uuid.UUID] = []
    for item in ids:
        try:
            value = uuid.UUID(item)
        except ValueError:
            raise HttpError(f"repoint_app_ids holds something that is not a check screen id: {item[:40]!r}.", 400) from None
        if value not in parsed:
            parsed.append(value)
    return {"repoint_apps": False, "repoint_app_ids": parsed}


def _repoint_body(moved) -> dict[str, Any]:
    return {"repointed": moved.repointed, "repoint_skipped": moved.skipped}


def _get_model(project, model_id) -> ModelVersion:
    return get_object_or_404(
        ModelVersion.objects.select_related("training_run__dataset_version", "project"),
        id=model_id,
        project=project,
    )


def _app_refs(apps) -> list[dict[str, str]]:
    return [{"id": str(app.id), "name": app.name, "slug": app.slug} for app in apps]


@access()
@require_http_methods(["GET"])
@json_errors
def training_backends(request: HttpRequest, project_id) -> JsonResponse:
    # The backend list is the image one; other modalities 404 like before, but in the API's own
    # JSON error shape (an Http404 here would be Django's HTML page).
    project = get_object_or_404(Project, id=project_id)
    if project.modality != "image":
        raise HttpError("The backend list is only available for picture projects.", 404)
    worker = lifecycle.worker_status()
    for_options = {"online": worker["online"], "downloads_allowed": worker["downloads_allowed"]}
    backends = []
    for cls in training.backend_classes().values():
        backend = cls()
        info = training.describe_backend(backend)
        info["worker_reports_available"] = worker["backends"].get(info["name"])
        info["start_options"] = backend.start_options(for_options)
        backends.append(info)
    # Whether ready-made models may be downloaded: the running helper's own answer, else the console's environment.
    from .training.torchvision_frcnn import downloads_for

    # A switch stored in the console wins, then the helper's report, then the console's environment.
    # ``reported_by`` says which of the three answered: "setting" (the stored console switch), "helper" (the running
    # helper's own report) or "console" (neither: the web process's environment, a guess). The docs list only
    # "helper" | "console"; the value for the stored switch is "setting" and is kept as is (no wire change).
    allowed, who = downloads_for(worker)
    downloads = {"allowed": allowed, "reported_by": who}
    return no_store(
        JsonResponse(
            {
                "default": training.training_settings()["DEFAULT_BACKEND"],
                "worker": {"online": worker["online"], "loading": worker["loading"]},
                "downloads": downloads,
                "backends": backends,
            }
        )
    )


@access()
@require_http_methods(["GET"])
def training_readiness(request: HttpRequest, project_id) -> JsonResponse:
    """Readiness for the Teach panel, dispatching on the project's data kind: the full answer for
    image projects (box recipes unchanged; a transcript recipe like handwriting OCR gets it too,
    with its own nouns and generic snapshots), and a plain "not available yet" state for every
    recipe that has no teaching tools yet."""
    project = get_object_or_404(Project, id=project_id)
    if project.modality != "image" or not image_project_uses_boxes(project):
        from .tasks import recipe_for_project

        recipe = recipe_for_project(project)
        teachable = recipe is not None and recipe.available and recipe.key not in NON_TRAINING_RECIPES
        if project.modality == "image" or teachable:
            return no_store(JsonResponse(lifecycle.readiness(project, request.GET.get("backend") or None)))
        return no_store(JsonResponse(_task_unavailable_readiness(project)))
    return no_store(JsonResponse(lifecycle.readiness(project, request.GET.get("backend") or None)))


def _task_unavailable_readiness(project) -> dict[str, Any]:
    """The "not available yet" answer for a project whose recipe has no teaching tools yet.

    Shape-compatible with ``lifecycle.readiness`` so the console's Teach panel renders the same
    refusal it shows for any other blocker, instead of crashing on a data kind it does not know.
    A recipe that is available but never trains (document-fields) gets its own honest sentence:
    there is nothing to teach, not a missing tool.
    """
    from .tasks import recipe_for_project

    recipe = recipe_for_project(project)
    what = recipe.title if recipe else project.get_modality_display()
    if recipe is not None and recipe.key in NON_TRAINING_RECIPES:
        reason = (
            "This project checks document fields with fixed rules (text, number and date), so there is "
            "no model to teach. Compare two saved versions of its table to score the fields instead."
        )
    else:
        reason = (
            recipe.unavailable_reason
            if recipe and recipe.unavailable_reason
            else f"Teaching {what.lower()} models is not available yet."
        )
    return {
        "ready": False,
        "blockers": [reason],
        "warnings": [],
        "backend": {"name": "", "available_in_web_process": False, "worker_online": False,
                    "worker_reports_available": None, "message": reason, "how_to_enable": ""},
        "worker": {"online": False, "loading": False},
        "dataset": None,
        "active_run_id": None,
        "start_from": None,
        "derived": None,
        "one_click": {"supported": False, "ready": False, "blockers": [reason], "warnings": [],
                      "notes": [], "files_rechecked": True, "snapshot": {
                          "latest_version": None, "up_to_date": False, "would_create_new": False}},
        "task_unavailable": True,
    }


@access(POST="train")
@require_http_methods(["GET", "POST"])
@json_errors
def training_runs(request: HttpRequest, project_id) -> JsonResponse:
    project = teachable_project(project_id)
    if request.method == "GET":
        runs = project.training_runs.select_related("dataset_version")[:LIST_LIMIT]
        return no_store(JsonResponse({"runs": [run_data(run) for run in runs]}))

    payload = json_payload(request, allow_empty=True)
    reject_unknown(payload, {"dataset_id", "snapshot", "backend", "config", "acknowledge_pretrained_terms"})
    config = payload.get("config", {})
    if not isinstance(config, dict):
        raise HttpError("config must be an object of training settings.")
    backend_name = _default_backend_for(project, payload)
    snapshot = payload.get("snapshot")
    if snapshot is not None and snapshot != "latest":
        raise HttpError('snapshot can only be "latest". To train on a particular version, send dataset_id instead.')
    if snapshot and payload.get("dataset_id") is not None:
        raise HttpError('Send either snapshot: "latest" or a dataset_id, not both.')
    if snapshot:
        # The one-click start: unless the caller chose, use the best ready-made start that can run and the quick settings
        # (for a backend that understands them; see lifecycle.snapshot_default_config).
        config = lifecycle.snapshot_default_config(backend_name, config)
        run, dataset, created = lifecycle.start_run_on_latest_snapshot(
            project, request.user, backend_name=backend_name, config=config,
            acknowledged_terms=payload.get("acknowledge_pretrained_terms", False),
        )
    else:
        if payload.get("dataset_id") is not None:
            try:
                dataset_id = uuid.UUID(str(payload["dataset_id"]))
            except ValueError:
                raise HttpError("dataset_id is not valid.") from None
            dataset = get_object_or_404(DatasetVersion, id=dataset_id, project=project)
        else:
            dataset = lifecycle.latest_dataset(project)
            if dataset is None:
                raise HttpError(
                    "There is no dataset version yet. Create one from reviewed labels, then start training.", 400
                )
        # A non-box recipe's backend rule (handwriting OCR -> ocr-trocr) was applied where the
        # backend was chosen (``_default_backend_for``), for both this and the snapshot path.
        run = lifecycle.create_run(
            project, request.user, dataset=dataset, backend_name=backend_name, config=config,
            acknowledged_terms=payload.get("acknowledge_pretrained_terms", False),
        )
        created = False
    worker = lifecycle.worker_status()
    return no_store(
        JsonResponse(
            {
                "run": run_data(run, detail=True),
                "snapshot": snapshot_data(dataset, newly_created=created),
                "worker_online": worker["online"],
            },
            status=202,
        )
    )


@access()
@require_http_methods(["GET"])
def training_run_detail(request: HttpRequest, project_id, run_id) -> JsonResponse:
    project = teachable_project(project_id)
    run = get_object_or_404(TrainingRun.objects.select_related("dataset_version"), id=run_id, project=project)
    runner.fail_stale_runs()
    run.refresh_from_db()
    return no_store(JsonResponse({"run": run_data(run, detail=True)}))


@access(POST="train")
@require_http_methods(["POST"])
@json_errors
def training_run_cancel(request: HttpRequest, project_id, run_id) -> JsonResponse:
    project = teachable_project(project_id)
    run = get_object_or_404(TrainingRun.objects.select_related("dataset_version"), id=run_id, project=project)
    if run.status not in TrainingRun.ACTIVE_STATUSES:
        raise lifecycle.LifecycleError(f"This run already {run.get_status_display().lower()}; there is nothing to cancel.", 409)
    run = lifecycle.request_cancel(run, request.user)
    return no_store(JsonResponse({"run": run_data(run, detail=True)}))


@access()
@require_http_methods(["GET"])
def model_collection(request: HttpRequest, project_id) -> JsonResponse:
    project = teachable_project(project_id)
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


@access()
@require_http_methods(["GET"])
def model_detail(request: HttpRequest, project_id, model_id) -> JsonResponse:
    project = teachable_project(project_id)
    return no_store(JsonResponse({"model": model_data(_get_model(project, model_id), detail=True)}))


@access()
@require_http_methods(["GET"])
def model_evaluation(request: HttpRequest, project_id, model_id) -> JsonResponse:
    project = teachable_project(project_id)
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


@access(POST="train")
@require_http_methods(["POST"])
@json_errors
def model_stage(request: HttpRequest, project_id, model_id) -> JsonResponse:
    project = teachable_project(project_id)
    model = _get_model(project, model_id)
    payload = json_payload(request, allow_empty=True)
    reject_unknown(payload, {"note"})
    model = lifecycle.stage_model(model, request.user, payload.get("note", ""))
    return no_store(JsonResponse({"model": model_data(_get_model(project, model.id), detail=True)}))


@access(POST="approve")
@require_http_methods(["POST"])
@json_errors
def model_approve(request: HttpRequest, project_id, model_id) -> JsonResponse:
    project = teachable_project(project_id)
    model = _get_model(project, model_id)
    payload = json_payload(request)
    reject_unknown(payload, {"note", "acknowledge_weak_results", "repoint_apps", "repoint_app_ids"})
    acknowledged = payload.get("acknowledge_weak_results", False)
    if not isinstance(acknowledged, bool):
        raise HttpError("acknowledge_weak_results must be true or false.", 400)
    repoint = _repoint_request(payload)
    model, previous = lifecycle.approve_model(
        model, request.user, payload.get("note"), acknowledge_weak_results=acknowledged, **repoint
    )
    moved = model.repoint
    message = (
        "Approved. Inference apps keep using the exact model version they were set up with; "
        "re-point an app explicitly to use this one."
    )
    if moved.asked:
        message = f"Approved. {len(moved.moved)} check screen(s) now use this model" + (
            f"; {len(moved.skipped)} could not be moved and still use the model that was replaced." if moved.skipped else "."
        )
    return no_store(
        JsonResponse(
            {
                "model": model_data(_get_model(project, model.id), detail=True),
                "retired_model_id": str(previous.id) if previous else None,
                "apps_still_on_retired_model": _app_refs(lifecycle.apps_using(previous)) if previous else [],
                **_repoint_body(moved),
                "message": message,
            }
        )
    )


@access(POST="approve")
@require_http_methods(["POST"])
@json_errors
def model_rollback(request: HttpRequest, project_id, model_id) -> JsonResponse:
    project = teachable_project(project_id)
    model = _get_model(project, model_id)
    payload = json_payload(request)
    reject_unknown(payload, {"note", "repoint_apps", "repoint_app_ids"})
    repoint = _repoint_request(payload)
    retired, restored = lifecycle.rollback_model(model, request.user, payload.get("note"), **repoint)
    moved = restored.repoint
    message = "Rolled back. Apps that were using the retired model will not run until re-pointed."
    if moved.asked:
        message = f"Rolled back. {len(moved.moved)} check screen(s) now use the restored model" + (
            f"; {len(moved.skipped)} could not be moved and will not run until re-pointed." if moved.skipped else "."
        )
    return no_store(
        JsonResponse(
            {
                "model": model_data(_get_model(project, restored.id), detail=True),
                "retired_model_id": str(retired.id),
                "apps_still_on_retired_model": _app_refs(lifecycle.apps_using(retired)),
                **_repoint_body(moved),
                "message": message,
            }
        )
    )
