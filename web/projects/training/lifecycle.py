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
import uuid
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from .. import permissions
from ..datasets import service as dataset_service, splits
from ..models import DatasetBox, DatasetVersion, InferenceApp, ModelEvent, ModelVersion, Project, TrainingRun
from . import data_home, get_backend, models_root, report, training_settings
from .base import StartBlocked

MAX_NOTE_CHARS = 2000
MAX_ERROR_CHARS = 600
WORKER_KEY = "training:worker"
INFERENCE_WORKER_KEY = "inference:worker"
HEARTBEAT_TTL_SECONDS = 45
# The readiness preview (``GET .../training/readiness``, polled by the Teach panel) re-checks picture files for at most
# this many seconds per request; the rest are left for the start, which always checks every file.
READINESS_FILE_CHECK_SECONDS = 2
FILES_NOT_RECHECKED_NOTE = (
    "The picture files were not re-checked for this preview, to keep the page quick. Missing or changed files are "
    "checked when you start."
)


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
    start_from, derived, one_click = _one_click_preview(project, backend_name, ok, availability, worker, dataset, active)
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
        # Additive, for the one-click Teach start (``snapshot: "latest"``): what it would start from, the settings it
        # would work out, and whether it could start right now. ``ready`` above is still the old explicit-dataset rule.
        "start_from": start_from,
        "derived": derived,
        "one_click": one_click,
    }


def _one_click_preview(
    project: Project, backend_name: str, backend_ok: bool, availability: dict[str, Any], worker: dict[str, Any],
    latest: DatasetVersion | None, active: TrainingRun | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any]]:
    """Read-only: ``(start_from, derived, one_click)`` for ``GET .../training/readiness``."""
    try:
        backend = get_backend(backend_name)
    except ValueError:
        backend = None
    start_from = backend.resolve_start(worker) if backend else None
    analysis = dataset_service.analyse(
        project, splits.SplitConfig.default(), use_cache=True, file_check=READINESS_FILE_CHECK_SECONDS
    )
    refusal = snapshot_refusal(analysis)
    current = refusal is None and snapshot_is_current(project, latest, analysis)
    would_create = refusal is None and not current
    train: int | None = None
    if analysis.accepted:
        train = _train_images(latest) if current else planned_train_pictures(analysis)
    test: int | None = None
    if analysis.accepted:
        test = _test_images(latest) if current else planned_test_pictures(analysis)
    derived = backend.teach_preview(train, start_from["resolved"] if start_from else None) if backend else None
    if derived is not None:
        derived["based_on"] = None if train is None else ("latest_snapshot" if current else "new_snapshot")

    blockers: list[str] = []
    if not backend_ok:
        blockers.append(
            f"Backend {backend_name!r} is not available. {availability.get('message', '')} {availability.get('how_to_enable', '')}".strip()
        )
    elif start_from is None:
        blockers.append("This training backend cannot pick its own starting model, so it cannot be started in one click.")
    elif start_from["resolved"] is None:
        blockers.extend([start_from["reason_if_none"], start_from["how_to_enable"]])
    if refusal is not None:
        blockers.extend(analysis.blockers if analysis.accepted else [refusal.message])
    elif train == 0:
        blockers.append(NO_PLANNED_TRAIN_MESSAGE)  # the new snapshot's default split would leave nothing in train
    if active:
        blockers.append("A training run is already queued or running for this project. Wait for it or cancel it.")
    # Not a blocker (the run can be taught), but a model with no held-out pictures has no results and so can never be
    # staged or approved: say it where the start button is, not only on the data health page.
    warnings = [NO_HELD_OUT_MESSAGE] if test == 0 else []
    one_click = {
        "supported": start_from is not None,
        "ready": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        # Additive: things the preview could not do. ``ready`` is "nothing that was checked is wrong"; a note here
        # says what was left unchecked, so a True above is never read as more than it is.
        "notes": [] if analysis.files_rechecked else [FILES_NOT_RECHECKED_NOTE],
        "files_rechecked": analysis.files_rechecked,
        "snapshot": {
            "latest_version": latest.version if latest else None,
            "up_to_date": current,
            "would_create_new": would_create,
        },
    }
    return start_from, derived, one_click


