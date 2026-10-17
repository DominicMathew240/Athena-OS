"""Build the canonical dataset manifest and its checksum.

``manifest_sha256`` is the SHA-256 of the canonical JSON (sorted keys, no spaces, UTF-8)
of the ``dataset`` document below. It does not depend on the export format.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from decimal import Decimal
from typing import Any

from . import MANIFEST_SCHEMA
from .splits import SPLIT_NAMES

EXCLUDED_REASON = (
    "Boxes marked Needs review are model proposals that nobody has accepted yet, "
    "so they are not ground truth and were left out."
)
MAX_DUPLICATE_EXAMPLES = 50
Q8 = Decimal("0.00000001")


def opaque_user(user_id: int | None) -> str | None:
    """Users appear in exports only as ``u<id>``; never an email or username."""
    return None if user_id is None else f"u{user_id}"


def coordinate(value: Any) -> str:
    return format(Decimal(str(value)).quantize(Q8), "f")


def canonical_json(document: Any) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_hex(document: Any) -> str:
    return hashlib.sha256(canonical_json(document)).hexdigest()


def box_entry(
    *, annotation_id, label, x, y, width, height, origin, model_name, confidence, created_by, reviewed_by
) -> dict[str, Any]:
    return {
        "annotation_id": str(annotation_id),
        "label": label,
        "x": coordinate(x),
        "y": coordinate(y),
        "width": coordinate(width),
        "height": coordinate(height),
        "origin": origin,
        "model_name": model_name,
        "confidence": confidence,
        "created_by": opaque_user(created_by),
        "reviewed_by": opaque_user(reviewed_by),
    }


def image_entry(*, asset_id, filename, sha256, width, height, size_bytes, content_type, split, boxes) -> dict[str, Any]:
    return {
        "asset_id": str(asset_id),
        "source_filename": filename,
        "sha256": sha256,
        "width": width,
        "height": height,
        "size_bytes": size_bytes,
        "content_type": content_type,
        "split": split,
        "boxes": sorted(boxes, key=lambda item: item["annotation_id"]),
    }


def sort_images(images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(images, key=lambda item: (item["sha256"], item["asset_id"]))


SPLIT_TITLES = {"train": "Training", "val": "Checking", "test": "Final test"}
EMPTY_SPLIT_ADVICE = {
    "train": "add more pictures so the model has something to learn from.",
    "val": "add more pictures to follow how well the model is learning.",
    "test": "add more pictures to check how well the model does.",
}


def summarise(
    images: list[dict[str, Any]],
    labels: list[str],
    split_notes: list[str] | None = None,
    requested: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Split counts, class balance, duplicate groups and plain-language warnings."""
    split_counts = {name: {"images": 0, "boxes": 0} for name in SPLIT_NAMES}
    balance = {label: {**{name: 0 for name in SPLIT_NAMES}, "total": 0} for label in labels}
    by_sha: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for image in images:
        split_counts[image["split"]]["images"] += 1
        split_counts[image["split"]]["boxes"] += len(image["boxes"])
        by_sha[image["sha256"]].append(image)
        for box in image["boxes"]:
            row = balance.setdefault(box["label"], {**{name: 0 for name in SPLIT_NAMES}, "total": 0})
            row[image["split"]] += 1
            row["total"] += 1

    groups = [group for group in by_sha.values() if len(group) > 1]
    groups.sort(key=lambda group: group[0]["sha256"])
    duplicates = {
        "groups": len(groups),
        "images_in_groups": sum(len(group) for group in groups),
        "leaks": sum(1 for group in groups if len({item["split"] for item in group}) > 1),
        "note": "Only byte-identical files (same SHA-256) are detected; near-duplicates are not.",
        "examples": [
            {
                "sha256": group[0]["sha256"],
                "asset_ids": sorted(item["asset_id"] for item in group),
                "split": group[0]["split"],
            }
            for group in groups[:MAX_DUPLICATE_EXAMPLES]
        ],
    }

    warnings: list[str] = []
    for name in SPLIT_NAMES:
        # A group the person set to 0% is empty on purpose; only warn about groups they asked for.
        if split_counts[name]["images"] == 0 and (requested is None or (requested.get(name) or 0) > 0):
            warnings.append(f"{SPLIT_TITLES[name]} has 0 pictures - {EMPTY_SPLIT_ADVICE[name]}")
    for label in labels:
        row = balance[label]
        if row["total"] == 0:
            warnings.append(f"Label {label!r} has no accepted boxes.")
        elif row["train"] == 0:
            warnings.append(f"Label {label!r} has no examples in the train split.")
    return {
        "split_counts": split_counts,
        "class_balance": balance,
        "duplicates": duplicates,
        "warnings": warnings,
        # Why a group is empty when there are too few different pictures; actual counts are split_counts.
        "split_notes": list(split_notes or []),
    }


def build_manifest(
    *,
    dataset_id,
    version: int,
    created_at: str,
    created_by: int | None,
    athena_version: str,
    project_id,
    project_name: str,
    labels: list[str],
    split_config: dict[str, Any],
    excluded: dict[str, Any],
    images: list[dict[str, Any]],
    split_notes: list[str] | None = None,
) -> dict[str, Any]:
    images = sort_images(images)
    return {
        "schema": MANIFEST_SCHEMA,
        "dataset_id": str(dataset_id),
        "version": version,
        "created_at": created_at,
        "created_by": opaque_user(created_by),
        "athena_version": athena_version,
        "project": {"id": str(project_id), "name": project_name},
        "label_names": list(labels),
        "split_config": split_config,
        "counts": {"images": len(images), "boxes": sum(len(image["boxes"]) for image in images)},
        "excluded": excluded,
        **summarise(images, labels, split_notes, split_config),
        "images": images,
    }
