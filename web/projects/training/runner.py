"""Worker side of training: claim a queued run, export the dataset, train, evaluate, register a candidate.

The web process never imports this module's heavy dependencies; ``backend.train`` imports torch
only inside the worker (``manage.py train_worker``).
"""

from __future__ import annotations

import logging
import platform
import shutil
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone

from ..datasets import service as dataset_service, writers
from ..models import ModelVersion, TrainingRun
from . import evaluation as evaluation_lib, models_root, scratch_root, training_settings
from .base import BackendUnavailable, EvalItem, TrainingBackend, TrainingCancelled
from .lifecycle import (
    MAX_ERROR_CHARS,
    WORKER_KEY,
    add_event,
    next_version_number,
    record_worker_heartbeat,
    sanitize,
    sha256_file,
)

logger = logging.getLogger(__name__)
PROGRESS_WRITE_INTERVAL = 2.0
EVAL_CONFIDENCE = evaluation_lib.DEFAULT_OPERATING_CONFIDENCE


class TrainingTimedOut(Exception):
    pass


# -- queue ---------------------------------------------------------------------------------
def claim_next_run() -> TrainingRun | None:
    with transaction.atomic():
        run = TrainingRun.objects.filter(status=TrainingRun.QUEUED).order_by("created_at").first()
        if run is None:
            return None
        now = timezone.now()
        claimed = TrainingRun.objects.filter(pk=run.pk, status=TrainingRun.QUEUED).update(
            status=TrainingRun.RUNNING, started_at=now, heartbeat_at=now
        )
        return TrainingRun.objects.get(pk=run.pk) if claimed else None


def _fail_runs(queryset, message: str) -> int:
    count = 0
    for run in queryset:
        changed = TrainingRun.objects.filter(pk=run.pk, status=TrainingRun.RUNNING).update(
            status=TrainingRun.FAILED, finished_at=timezone.now(), error=message
        )
        if changed:
            count += 1
            add_event(run.project, "run_finished", run=run, from_state=TrainingRun.RUNNING,
                      to_state=TrainingRun.FAILED, note=message)
    return count


def fail_stale_runs() -> int:
    """Mark runs left ``running`` by a worker that died (no heartbeat, or over the time limit) as failed."""
    conf = training_settings()
    now = timezone.now()
    stale = now - timedelta(seconds=conf["STALE_HEARTBEAT_SECONDS"])
    too_old = now - timedelta(seconds=conf["RUN_TIMEOUT_SECONDS"])
    running = TrainingRun.objects.filter(status=TrainingRun.RUNNING).select_related("project")
    ids = [
        run.pk
        for run in running
        if (run.heartbeat_at or run.started_at or now) < stale or (run.started_at and run.started_at < too_old)
    ]
    return _fail_runs(
        running.filter(pk__in=ids),
        "The training worker stopped before this run finished. Start it again and queue a new run.",
    )


def fail_orphaned_runs() -> int:
    """At worker start-up (one worker per deployment) nothing can legitimately be ``running``."""
    return _fail_runs(
        TrainingRun.objects.filter(status=TrainingRun.RUNNING).select_related("project"),
        "The training worker restarted while this run was in progress. Queue a new run.",
    )


# -- dataset export --------------------------------------------------------------------------
def export_dataset(run: TrainingRun, backend: TrainingBackend, dest: Path) -> list[EvalItem]:
    """Write the frozen dataset version to ``dest`` (verifying checksums) and return the test items."""
    version = run.dataset_version
    images = dataset_service.verify_integrity(version)  # raises ValueError if rows changed
    problems = dataset_service.file_problems(version)
    if problems:
        raise ValueError(
            f"{len(problems)} image(s) in dataset version {version.version} are missing or changed on disk, "
            "so it cannot be used for training. Create a new dataset version."
        )
    storage_keys = {str(row.asset_uuid): row.storage_key for row in version.images.all()}
    files = writers.build_files(backend.dataset_format, version.manifest, version.manifest_sha256, storage_keys)
    dest.mkdir(parents=True, exist_ok=True)
    for item in files:
        target = dest / item.path
        if dest.resolve() not in target.resolve().parents:
            raise ValueError("Unsafe path in dataset export.")
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(item, writers.TextFile):
            target.write_bytes(item.data)
        else:
            with default_storage.open(item.storage_key, "rb") as source, open(target, "wb") as sink:
                shutil.copyfileobj(source, sink)
            if sha256_file(target) != item.sha256:
                raise ValueError("An image changed while the dataset was being prepared. Try again.")
    return test_items(version.manifest, dest)


