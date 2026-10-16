"""Readiness analysis and immutable version creation."""

from __future__ import annotations

import hashlib
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import Max

from ..models import Asset, BoxAnnotation, DatasetBox, DatasetImage, DatasetVersion, Project
from . import athena_version, manifest, max_images, splits, validation

PROBLEM_LIMIT = 200
ACCEPTED = BoxAnnotation.STATUS_ACCEPTED
SUGGESTED = BoxAnnotation.STATUS_SUGGESTED

NO_ACCEPTED_MESSAGE = (
    "There are no accepted boxes to export. Draw or accept at least one box first; "
    "boxes marked Needs review are not ground truth and are never exported."
)
INTEGRITY_MESSAGE = (
    "This dataset version no longer matches its recorded checksum: its stored rows changed after it was created."
)


class VersionConflict(Exception):
    """The project changed while the version was being prepared; the caller should retry."""


@dataclass
class Analysis:
    project: Project
    config: splits.SplitConfig
    accepted: list[BoxAnnotation]
    assets: dict[Any, Asset]
    suggested_boxes: int
    images_total: int
    images_without_accepted: int
    problems: list[validation.Problem] = field(default_factory=list)
    fingerprint: str = ""

    @property
    def excluded(self) -> dict[str, Any]:
        return {
            "suggested_boxes": self.suggested_boxes,
            "images_without_accepted_boxes": self.images_without_accepted,
            "reason": manifest.EXCLUDED_REASON,
        }

    @property
    def blockers(self) -> list[str]:
        if not self.accepted:
            return [NO_ACCEPTED_MESSAGE]
        lines: list[str] = []
        counts = Counter(problem.code for problem in self.problems)
        texts = {
            "label_not_in_project": "{n} box(es) use a label that is not in this project's label list (labels must match exactly, including capital letters).",
            "label_unsupported_characters": "{n} label(s) contain line breaks or control characters and cannot be written to class files.",
            "geometry_out_of_range": "{n} box(es) extend outside their image.",
            "non_positive_size": "{n} box(es) have no width or height.",
            "file_missing": "{n} image file(s) are missing from storage.",
            "hash_mismatch": "{n} image file(s) changed on disk since upload, so their recorded checksum no longer matches.",
            "too_many_images": "{n} problem(s): the project has more images than one dataset version may hold.",
        }
        for code, template in texts.items():
            if counts.get(code):
                lines.append(template.format(n=counts[code]))
        return lines

    @property
    def ready(self) -> bool:
        return bool(self.accepted) and not self.problems


def _fingerprint(project: Project) -> str:
    rows = (
        BoxAnnotation.objects.filter(asset__project=project, status=ACCEPTED)
        .order_by("id")
        .values_list("id", "label", "x", "y", "width", "height", "updated_at", "asset__sha256")
    )
    digest = hashlib.sha256()
    for row in rows:
        digest.update(repr(row).encode())
    return digest.hexdigest()


def analyse(project: Project, config: splits.SplitConfig, *, use_cache: bool = False) -> Analysis:
    """Inspect a project without writing anything.

    ``use_cache`` lets file checks answer from the stat-keyed verification cache. Only the
    read-only readiness view sets it; creating a version always hashes every file.
    """
    fingerprint = _fingerprint(project)
    accepted = list(
        BoxAnnotation.objects.filter(asset__project=project, status=ACCEPTED)
        .select_related("asset")
        .order_by("id")
    )
    assets: dict[Any, Asset] = {}
    for box in accepted:
        assets.setdefault(box.asset_id, box.asset)
    images_total = project.assets.count()
    suggested = BoxAnnotation.objects.filter(asset__project=project, status=SUGGESTED).count()
    analysis = Analysis(
        project=project,
        config=config,
        accepted=accepted,
        assets=assets,
        suggested_boxes=suggested,
        images_total=images_total,
        images_without_accepted=images_total - len(assets),
        fingerprint=fingerprint,
    )
    if not accepted:
        return analysis

    problems = analysis.problems
    problems.extend(validation.unsafe_label_problems(project.labels))
    if len(assets) > max_images():
        problems.append(
            validation.Problem("too_many_images", f"A dataset version can hold at most {max_images()} images.")
        )
    for box in accepted:
        asset_id = str(box.asset_id)
        for found in (validation.label_problem(box.label, project.labels), validation.geometry_problem(box)):
            if found:
                problems.append(
                    validation.Problem(found.code, found.message, asset_id, str(box.id))
                )
    for asset in assets.values():
        found = validation.file_problem(asset.storage_key, asset.sha256, str(asset.id), cached=use_cache)
        if found:
            problems.append(found)
    return analysis


def _images_for(analysis: Analysis) -> list[dict[str, Any]]:
    boxes_by_asset: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for box in analysis.accepted:
        boxes_by_asset[box.asset_id].append(
            manifest.box_entry(
                annotation_id=box.id,
                label=box.label,
                x=box.x,
                y=box.y,
                width=box.width,
                height=box.height,
                origin=box.origin,
                model_name=box.model_name,
                confidence=box.confidence,
                created_by=box.created_by_id,
                reviewed_by=box.reviewed_by_id,
            )
        )
    split_of = splits.assign_splits((asset.sha256 for asset in analysis.assets.values()), analysis.config)
    return [
        manifest.image_entry(
            asset_id=asset.id,
            filename=asset.filename,
            sha256=asset.sha256,
            width=asset.width,
            height=asset.height,
            size_bytes=asset.size_bytes,
            content_type=asset.content_type,
            split=split_of[asset.sha256],
            boxes=boxes_by_asset[asset.id],
        )
        for asset in analysis.assets.values()
    ]


