"""Worker side of inference: run queued images through their pinned model version."""

from __future__ import annotations

import logging
import time
from datetime import timedelta

from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone
from PIL import Image

from ..models import ObservationRecord
from ..training import get_backend, training_settings
from ..training.lifecycle import MAX_ERROR_CHARS, sanitize, verify_artifact
from .service import MAX_PREDICTIONS, PREDICTION_FLOOR

logger = logging.getLogger(__name__)


def claim_next_observation() -> ObservationRecord | None:
    with transaction.atomic():
        row = ObservationRecord.objects.filter(status=ObservationRecord.QUEUED).order_by("created_at").first()
        if row is None:
            return None
        claimed = ObservationRecord.objects.filter(pk=row.pk, status=ObservationRecord.QUEUED).update(
            status=ObservationRecord.RUNNING, started_at=timezone.now()
        )
        return ObservationRecord.objects.get(pk=row.pk) if claimed else None


def _fail(queryset, message: str) -> int:
    return queryset.update(status=ObservationRecord.FAILED, finished_at=timezone.now(), error=message)


def fail_orphaned_observations() -> int:
    return _fail(
        ObservationRecord.objects.filter(status=ObservationRecord.RUNNING),
        "The inference worker restarted while this image was being analysed. Submit it again.",
    )


def fail_stale_observations() -> int:
    cutoff = timezone.now() - timedelta(seconds=training_settings()["INFERENCE_TIMEOUT_SECONDS"] * 2)
    return _fail(
        ObservationRecord.objects.filter(status=ObservationRecord.RUNNING, started_at__lt=cutoff),
        "Analysing this image took too long. Submit it again.",
    )


def process_observation(observation: ObservationRecord, backend=None) -> ObservationRecord:
    """Predict for a claimed observation. Never raises; failures are stored on the row."""
    try:
        model = observation.model_version
        backend = backend or get_backend(model.backend)
        artifact = verify_artifact(model)
        with default_storage.open(observation.image_key, "rb") as handle, Image.open(handle) as opened:
            opened.load()
            image = opened.convert("RGB")
        started = time.monotonic()
        found = backend.predict(artifact, image, {"confidence": PREDICTION_FLOOR, "max_boxes": MAX_PREDICTIONS})
        elapsed = time.monotonic() - started
        ordered = sorted(
            (p for p in found if p.label in model.label_names), key=lambda p: -p.score
        )[:MAX_PREDICTIONS]
        predictions = [
            {
                "index": index,
                "label": p.label,
                "score": round(float(p.score), 6),
                "box": {k: round(float(v), 6) for k, v in (("x", p.x), ("y", p.y), ("width", p.width), ("height", p.height))},
            }
            for index, p in enumerate(ordered)
        ]
        logger.info("Observation %s: %d predictions in %.1fs", observation.pk, len(predictions), elapsed)
        ObservationRecord.objects.filter(pk=observation.pk, status=ObservationRecord.RUNNING).update(
            status=ObservationRecord.SUCCEEDED, predictions=predictions, finished_at=timezone.now(), error=""
        )
    except Exception as exc:  # noqa: BLE001 - stored for the operator
        logger.exception("Observation %s failed", observation.pk)
        ObservationRecord.objects.filter(pk=observation.pk, status=ObservationRecord.RUNNING).update(
            status=ObservationRecord.FAILED,
            finished_at=timezone.now(),
            error=sanitize(str(getattr(exc, "message", None) or exc) or exc.__class__.__name__, MAX_ERROR_CHARS),
        )
    return ObservationRecord.objects.get(pk=observation.pk)
