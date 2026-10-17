"""Run and model-stage rules. Every rule is enforced here (server-side), never in a view or the UI.

* Training success creates a ``candidate`` and nothing more.
* candidate -> staged needs a held-out evaluation. staged -> approved needs a person's note.
* Approving retires the previous approved model in the same transaction and keeps a rollback pointer.
* Every run creation, run outcome and stage transition appends a ``ModelEvent``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from .. import permissions
from ..models import DatasetVersion, InferenceApp, ModelEvent, ModelVersion, Project, TrainingRun
from . import data_home, get_backend, models_root, training_settings

MAX_NOTE_CHARS = 2000
MAX_ERROR_CHARS = 600
WORKER_KEY = "training:worker"
INFERENCE_WORKER_KEY = "inference:worker"
HEARTBEAT_TTL_SECONDS = 45


class LifecycleError(Exception):
    """A rule refused the request. ``message`` says what happened and what to do next."""

    def __init__(self, message: str, status: int = 409, **extra: Any) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.extra = extra


# -- text hygiene ----------------------------------------------------------------------------
def sanitize(text: str, limit: int = MAX_ERROR_CHARS) -> str:
    """Drop absolute paths and the home directory from text that may be shown to a client."""
    text = str(text or "")
    for secret in {str(data_home()), str(settings.MEDIA_ROOT), str(Path.home()), os.getcwd()}:
        if secret and secret != "/":
            text = text.replace(secret, "<server>")
    text = re.sub(r"(?<![\w.])/(?:[\w.@+-]+/){2,}[\w.@+-]*", "<path>", text)
    return text[:limit]


def clean_note(note: Any, *, required: bool, what: str = "A note") -> str:
    if note is None:
        note = ""
    if not isinstance(note, str):
        raise LifecycleError("note must be text.", 400)
    note = note.strip()
    if required and not note:
        raise LifecycleError(f"{what} is required. Write a sentence saying why, then try again.", 400)
    if len(note) > MAX_NOTE_CHARS:
        raise LifecycleError(f"The note is too long (limit {MAX_NOTE_CHARS} characters).", 400)
    if any(ord(ch) < 32 and ch not in "\n\r\t" for ch in note):
        raise LifecycleError("The note contains unsupported control characters.", 400)
    return note


# -- worker presence -------------------------------------------------------------------------
def record_worker_heartbeat(
    key: str, state: str = "ready", backends: dict[str, bool] | None = None, extras: dict[str, Any] | None = None
) -> None:
    """``extras`` are facts about the helper's own environment (for example whether it may download weights)."""
    cache.set(key, {"state": state, "backends": backends or {}, **(extras or {})}, HEARTBEAT_TTL_SECONDS)


def worker_status(key: str = WORKER_KEY) -> dict[str, Any]:
    beat = cache.get(key)
    if not isinstance(beat, dict):
        return {"online": False, "loading": False, "backends": {}, "downloads_allowed": None}
    return {
        "online": True,
        "loading": beat.get("state") == "loading",
        "backends": beat.get("backends", {}),
        "downloads_allowed": beat.get("downloads_allowed"),  # None: an older helper that does not say
    }


def backend_runnable(backend_name: str, worker_key: str = WORKER_KEY) -> tuple[bool, dict[str, Any]]:
    """True when the web process *or* a live worker says the backend can run."""
    try:
        backend = get_backend(backend_name)
    except ValueError as error:  # nothing with that name is installed here (for example a plug-in was removed)
        return False, {
            "available_in_web_process": False,
            "worker_online": worker_status(worker_key)["online"],
            "worker_reports_available": None,
            "message": str(error),
            "how_to_enable": "It is not installed: install the package that provides it, then try again.",
        }
    local = backend.check_available()
    worker = worker_status(worker_key)
    reported = worker["backends"].get(backend_name)
    return (local.ok or bool(reported)), {
        "available_in_web_process": local.ok,
        "worker_online": worker["online"],
        "worker_reports_available": reported,
        "message": local.message,
        "how_to_enable": local.how_to_enable,
    }


# -- audit -----------------------------------------------------------------------------------
def add_event(
    project: Project,
    kind: str,
    *,
    user=None,
    model: ModelVersion | None = None,
    run: TrainingRun | None = None,
    from_state: str = "",
    to_state: str = "",
    note: str = "",
) -> ModelEvent:
    authenticated = getattr(user, "is_authenticated", False)
    return ModelEvent.objects.create(
        project=project,
        model_version=model,
        training_run=run,
        kind=kind,
        from_state=from_state,
        to_state=to_state,
        note=note,
        actor=user if authenticated else None,
        actor_id_snapshot=user.pk if authenticated else None,
    )