def _split_items(manifest: dict[str, Any], dest: Path, split: str) -> list[EvalItem]:
    grouped = writers._by_split(manifest["images"])  # noqa: SLF001 - same package, same naming rule
    items = []
    for index, image in grouped.get(split, []):
        name = writers.image_name(index, image)
        boxes = [
            (box["label"], float(box["x"]), float(box["y"]), float(box["width"]), float(box["height"]))
            for box in image["boxes"]
        ]
        items.append(
            EvalItem(dest / "images" / split / name, boxes, image["width"], image["height"], str(image["asset_id"]))
        )
    return items


def test_items(manifest: dict[str, Any], dest: Path) -> list[EvalItem]:
    """Held-out images with ground truth, in the same order/naming the writers used."""
    return _split_items(manifest, dest, "test")


def val_items(manifest: dict[str, Any], dest: Path) -> list[EvalItem]:
    """The validation ("Checking") images with ground truth, built exactly like ``test_items``. Used only to choose
    confidence thresholds (``operating_points``); the model was not trained on the test split but may have been
    steered by these, which is why numbers about new pictures come from the test split."""
    return _split_items(manifest, dest, "val")


def work_out_operating_points(
    backend, artifact: Path, val: list[EvalItem], test: list[EvalItem], labels, log, check=None
) -> dict | None:
    """Thresholds and score wording from the validation split (see ``operating_points``). Optional and additive:
    ``None`` when there are no validation pictures, the backend cannot score them, or anything goes wrong - the
    run never fails because of it, and the evaluation then simply has no ``operating_points`` key.

    ``check`` (the run's cancel and time-limit test) is called before each pass over a split and raises
    ``TrainingCancelled`` or ``TrainingTimedOut``; those end the run instead of being swallowed here. A pass over one
    split cannot be interrupted part-way."""
    from . import operating_points

    if not val:
        return None
    if check:
        check()
    log.add("Working out which confidence settings suit the Checking pictures")
    log.flush(force=True)

    def step(message: str) -> None:
        if check:
            check()
        log.add(message)
        log.flush(force=True)

    try:
        return operating_points.collect(
            backend, artifact, val, test, labels, {"confidence": EVAL_CONFIDENCE, "max_boxes": 100}, step=step
        )
    except (TrainingCancelled, TrainingTimedOut):
        raise
    except Exception as exc:  # noqa: BLE001 - recorded in the run log; the held-out evaluation stands without it
        logger.exception("Operating points failed for run %s", log.run.pk)
        log.add(f"Could not work out suggested thresholds: {sanitize(str(exc) or exc.__class__.__name__, 200)}")
        return None


