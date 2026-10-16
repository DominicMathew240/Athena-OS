"""Dataset versioning: validation, deterministic splits, manifests and export writers.

Only boxes whose status is ``accepted`` count as ground truth. Everything here is
side-effect free except ``service.create_version``, which writes one immutable snapshot.
"""

from __future__ import annotations

from django.conf import settings

FORMATS = ("coco", "yolo")
MANIFEST_SCHEMA = "athena.dataset-manifest/1"
DEFAULT_MAX_IMAGES = 50_000
DEFAULT_EXPORT_MAX_BYTES = 2 * 1024**3


def athena_version() -> str:
    return str(getattr(settings, "ATHENA_VERSION", "0.1.0-dev"))


def max_images() -> int:
    return int(getattr(settings, "DATASET_MAX_IMAGES", DEFAULT_MAX_IMAGES))


def export_max_bytes() -> int:
    return int(getattr(settings, "DATASET_EXPORT_MAX_BYTES", DEFAULT_EXPORT_MAX_BYTES))