# -- runs ------------------------------------------------------------------------------------
def config_digest(config: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def latest_dataset(project: Project) -> DatasetVersion | None:
    return project.dataset_versions.order_by("-version").first()


def split_counts(version: DatasetVersion) -> dict[str, dict[str, int]]:
    return (version.manifest or {}).get("split_counts", {})


def readiness(project: Project, backend_name: str | None = None) -> dict[str, Any]:
    backend_name = backend_name or training_settings()["DEFAULT_BACKEND"]
    blockers: list[str] = []
    warnings: list[str] = []
    dataset = latest_dataset(project)
    counts: dict[str, Any] = {}
    if dataset is None:
        blockers.append("There is no dataset version yet. Create one from reviewed (accepted) boxes first.")
    else:
        counts = split_counts(dataset)
        if counts.get("train", {}).get("images", 0) == 0:
            blockers.append(
                f"Dataset version {dataset.version} has no images in its train split, so nothing can be trained on it."
            )
        if counts.get("test", {}).get("images", 0) == 0:
            warnings.append(
                f"Dataset version {dataset.version} has no held-out test images. Training will run, but the model "
                "will have no evaluation and cannot be staged or approved."
            )
    try:
        ok, availability = backend_runnable(backend_name)
    except ValueError as error:
        ok, availability = False, {"message": str(error), "how_to_enable": "Pick one of the installed backends."}
    if not ok:
        blockers.append(
            f"Backend {backend_name!r} is not available. {availability.get('message', '')} {availability.get('how_to_enable', '')}".strip()
        )
    worker = worker_status()
    if ok and not worker["online"]:
        warnings.append(
            "No training worker is running, so a new run will wait in the queue. Start it with `python manage.py train_worker`."
        )
    active = project.training_runs.filter(status__in=TrainingRun.ACTIVE_STATUSES).first()
    if active:
        blockers.append("A training run is already queued or running for this project. Wait for it or cancel it.")
    return {
        "ready": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "backend": {"name": backend_name, **availability},
        "worker": {"online": worker["online"], "loading": worker["loading"]},
        "dataset": None
        if dataset is None
        else {
            "id": str(dataset.id),
            "version": dataset.version,
            "manifest_sha256": dataset.manifest_sha256,
            "split_counts": counts,
        },
        "active_run_id": str(active.id) if active else None,
    }


def create_run(
    project: Project, user, *, dataset: DatasetVersion, backend_name: str, config: dict[str, Any],
    acknowledged_terms: bool = False,
) -> TrainingRun:
    if dataset.project_id != project.id:
        raise LifecycleError("That dataset version does not belong to this project.", 404)
    if split_counts(dataset).get("train", {}).get("images", 0) == 0:
        raise LifecycleError(
            f"Dataset version {dataset.version} has no train images. Create a version with a larger train share.", 422
        )
    try:
        backend = get_backend(backend_name)
    except ValueError as error:
        raise LifecycleError(str(error) + " See GET .../training/backends for the installed ones.", 400) from None
    ok, info = backend_runnable(backend_name)
    if not ok:
        raise LifecycleError(
            f"Backend {backend_name!r} cannot run right now. {info['message']} {info['how_to_enable']}".strip(),
            503,
            backend=backend_name,
        )
    try:
        normalized = backend.validate_config(config)
    except ValueError as error:
        raise LifecycleError(str(error), 400) from None
    # The backend knows whether this start needs the person's tick about licence terms, and whether it can run at
    # all (for example a download that is switched off). The page's tick-box is only a convenience: this is the rule.
    check = backend.start_check(normalized, worker_status())
    if check.get("blocked"):
        raise LifecycleError(check["blocked"], 409)
    if check.get("needs_acknowledgement") and acknowledged_terms is not True:
        raise LifecycleError(
            "Tick the box to confirm you have read the licence note for the ready-made model, then start again.", 400
        )
    note = f"Dataset version {dataset.version}, backend {backend_name}."
    if check.get("needs_acknowledgement"):
        mode = normalized.get("pretrained", "a ready-made model")
        note += (
            f" Started from a ready-made model ({mode}). The person who started it confirmed they had read its licence "
            "note and that they will ask a lawyer before any commercial use."
        )
    try:
        with transaction.atomic():
            run = TrainingRun.objects.create(
                project=project,
                dataset_version=dataset,
                dataset_manifest_sha256=dataset.manifest_sha256,
                backend=backend_name,
                config=normalized,
                config_sha256=config_digest(normalized),
                created_by=user if getattr(user, "is_authenticated", False) else None,
            )
            add_event(
                project, "run_created", user=user, run=run, to_state=TrainingRun.QUEUED, note=note,
            )
    except IntegrityError:
        raise LifecycleError(
            "A training run is already queued or running for this project. Wait for it to finish or cancel it.", 409
        ) from None
    return run


def request_cancel(run: TrainingRun, user) -> TrainingRun:
    with transaction.atomic():
        if run.status == TrainingRun.QUEUED:
            changed = TrainingRun.objects.filter(pk=run.pk, status=TrainingRun.QUEUED).update(
                status=TrainingRun.CANCELLED, finished_at=timezone.now()
            )
            if changed:
                add_event(
                    run.project, "run_finished", user=user, run=run,
                    from_state=TrainingRun.QUEUED, to_state=TrainingRun.CANCELLED, note="Cancelled before it started.",
                )
            else:  # a worker claimed it in between
                TrainingRun.objects.filter(pk=run.pk).update(cancel_requested=True)
        elif run.status == TrainingRun.RUNNING:
            TrainingRun.objects.filter(pk=run.pk).update(cancel_requested=True)
            add_event(run.project, "run_cancel_requested", user=user, run=run, from_state=run.status, to_state=run.status)
    run.refresh_from_db()
    return run


# -- model versions --------------------------------------------------------------------------
def next_version_number(project: Project) -> int:
    return (project.model_versions.aggregate(top=Max("version"))["top"] or 0) + 1


def artifact_file(model: ModelVersion) -> Path:
    """Absolute path of a model's artifact, refusing anything outside the models directory."""
    root = models_root().resolve()
    path = (data_home() / model.artifact_path).resolve()
    if root not in path.parents:
        raise LifecycleError("The stored model location is not valid. Re-train or re-import the model.", 500)
    return path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_artifact(model: ModelVersion) -> Path:
    path = artifact_file(model)
    if not path.is_file():
        raise LifecycleError("This model's file is missing from storage. Re-train the model.", 409)
    if sha256_file(path) != model.artifact_sha256:
        raise LifecycleError(
            "This model's file no longer matches the checksum recorded when it was trained, so it will not be used.", 409
        )
    return path


def _locked(model_id) -> ModelVersion:
    return ModelVersion.objects.select_for_update().select_related("project").get(pk=model_id)


def stage_model(model: ModelVersion, user, note: str = "") -> ModelVersion:
    note = clean_note(note, required=False)
    with transaction.atomic():
        model = _locked(model.pk)
        if model.stage != ModelVersion.CANDIDATE:
            raise LifecycleError(
                f"Only a candidate can be staged; this model is {model.stage}. Nothing was changed.", 409
            )
        if not (model.evaluation or {}).get("available"):
            raise LifecycleError(
                "This model has no held-out evaluation, so it cannot be staged. Create a dataset version with test "
                "images, train again, and review the evaluation report.",
                422,
            )
        model.stage = ModelVersion.STAGED
        model.staged_by = user if getattr(user, "is_authenticated", False) else None
        model.staged_at = timezone.now()
        model.save(update_fields=["stage", "staged_by", "staged_at"])
        add_event(
            model.project, "staged", user=user, model=model,
            from_state=ModelVersion.CANDIDATE, to_state=ModelVersion.STAGED, note=note,
        )
    return model


def weak_results_reason(evaluation: dict[str, Any] | None) -> str | None:
    """Why a model's held-out results are too weak to approve without a deliberate acknowledgement.

    ``None`` means the results do not trigger the rule. Otherwise a plain sentence (without the
    ``acknowledge_weak_results`` instruction) that is also written to the audit trail.
    """
    evaluation = evaluation or {}
    if not evaluation.get("available"):
        return "it has no results: nothing was held back to check it on, so nobody can say how well it works"
    overall = evaluation.get("overall") or {}
    if not overall.get("recall"):  # 0.0 (or missing): none of the people-drawn boxes were found at IoU 0.5
        found = overall.get("true_positives", 0)
        total = (evaluation.get("data") or {}).get("boxes", found + overall.get("false_negatives", 0))
        return f"it found none of the test boxes ({found} of {total} at an overlap of 0.5)"
    return None


def approve_model(
    model: ModelVersion, user, note: str, *, acknowledge_weak_results: bool = False
) -> tuple[ModelVersion, ModelVersion | None]:
    if not getattr(user, "is_authenticated", False):
        raise LifecycleError("Sign in to approve a model.", 401)
    note = clean_note(note, required=True, what="An approval note")
    with transaction.atomic():
        model = _locked(model.pk)
        if model.stage != ModelVersion.STAGED:
            raise LifecycleError(
                f"Only a staged model can be approved; this model is {model.stage}. Stage it after reading its "
                "evaluation report. Nothing was changed.",
                409,
            )
        if permissions.requires_second_person():
            run = model.training_run
            if run is not None and run.created_by_id is not None and run.created_by_id == user.pk:
                raise LifecycleError(
                    "You started the teaching run for this model, and this console is set so that someone else has "
                    "to approve it. Ask another Approver. Nothing was changed.",
                    403,
                )
        weak = weak_results_reason(model.evaluation)
        if weak and not acknowledge_weak_results:
            raise LifecycleError(
                f"This model cannot be approved yet: {weak}. If you still want to use it, tick the box that says you "
                "understand this and try again (API clients: send acknowledge_weak_results: true). Nothing was changed.",
                409,
                needs_acknowledgement=True,
                weak_results=weak,
            )
        verify_artifact(model)
        previous = (
            ModelVersion.objects.select_for_update()
            .filter(project=model.project, stage=ModelVersion.APPROVED)
            .exclude(pk=model.pk)
            .first()
        )
        now = timezone.now()
        if previous:
            previous.stage = ModelVersion.RETIRED
            previous.retired_at = now
            previous.save(update_fields=["stage", "retired_at"])
            add_event(
                model.project, "retired", user=user, model=previous,
                from_state=ModelVersion.APPROVED, to_state=ModelVersion.RETIRED,
                note=f"Replaced by model v{model.version}.",
            )
        model.stage = ModelVersion.APPROVED
        model.approved_by = user
        model.approved_at = now
        model.approval_note = note
        model.previous_approved = previous
        model.save(update_fields=["stage", "approved_by", "approved_at", "approval_note", "previous_approved"])
        audit = note
        if weak:  # the person was told and chose to go on: keep that in the audit trail
            audit = f"{note}\n[Weak results acknowledged by the approver: {weak}. Approved anyway.]"
        audit = f"{audit}\n{permissions.audit_tag(user, model.project)}"  # role and opaque user id, never a name
        add_event(
            model.project, "approved", user=user, model=model,
            from_state=ModelVersion.STAGED, to_state=ModelVersion.APPROVED, note=audit,
        )
    return model, previous


def rollback_model(model: ModelVersion, user, note: str) -> tuple[ModelVersion, ModelVersion]:
    """Retire the approved ``model`` and restore the model it replaced."""
    if not getattr(user, "is_authenticated", False):
        raise LifecycleError("Sign in to roll back a model.", 401)
    note = clean_note(note, required=True, what="A rollback note")
    audit_tag = permissions.audit_tag(user, model.project_id)
    with transaction.atomic():
        model = _locked(model.pk)
        if model.stage != ModelVersion.APPROVED:
            raise LifecycleError(
                f"Only the currently approved model can be rolled back; this model is {model.stage}.", 409
            )
        target = (
            ModelVersion.objects.select_for_update().filter(pk=model.previous_approved_id).first()
            if model.previous_approved_id
            else None
        )
        if target is None or target.stage != ModelVersion.RETIRED:
            raise LifecycleError(
                "There is no earlier approved model to return to. Approve another staged model instead.", 409
            )
        verify_artifact(target)
        now = timezone.now()
        model.stage = ModelVersion.RETIRED
        model.retired_at = now
        model.save(update_fields=["stage", "retired_at"])
        add_event(
            model.project, "rollback_retired", user=user, model=model,
            from_state=ModelVersion.APPROVED, to_state=ModelVersion.RETIRED, note=f"{note}\n{audit_tag}",
        )
        target.stage = ModelVersion.APPROVED
        target.retired_at = None
        target.save(update_fields=["stage", "retired_at"])
        add_event(
            model.project, "rollback_restored", user=user, model=target,
            from_state=ModelVersion.RETIRED, to_state=ModelVersion.APPROVED,
            note=f"Restored in place of model v{model.version}. {note}\n{audit_tag}",
        )
    return model, target


def apps_using(model: ModelVersion) -> list[InferenceApp]:
    return list(InferenceApp.objects.filter(model_version=model, status=InferenceApp.ACTIVE))