def work_out_comparison(
    run: TrainingRun, backend, artifact: Path, test: list[EvalItem], labels, evaluation: dict, log, check=None
) -> dict | None:
    """How the new model compares with the model in use on the same Final test pictures (see ``comparison``).

    Optional and additive, exactly like ``work_out_operating_points``: ``None`` (the evaluation then simply has no
    ``comparison`` key) when there is no model in use, no test split, or the model in use cannot run here, and also
    when anything goes wrong - the run never fails because of it. ``check`` (cancel and time limit) is called before
    each pass and ``TrainingCancelled`` / ``TrainingTimedOut`` end the run instead of being swallowed. The only extra
    cost is one prediction pass of the model in use over the test split."""
    from . import comparison

    if not test or not evaluation.get("available"):
        return None
    try:
        return comparison.compare_with_approved(
            run.project,
            comparison.Candidate(backend, artifact, evaluation, list(labels), run.dataset_version.manifest),
            test,
            thresholds={"confidence": EVAL_CONFIDENCE, "max_boxes": 100},
            log=log,
            check=check,
        )
    except (TrainingCancelled, TrainingTimedOut):
        raise
    except Exception as exc:  # noqa: BLE001 - recorded in the run log; the held-out evaluation stands without it
        logger.exception("Comparison with the model in use failed for run %s", log.run.pk)
        log.add(f"Could not compare with the model in use: {sanitize(str(exc) or exc.__class__.__name__, 200)}")
        return None