def _train_images(dataset: DatasetVersion) -> int:
    return split_counts(dataset).get("train", {}).get("images", 0)


def _test_images(dataset: DatasetVersion) -> int:
    return split_counts(dataset).get("test", {}).get("images", 0)


def prepare_start(
    backend_name: str, config: dict[str, Any], *, train_pictures: int, acknowledged_terms: bool
) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    """Every rule about *how* a run may start, before anything is written: the backend must exist and be runnable,
    ``"auto"`` choices are resolved (never to a from-scratch start), derived settings are filled in, the config is
    validated, the start is checked, and the licence tick is required. Returns ``(backend, config, start_check)``."""
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
    worker = worker_status()
    try:
        prepared = backend.prepare_config(dict(config), train_pictures=train_pictures, worker=worker)
        normalized = backend.validate_config(prepared)
    except StartBlocked as blocked:
        raise LifecycleError(str(blocked), 409, **blocked.details) from None
    except ValueError as error:
        raise LifecycleError(str(error), 400) from None
    # The backend knows whether this start needs the person's tick about licence terms, and whether it can run at
    # all (for example a download that is switched off). The page's tick-box is only a convenience: this is the rule.
    check = backend.start_check(normalized, worker)
    if check.get("blocked"):
        raise LifecycleError(check["blocked"], 409)
    if check.get("needs_acknowledgement") and acknowledged_terms is not True:
        raise LifecycleError(
            "Tick the box to confirm you have read the licence note for the ready-made model, then start again.",
            400,
            needs_acknowledgement=True,
        )
    return backend, normalized, check


