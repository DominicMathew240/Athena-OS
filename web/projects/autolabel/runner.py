"""Job queue handling shared by the worker command, the API, and tests."""

from __future__ import annotations

import logging
import time
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone
from PIL import Image

from ..models import Asset, AutoLabelJob, BoxAnnotation
from .base import AutoLabeler, normalized_box

logger = logging.getLogger(__name__)

HEARTBEAT_KEY = "autolabel:worker-heartbeat"
HEARTBEAT_TTL_SECONDS = 45
MAX_ERROR_CHARS = 400


WORKER_LOADING = "loading"
WORKER_READY = "ready"


def record_heartbeat(state: str = WORKER_READY) -> None:
    """Tell the console the worker is alive; ``state`` is ``loading`` while models download/load."""
    cache.set(HEARTBEAT_KEY, state, HEARTBEAT_TTL_SECONDS)


def worker_online() -> bool:
    return cache.get(HEARTBEAT_KEY) is not None


def worker_loading() -> bool:
    """True while an online worker is still loading (or downloading) its models."""
    return cache.get(HEARTBEAT_KEY) == WORKER_LOADING


def fail_stale_jobs() -> int:
    """Fail jobs left ``running`` by a worker that died, so the project is not stuck."""
    cutoff = timezone.now() - timedelta(seconds=settings.AUTOLABEL["JOB_TIMEOUT_SECONDS"])
    return AutoLabelJob.objects.filter(status=AutoLabelJob.RUNNING, started_at__lt=cutoff).update(
        status=AutoLabelJob.FAILED,
        finished_at=timezone.now(),
        error="The auto-label worker stopped before this job finished. Start it again to retry.",
    )


def enqueue(project, *, asset=None, only_unlabeled=True, min_confidence=0.35, max_boxes=20, user=None):
    """Create a queued job. Raises ``IntegrityError`` if the project already has an active one."""
    with transaction.atomic():
        return AutoLabelJob.objects.create(
            project=project,
            asset=asset,
            only_unlabeled=only_unlabeled,
            min_confidence=min_confidence,
            max_boxes=max_boxes,
            created_by=user if getattr(user, "is_authenticated", False) else None,
        )


def claim_next_job() -> AutoLabelJob | None:
    with transaction.atomic():
        job = AutoLabelJob.objects.filter(status=AutoLabelJob.QUEUED).order_by("created_at").first()
        if job is None:
            return None
        claimed = AutoLabelJob.objects.filter(pk=job.pk, status=AutoLabelJob.QUEUED).update(
            status=AutoLabelJob.RUNNING, started_at=timezone.now()
        )
        return AutoLabelJob.objects.get(pk=job.pk) if claimed else None


def _target_assets(job: AutoLabelJob) -> list[Asset]:
    assets = Asset.objects.filter(project=job.project)
    if job.asset_id:
        assets = assets.filter(pk=job.asset_id)
    elif job.only_unlabeled:
        assets = assets.exclude(annotations__status=BoxAnnotation.STATUS_ACCEPTED)
    return list(assets.order_by("created_at", "id"))


def _cancelled(job: AutoLabelJob) -> bool:
    return AutoLabelJob.objects.filter(pk=job.pk, cancel_requested=True).exists()


def _skipped_note(names: list[str]) -> str:
    if not names:
        return ""
    shown = ", ".join(names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else "")
    noun = "image" if len(names) == 1 else "images"
    return f"Skipped {len(names)} unreadable {noun} ({shown}). Remove or re-import them."[:MAX_ERROR_CHARS]


def run_job(job: AutoLabelJob, labeler: AutoLabeler) -> AutoLabelJob:
    """Run a claimed job to completion. Never raises: failures are recorded on the job."""
    try:
        labels = list(job.project.labels)
        if not labels:
            raise ValueError("Add at least one label to this project before auto-labelling.")
        assets = _target_assets(job)
        AutoLabelJob.objects.filter(pk=job.pk).update(total_assets=len(assets), model_name=labeler.name)
        proposed = 0
        unreadable: list[str] = []
        for index, asset in enumerate(assets):
            if _cancelled(job):
                AutoLabelJob.objects.filter(pk=job.pk).update(
                    status=AutoLabelJob.CANCELLED, finished_at=timezone.now()
                )
                return AutoLabelJob.objects.get(pk=job.pk)
            started = time.monotonic()
            try:
                with default_storage.open(asset.storage_key, "rb") as handle, Image.open(handle) as opened:
                    opened.load()
                    image = opened.copy()  # `opened` is closed (and unusable) once this block exits
            except (OSError, ValueError) as error:  # missing/corrupt file: skip it, keep the batch going
                logger.warning("Job %s skipped unreadable image %s: %s", job.pk, asset.pk, error)
                unreadable.append(asset.filename)
                AutoLabelJob.objects.filter(pk=job.pk).update(processed_assets=index + 1)
                continue
            proposals = labeler.propose(
                image, labels, min_confidence=job.min_confidence, max_boxes=job.max_boxes
            )
            logger.info(
                "Job %s image %d/%d: %d proposals in %.1fs",
                job.pk, index + 1, len(assets), len(proposals), time.monotonic() - started,
            )
            with transaction.atomic():
                if not Asset.objects.filter(pk=asset.pk).exists():
                    continue
                BoxAnnotation.objects.filter(
                    asset=asset,
                    origin=BoxAnnotation.ORIGIN_MODEL,
                    status=BoxAnnotation.STATUS_SUGGESTED,
                ).delete()
                rows = []
                for proposal in proposals:
                    box = normalized_box(proposal)
                    if box and proposal.label in labels:
                        rows.append(
                            BoxAnnotation(
                                asset=asset,
                                label=proposal.label,
                                origin=BoxAnnotation.ORIGIN_MODEL,
                                status=BoxAnnotation.STATUS_SUGGESTED,
                                confidence=proposal.confidence,
                                model_name=labeler.name,
                                job=job,
                                **box,
                            )
                        )
                BoxAnnotation.objects.bulk_create(rows)
                proposed += len(rows)
            AutoLabelJob.objects.filter(pk=job.pk).update(
                processed_assets=index + 1, boxes_proposed=proposed
            )
            record_heartbeat()
        AutoLabelJob.objects.filter(pk=job.pk).update(
            status=AutoLabelJob.SUCCEEDED,
            finished_at=timezone.now(),
            error=_skipped_note(unreadable),
        )
    except Exception as error:  # noqa: BLE001 - recorded on the job for the operator
        logger.exception("Auto-label job %s failed", job.pk)
        AutoLabelJob.objects.filter(pk=job.pk).update(
            status=AutoLabelJob.FAILED,
            finished_at=timezone.now(),
            error=str(error)[:MAX_ERROR_CHARS] or error.__class__.__name__,
        )
    return AutoLabelJob.objects.get(pk=job.pk)