# -- logging buffer --------------------------------------------------------------------------
class RunLog:
    def __init__(self, run: TrainingRun) -> None:
        self.run = run
        self.lines: list[str] = []
        self.progress = 0.0
        self._last_write = 0.0
        self.limit = training_settings()["MAX_LOG_CHARS"]

    def add(self, message: str) -> None:
        self.lines.append(f"{timezone.now():%H:%M:%S} {sanitize(message, 300)}")
        text = "\n".join(self.lines)
        if len(text) > self.limit:  # keep the newest lines
            text = text[-self.limit :]
            self.lines = text.split("\n")[1:]

    def flush(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_write < PROGRESS_WRITE_INTERVAL:
            return
        self._last_write = now
        TrainingRun.objects.filter(pk=self.run.pk).update(
            logs="\n".join(self.lines), progress=round(self.progress, 4), heartbeat_at=timezone.now()
        )


def runtime_snapshot(backend: TrainingBackend) -> dict[str, Any]:
    import os

    return {
        "system": platform.system(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "backend": backend.name,
        **backend.runtime_info(),
    }


def _cancelled(run: TrainingRun) -> bool:
    return TrainingRun.objects.filter(pk=run.pk, cancel_requested=True).exists()


# -- the run ---------------------------------------------------------------------------------
def run_training(run: TrainingRun, backend: TrainingBackend | None = None) -> TrainingRun:
    """Run a claimed run to completion. Never raises: outcomes are recorded on the run."""
    from . import get_backend

    work = scratch_root() / str(run.id)
    log = RunLog(run)
    started = time.monotonic()
    timeout = training_settings()["RUN_TIMEOUT_SECONDS"]
    outcome, error = TrainingRun.FAILED, ""
    try:
        backend = backend or get_backend(run.backend)
        availability = backend.check_available()
        if not availability.ok:
            raise BackendUnavailable(f"{availability.message} {availability.how_to_enable}".strip())
        TrainingRun.objects.filter(pk=run.pk).update(runtime_info=runtime_snapshot(backend))
        log.add(f"Preparing dataset version {run.dataset_version.version}")
        log.flush(force=True)
        work.mkdir(parents=True, exist_ok=True)
        dataset_dir = work / "dataset"
        items = export_dataset(run, backend, dataset_dir)
        labels = list(run.dataset_version.label_names)
        log.add(f"Dataset ready: {run.dataset_version.image_count} images, {len(items)} held-out test images")

        def check_limits() -> None:
            if _cancelled(run):
                raise TrainingCancelled("Training was cancelled.")
            if time.monotonic() - started > timeout:
                raise TrainingTimedOut(f"The run exceeded the time limit of {timeout} seconds.")

        def progress(fraction: float, message: str) -> None:
            if time.monotonic() - started > timeout:
                raise TrainingTimedOut(f"The run exceeded the time limit of {timeout} seconds.")
            log.progress = min(0.9, max(0.0, fraction) * 0.9)
            log.add(message)
            log.flush()

        result = backend.train(dataset_dir, dict(run.config), progress, lambda: _cancelled(run))
        if _cancelled(run):
            raise TrainingCancelled("Training was cancelled.")
        log.progress = 0.9
        log.add("Training finished; evaluating on held-out test images")
        log.flush(force=True)
        boxes_per_label = {label: 0 for label in labels}
        for item in items:
            for label, *_ in item.boxes:
                boxes_per_label[label] = boxes_per_label.get(label, 0) + 1
        if items:
            evaluation = backend.evaluate(
                Path(result.artifact_path), items, labels, {"confidence": EVAL_CONFIDENCE, "max_boxes": 100}
            )
        else:
            evaluation = evaluation_lib.not_available(
                "The dataset version's test split has no images, so there is nothing held out to measure against.",
                0,
                boxes_per_label,
            )
        evaluation = {
            **evaluation,
            "dataset": {
                "version": run.dataset_version.version,
                "manifest_sha256": run.dataset_manifest_sha256,
            },
        }
        points = work_out_operating_points(
            backend, Path(result.artifact_path), val_items(run.dataset_version.manifest, dataset_dir), items, labels, log,
            check=check_limits,
        )
        if points is not None:
            evaluation["operating_points"] = points
        compared = work_out_comparison(
            run, backend, Path(result.artifact_path), items, labels, evaluation, log, check=check_limits
        )
        if compared is not None:
            evaluation["comparison"] = compared
        register_candidate(run, result.artifact_path, list(result.label_names or labels), evaluation, result.metrics)
        outcome = TrainingRun.SUCCEEDED
        log.progress = 1.0
        log.add("Done. The model is a candidate; it is not in use until a person stages and approves it.")
    except TrainingCancelled:
        outcome, error = TrainingRun.CANCELLED, "Cancelled by request."
        log.add(error)
    except Exception as exc:  # noqa: BLE001 - recorded on the run for the operator
        logger.exception("Training run %s failed", run.pk)
        outcome = TrainingRun.FAILED
        error = sanitize(str(exc) or exc.__class__.__name__, MAX_ERROR_CHARS)
        log.add(f"Failed: {error}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    changed = TrainingRun.objects.filter(pk=run.pk, status=TrainingRun.RUNNING).update(
        status=outcome, finished_at=timezone.now(), error=error, logs="\n".join(log.lines),
        progress=round(log.progress, 4),
    )
    if changed:
        add_event(run.project, "run_finished", run=run, from_state=TrainingRun.RUNNING, to_state=outcome, note=error)
    return TrainingRun.objects.get(pk=run.pk)


def register_candidate(
    run: TrainingRun, artifact: Path, labels: list[str], evaluation: dict[str, Any], metrics: dict[str, Any]
) -> ModelVersion:
    """Move the artifact under the data home, hash it, and create a ``candidate`` - never anything more."""
    import uuid

    model_id = uuid.uuid4()
    relative = f"models/{run.project_id}/{model_id}/model.bin"
    target = models_root().parent / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(artifact, target)
    target.chmod(0o600)
    with transaction.atomic():
        model = ModelVersion.objects.create(
            id=model_id,
            project=run.project,
            version=next_version_number(run.project),
            training_run=run,
            backend=run.backend,
            artifact_path=relative,
            artifact_sha256=sha256_file(target),
            artifact_size_bytes=target.stat().st_size,
            label_names=labels,
            evaluation=evaluation,
            stage=ModelVersion.CANDIDATE,
        )
        TrainingRun.objects.filter(pk=run.pk).update(
            metrics={**metrics, "evaluation_available": bool(evaluation.get("available"))}
        )
        add_event(
            run.project, "candidate_created", model=model, run=run, to_state=ModelVersion.CANDIDATE,
            note="Training finished. This model is not in use until a person stages and approves it.",
        )
    return model


def heartbeat(state: str, backend_names: dict[str, bool] | None = None) -> None:
    # The helper is the process that downloads ready-made models, so it says whether it is allowed to.
    from .torchvision_frcnn import downloads_allowed

    record_worker_heartbeat(WORKER_KEY, state, backend_names, {"downloads_allowed": downloads_allowed()})