def create_run(
    project: Project, user, *, dataset: DatasetVersion, backend_name: str, config: dict[str, Any],
    acknowledged_terms: bool = False, note_extra: str = "",
) -> TrainingRun:
    if dataset.project_id != project.id:
        raise LifecycleError("That dataset version does not belong to this project.", 404)
    if _train_images(dataset) == 0:
        raise LifecycleError(
            f"Dataset version {dataset.version} has no train images. Create a version with a larger train share.", 422
        )
    _, normalized, check = prepare_start(
        backend_name, config, train_pictures=_train_images(dataset), acknowledged_terms=acknowledged_terms
    )
    note = f"Dataset version {dataset.version}, backend {backend_name}."
    if note_extra:
        note += f" {note_extra}"
    if check.get("needs_acknowledgement"):
        mode = normalized.get("pretrained", "a ready-made model")
        picked = ", picked automatically because it could run here" if normalized.get("pretrained_requested") else ""
        note += (
            f" Started from a ready-made model ({mode}{picked}). The person who started it "
            "confirmed they had read its licence note and that they will ask a lawyer before any commercial use."
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


# -- one-click start: freeze the current work if needed, then train on it ---------------------------
def _problem_extras(analysis: dataset_service.Analysis) -> dict[str, Any]:
    limit = dataset_service.PROBLEM_LIMIT
    return {
        "blockers": analysis.blockers,
        "excluded": analysis.excluded,
        "problems": [problem.as_dict() for problem in analysis.problems[:limit]],
        "problem_count": len(analysis.problems),
        "problems_truncated": len(analysis.problems) > limit,
    }


def snapshot_refusal(analysis: dataset_service.Analysis) -> LifecycleError | None:
    """The refusal ``POST .../datasets`` gives when the current work cannot be frozen (same words, same codes)."""
    if not analysis.accepted:
        return LifecycleError(dataset_service.NO_ACCEPTED_MESSAGE, 400, excluded=analysis.excluded)
    if not analysis.ready:
        return LifecycleError(
            "Fix the problems below before creating a dataset version. Nothing was created.", 422, **_problem_extras(analysis)
        )
    return None


def snapshot_is_current(project: Project, version: DatasetVersion | None, analysis: dataset_service.Analysis) -> bool:
    """True when ``version`` already holds exactly the accepted boxes in ``analysis`` (and the same label list).

    ``datasets.service.content_fingerprint`` is not stored on a version, and it includes ``updated_at`` (which an unchanged
    box can bump), so the comparison is on content: each accepted box's id, label, geometry and picture checksum.
    Suggested boxes never count. A version marked incomplete (pictures deleted afterwards) is never current, and
    neither is one whose split left no pictures in train: a run can never start on it (``create_run`` refuses), so
    the one-click start freezes the default split again instead."""
    if version is None or version.incomplete_reason or list(version.label_names) != list(project.labels):
        return False
    if _train_images(version) == 0:
        return False
    frozen = {
        (box_id, label, x, y, width, height, sha)
        for box_id, label, x, y, width, height, sha in DatasetBox.objects.filter(image__version=version).values_list(
            "source_annotation_id", "label", "x", "y", "width", "height", "image__sha256"
        )
    }
    current = {
        (box.id, box.label, box.x, box.y, box.width, box.height, box.asset.sha256) for box in analysis.accepted
    }
    return frozen == current


NO_HELD_OUT_MESSAGE = (
    "No pictures would be kept back to check the result, so you could not approve this model."
)
NO_PLANNED_TRAIN_MESSAGE = (
    "The default split would leave no pictures to train on. Review and accept boxes on a few more pictures, then start again."
)


def snapshot_default_config(backend_name: str, config: dict[str, Any]) -> dict[str, Any]:
    """The one-click start's defaults, under whatever the caller chose: ``pretrained: "auto"`` (best ready-made start
    that can run) and ``quality: "quick"`` (settings worked out from the data).

    They are only added for a backend that understands them, i.e. one that can pick its own starting model
    (``resolve_start`` is not ``None``, the same test the readiness preview uses). Any other backend, and an unknown
    one (which ``prepare_start`` refuses in plain words), is started on exactly what the caller sent, so a request that
    never mentioned these settings is never refused for them. This is deliberately the one-click path only: a legacy
    ``dataset_id`` request gets no ``quality`` unless it asks for one."""
    try:
        backend = get_backend(backend_name)
    except ValueError:
        return dict(config)
    if backend.resolve_start(worker_status()) is None:
        return dict(config)
    return {"pretrained": "auto", "quality": "quick", **config}


def _planned_pictures(analysis: dataset_service.Analysis, split_name: str) -> int:
    """The same assignment the data health page and a new snapshot use (``splits.assign_splits``), counted per group."""
    split_of = splits.assign_splits((asset.sha256 for asset in analysis.assets.values()), analysis.config)
    return sum(1 for asset in analysis.assets.values() if split_of[asset.sha256] == split_name)


def planned_train_pictures(analysis: dataset_service.Analysis) -> int:
    """How many pictures the default split would put in train for ``analysis`` (no write)."""
    return _planned_pictures(analysis, "train")


def planned_test_pictures(analysis: dataset_service.Analysis) -> int:
    """How many pictures the default split would keep back for the final test for ``analysis`` (no write)."""
    return _planned_pictures(analysis, "test")


BOXES_CHANGED_MESSAGE = "Boxes changed while this was starting. Please try again."


def _lock_project(project: Project) -> None:
    """Take the project's write lock for the rest of the surrounding transaction (the lock ``create_version`` takes)."""
    Project.objects.select_for_update().get(pk=project.pk)


def _is_running(project: Project) -> bool:
    return project.training_runs.filter(status__in=TrainingRun.ACTIVE_STATUSES).exists()


ALREADY_ACTIVE_MESSAGE = "A training run is already queued or running for this project. Wait for it to finish or cancel it."


def start_run_on_latest_snapshot(
    project: Project, user, *, backend_name: str, config: dict[str, Any], acknowledged_terms: bool = False
) -> tuple[TrainingRun, DatasetVersion, bool]:
    """Start a run on the current accepted work: reuse the latest dataset version when it already holds exactly that
    work, otherwise freeze a new one (default split) first. Returns ``(run, version, newly_created)``.

    Two phases. First everything slow is done with **no transaction open**: the project is re-read (a caller's
    ``project`` may be stale), the work is analysed and, when a new snapshot is needed, every picture is hashed. Then
    one short transaction takes the project lock and only re-checks and writes: no run already active, the project
    and its boxes still exactly as they were analysed (otherwise 409, nothing written), then the snapshot and the run.
    Hashing never happens under the lock, so other writers (box saves, uploads) are not held up by it.

    All or nothing: any refusal after the snapshot is written (licence tick, a run already active, anything) rolls the
    snapshot back too. On PostgreSQL ``select_for_update`` serialises racing starts; SQLite ignores it (it serialises
    whole writes instead) - the lock is therefore unverified against a real multi-user database."""
    if _is_running(project):  # cheap and early, so a refused start never hashes anything
        raise LifecycleError(ALREADY_ACTIVE_MESSAGE, 409)
    project.refresh_from_db()  # labels may have changed since the caller loaded the project
    labels_seen = list(project.labels)
    latest = latest_dataset(project)
    analysis = dataset_service.analyse(project, splits.SplitConfig.default(), use_cache=True)
    refusal = snapshot_refusal(analysis)
    if refusal:
        raise refusal
    create = not snapshot_is_current(project, latest, analysis)
    # Reusing the current snapshot trusts the check made when it was frozen (that one hashed every file); the look
    # above answers from the stat cache, so a picture changed without its size or modified time changing is not
    # re-hashed on this path. Creating a new snapshot always re-hashes every file (the second ``analyse`` below).
    planned = planned_train_pictures(analysis) if create else _train_images(latest)
    if planned == 0:
        raise LifecycleError(NO_PLANNED_TRAIN_MESSAGE, 422)  # before anything is written
    # Cheap rules first (backend, licence tick, a ready-made start that can run), so a request that cannot start
    # never hashes every picture.
    prepare_start(backend_name, config, train_pictures=planned, acknowledged_terms=acknowledged_terms)
    if create:
        analysis = dataset_service.analyse(project, splits.SplitConfig.default())  # creating always hashes the files
        refusal = snapshot_refusal(analysis)
        if refusal:
            raise refusal
    with transaction.atomic():
        _lock_project(project)
        if _is_running(project):
            raise LifecycleError(ALREADY_ACTIVE_MESSAGE, 409)
        project.refresh_from_db()
        if list(project.labels) != labels_seen or dataset_service.content_fingerprint(project) != analysis.fingerprint:
            raise LifecycleError(BOXES_CHANGED_MESSAGE, 409)
        version = latest
        note = ""
        if create:
            try:
                version = dataset_service.create_version(analysis, user)
            except dataset_service.VersionConflict as error:
                raise LifecycleError(str(error), 409) from None
            note = f"Dataset version {version.version} was frozen automatically from the accepted boxes when this run started."
        run = create_run(
            project, user, dataset=version, backend_name=backend_name, config=config,
            acknowledged_terms=acknowledged_terms, note_extra=note,
        )
    return run, version, create


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

    ``None`` means the results do not trigger the rule. It fires when nothing was evaluated, when none of the test
    boxes were found, when the plain-results verdict (``report.plain_results``) is ``not_ready`` or
    ``cannot_tell``, and when the stored comparison with the model in use is readable, fair, and says ``worse``.
    Otherwise a plain sentence (without the ``acknowledge_weak_results`` instruction) that is also
    written to the audit trail.
    """
    evaluation = evaluation or {}
    if not evaluation.get("available"):
        return "it has no results: nothing was held back to check it on, so nobody can say how well it works"
    overall = evaluation.get("overall") or {}
    if not overall.get("recall"):  # 0.0 (or missing): none of the people-drawn boxes were found at IoU 0.5
        found = overall.get("true_positives", 0)
        total = (evaluation.get("data") or {}).get("boxes", found + overall.get("false_negatives", 0))
        return f"it found none of the test boxes ({found} of {total} at an overlap of 0.5)"
    # The plain-results verdict (the one the page shows): too few test boxes to tell, or too few found. A decided but
    # middling "needs more examples" is a judgement call for the approver and needs only the note.
    results = report.plain_results(evaluation)
    plain = results["overall"]
    if plain["verdict"] in ("not_ready", "cannot_tell"):
        headline = plain["headline"].removesuffix(f" {report.NOT_FINAL_DECISION}").rstrip(". ")
        return f"the results are too thin or too weak to rely on ({headline})"
    # A fair comparison on the same test pictures that says the new model did worse than the model in use. Read
    # through the report's checked reader, which derives the verdict again from the counts: a damaged block is
    # "not available" (it neither triggers nor hides this), an unfair one is "cannot tell", and the stored word is never
    # trusted. "better", "about the same" and "cannot tell" add no friction.
    comparison = results.get("comparison") or {}
    if comparison.get("available") is True and comparison.get("fair") is True and comparison.get("verdict") == "worse":
        said = str(comparison.get("plain") or "").removesuffix(f" {report.NOT_FINAL_DECISION}").rstrip(". ")
        return f"the new model did worse than the model in use on the same test pictures ({said})"
    return None


def approve_model(
    model: ModelVersion, user, note: str, *, acknowledge_weak_results: bool = False,
    repoint_apps: bool = False, repoint_app_ids: list[uuid.UUID] | None = None,
) -> tuple[ModelVersion, ModelVersion | None]:
    """Approve a staged model (retiring the one in use). Check screens are re-pointed to it only when the caller says
    so, in the same transaction: ``repoint_apps`` (every active screen pinned to the model being retired) or
    ``repoint_app_ids`` (exactly those). The result is on ``model.repoint`` (``repointed`` and ``skipped``)."""
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
        moved = _repoint(
            model.project, new=model, old=previous, user=user,
            all_pinned=repoint_apps, app_ids=repoint_app_ids, explicit_note=note,
        )
        audit = note
        if weak:  # the person was told and chose to go on: keep that in the audit trail
            audit = f"{note}\n[Weak results acknowledged by the approver: {weak}. Approved anyway.]"
        if moved.asked:
            audit = f"{audit}\n{moved.summary('at approval')}"
        audit = f"{audit}\n{permissions.audit_tag(user, model.project)}"  # role and opaque user id, never a name
        add_event(
            model.project, "approved", user=user, model=model,
            from_state=ModelVersion.STAGED, to_state=ModelVersion.APPROVED, note=audit,
        )
        moved.write_events(note)
    model.repoint = moved
    return model, previous


def rollback_model(
    model: ModelVersion, user, note: str, *, repoint_apps: bool = False, repoint_app_ids: list[uuid.UUID] | None = None
) -> tuple[ModelVersion, ModelVersion]:
    """Retire the approved ``model`` and restore the model it replaced. As for an approval, check screens move to the
    restored model only when asked (``repoint_apps`` / ``repoint_app_ids``); the result is on ``target.repoint``."""
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
        moved = _repoint(
            model.project, new=target, old=model, user=user,
            all_pinned=repoint_apps, app_ids=repoint_app_ids, explicit_note=note,
        )
        restored_note = f"Restored in place of model v{model.version}. {note}"
        if moved.asked:
            restored_note = f"{restored_note}\n{moved.summary('by the rollback')}"
        add_event(
            model.project, "rollback_restored", user=user, model=target,
            from_state=ModelVersion.RETIRED, to_state=ModelVersion.APPROVED, note=f"{restored_note}\n{audit_tag}",
        )
        moved.write_events(note)
    target.repoint = moved
    return model, target


def apps_using(model: ModelVersion) -> list[InferenceApp]:
    return list(InferenceApp.objects.filter(model_version=model, status=InferenceApp.ACTIVE))


# -- re-pointing check screens as an explicit part of a decision -----------------------------------
MAX_REPOINT_IDS = 100


def repoint_problem(app: InferenceApp, model: ModelVersion) -> str | None:
    """Why ``app`` could not be switched to ``model`` (``None``: it could). The rule the builder's "switch model"
    uses (``inference.service.update_app``) minus the one about the model already being approved, so a staged model can be
    checked before its approval: the model's backend is installed here, and the screen's own, strictly validated
    configuration (the labels it displays) still fits the new model's labels."""
    from ..inference import schema

    if app.project_id != model.project_id:
        return "That model version belongs to a different project."
    try:
        get_backend(model.backend)
    except ValueError:
        return f"The backend {model.backend!r} this model needs is not installed. Install it, then try again."
    try:
        schema.validate_config(schema.merged(app.config, {}), model.label_names)
    except schema.ConfigError as error:
        return str(error)
    return None


def would_repoint(model: ModelVersion) -> list[dict[str, Any]]:
    """Read-only preview for a staged model: the active check screens pinned to the model in use today, each with whether
    approving with ``repoint_apps`` would move it and, if not, why."""
    if model.stage != ModelVersion.STAGED:
        return []
    in_use = ModelVersion.objects.filter(project_id=model.project_id, stage=ModelVersion.APPROVED).exclude(pk=model.pk).first()
    if in_use is None:
        return []
    rows = []
    for app in apps_using(in_use):
        reason = repoint_problem(app, model)
        rows.append({"app_id": str(app.id), "name": app.name, "can_repoint": reason is None, "reason": reason})
    return rows


class Repointed:
    """What one approval or rollback did to check screens: ``asked`` (the caller opted in), ``moved`` and ``skipped``."""

    def __init__(self, project: Project, user, new: ModelVersion, old: ModelVersion | None, asked: bool) -> None:
        self.project, self.user, self.new, self.old, self.asked = project, user, new, old, asked
        self.moved: list[tuple[InferenceApp, ModelVersion | None]] = []  # (the screen, the model it was pinned to)
        self.skipped: list[dict[str, str]] = []

    @property
    def repointed(self) -> list[dict[str, str]]:
        return [{"app_id": str(app.id), "name": app.name} for app, _ in self.moved]

    def summary(self, when: str) -> str:
        return (
            f"[Check screens re-pointed to this model {when}: {len(self.moved)}. "
            f"Left alone: {len(self.skipped)}.]"
        )

    def write_events(self, note: str) -> None:
        """One append-only line per moved screen, on the model it now runs. App names are data (JSON), never prose."""
        tag = permissions.audit_tag(self.user, self.project)
        for app, was in self.moved:
            data = {
                "app_id": str(app.id), "app_name": app.name,
                "from_model_id": str(was.id) if was else None, "from_version": was.version if was else None,
                "to_model_id": str(self.new.id), "to_version": self.new.version,
            }
            add_event(
                self.project, "apps_repointed", user=self.user, model=self.new,
                note=(
                    f"A check screen was re-pointed to model v{self.new.version} at the approver's request.\n"
                    f"{note}\n{json.dumps(data, ensure_ascii=False, sort_keys=True)}\n{tag}"
                ),
            )


def _repoint(
    project: Project, *, new: ModelVersion, old: ModelVersion | None, user, all_pinned: bool,
    app_ids: list[uuid.UUID] | None, explicit_note: str,
) -> Repointed:
    """Inside the caller's transaction, after ``new`` is approved and ``old`` is retired. ``all_pinned`` moves every
    active screen pinned to ``old``; ``app_ids`` moves exactly those (pinned to ``old`` or to a model that cannot run).
    A screen that does not fit is left alone and reported; any other error propagates and undoes the whole decision."""
    from ..inference import service as inference_service

    moved = Repointed(project, user, new, old, asked=bool(all_pinned or app_ids))
    if not moved.asked:
        return moved
    apps = InferenceApp.objects.select_for_update(of=("self",)).select_related("model_version")
    if app_ids:
        found = {app.id: app for app in apps.filter(project=project, pk__in=app_ids)}
        missing = [str(pk) for pk in app_ids if pk not in found]
        if missing:
            raise LifecycleError(
                "repoint_app_ids must be check screens of this project; not recognised: " + ", ".join(missing) + ". "
                "Nothing was changed.", 400,
            )
        chosen = [found[pk] for pk in app_ids]
        for app in chosen:
            pinned = app.model_version
            if pinned.stage == ModelVersion.APPROVED and pinned.pk != new.pk:
                raise LifecycleError(
                    f"The check screen {app.name!r} is using model v{pinned.version}, which is still approved, so it "
                    "is not re-pointed by this decision. Nothing was changed.", 400,
                )
        chosen.sort(key=lambda app: (app.name, str(app.id)))
    else:
        chosen = list(apps.filter(project=project, status=InferenceApp.ACTIVE, model_version=old)) if old else []
    for app in chosen:
        if app.model_version_id == new.pk:
            moved.skipped.append({"app_id": str(app.id), "name": app.name, "reason": "It already uses this model."})
            continue
        was = app.model_version
        try:
            inference_service.update_app(app, model=new)
        except LifecycleError as error:
            moved.skipped.append({"app_id": str(app.id), "name": app.name, "reason": error.message})
            continue
        moved.moved.append((app, was))
    return moved