def preview(analysis: Analysis) -> dict[str, Any]:
    """Split / balance / duplicate summary for readiness, built exactly like the manifest's."""
    if not analysis.accepted:
        return {}
    return manifest.summarise(_images_for(analysis), analysis.project.labels)


def create_version(analysis: Analysis, user) -> DatasetVersion:
    """Freeze ``analysis`` as the next version. Caller has checked ``analysis.ready``."""
    project = analysis.project
    images = _images_for(analysis)
    try:
        with transaction.atomic():
            Project.objects.select_for_update().get(pk=project.pk)
            if _fingerprint(project) != analysis.fingerprint:
                raise VersionConflict("Boxes changed while the dataset was being checked. Please try again.")
            number = (project.dataset_versions.aggregate(top=Max("version"))["top"] or 0) + 1
            version = DatasetVersion(
                project=project,
                version=number,
                created_by=user if getattr(user, "is_authenticated", False) else None,
                label_names=list(project.labels),
                image_count=len(images),
                box_count=len(analysis.accepted),
                excluded_suggested_count=analysis.suggested_boxes,
                excluded_image_count=analysis.images_without_accepted,
                split_config=analysis.config.as_dict(),
            )
            document = manifest.build_manifest(
                dataset_id=version.id,
                version=number,
                created_at=version.created_at.isoformat(),
                created_by=version.created_by_id,
                athena_version=athena_version(),
                project_id=project.id,
                project_name=project.name,
                labels=version.label_names,
                split_config=version.split_config,
                excluded=analysis.excluded,
                images=images,
            )
            version.manifest = document
            version.manifest_sha256 = manifest.sha256_hex(document)
            version.save()
            row_by_asset: dict[str, DatasetImage] = {}
            rows = []
            for image in images:
                asset = analysis.assets[_uuid(image["asset_id"])]
                row = DatasetImage(
                    version=version,
                    asset=asset,
                    asset_uuid=asset.id,
                    filename=asset.filename,
                    storage_key=asset.storage_key,
                    content_type=asset.content_type,
                    sha256=asset.sha256,
                    size_bytes=asset.size_bytes,
                    width=asset.width,
                    height=asset.height,
                    split=image["split"],
                )
                row_by_asset[image["asset_id"]] = row
                rows.append(row)
            DatasetImage.objects.bulk_create(rows)
            DatasetBox.objects.bulk_create(
                DatasetBox(
                    image=row_by_asset[str(box.asset_id)],
                    source_annotation_id=box.id,
                    label=box.label,
                    origin=box.origin,
                    model_name=box.model_name,
                    confidence=box.confidence,
                    created_by_id_snapshot=box.created_by_id,
                    reviewed_by_id_snapshot=box.reviewed_by_id,
                    x=box.x,
                    y=box.y,
                    width=box.width,
                    height=box.height,
                )
                for box in analysis.accepted
            )
    except IntegrityError as error:
        raise VersionConflict("Another dataset version was created at the same time. Please try again.") from error
    return version


def _uuid(value: str) -> uuid.UUID:
    return uuid.UUID(value)


def images_from_rows(version: DatasetVersion) -> list[dict[str, Any]]:
    """Rebuild the manifest's ``images`` list from the snapshot tables."""
    entries = []
    for row in version.images.prefetch_related("boxes"):
        entries.append(
            manifest.image_entry(
                asset_id=row.asset_uuid,
                filename=row.filename,
                sha256=row.sha256,
                width=row.width,
                height=row.height,
                size_bytes=row.size_bytes,
                content_type=row.content_type,
                split=row.split,
                boxes=[
                    manifest.box_entry(
                        annotation_id=box.source_annotation_id,
                        label=box.label,
                        x=box.x,
                        y=box.y,
                        width=box.width,
                        height=box.height,
                        origin=box.origin,
                        model_name=box.model_name,
                        confidence=box.confidence,
                        created_by=box.created_by_id_snapshot,
                        reviewed_by=box.reviewed_by_id_snapshot,
                    )
                    for box in row.boxes.all()
                ],
            )
        )
    return manifest.sort_images(entries)


def verify_integrity(version: DatasetVersion) -> list[dict[str, Any]]:
    """Return snapshot images if rows and manifest still match the recorded checksum, else raise."""
    stored = version.manifest
    if manifest.sha256_hex(stored) != version.manifest_sha256:
        raise ValueError(INTEGRITY_MESSAGE)
    images = images_from_rows(version)
    if images != stored.get("images"):
        raise ValueError(INTEGRITY_MESSAGE)
    return images


def file_problems(version: DatasetVersion) -> list[validation.Problem]:
    problems = []
    for row in version.images.all():
        found = validation.file_problem(row.storage_key, row.sha256, str(row.asset_uuid))
        if found:
            problems.append(found)
    return problems
