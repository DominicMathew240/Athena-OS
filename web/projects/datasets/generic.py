"""Frozen dataset versions for non-image modalities (generic entries instead of images+boxes).

Mirrors :mod:`projects.datasets.service` step for step - readiness analysis, deterministic
splits, a canonical manifest with a checksum, immutable snapshot rows - but freezes one
:class:`~projects.models.DatasetEntry` per asset, carrying the accepted generic annotation
(``AssetAnnotation.data``) verbatim instead of boxes. Image projects never reach this module;
their path is unchanged.

Splits reuse :mod:`projects.datasets.splits` unchanged, keyed on the asset's SHA-256 exactly
like images: byte-identical files land in the same split, 80/10/10 by default, and the same
min-1 rule applies (with at least as many distinct files as requested groups, every group gets
one; below that a group stays empty and the manifest says so - ``shortfall_notes`` speaks of
"pictures" because it is shared with the image path).

The manifest keeps the image document's shape (``schema``, ``label_names``, ``split_config``,
``excluded``, per-split counts, warnings) with the per-item list named ``"entries"`` (never
``"images"``) and top-level counts ``entry_count`` / ``annotation_count`` / ``total_bytes``.
``DatasetVersion.image_count`` / ``box_count`` are always 0 for generic versions; the real
numbers live in the manifest, which is what the checksum covers.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import Max

from ..models import Asset, AssetAnnotation, DatasetEntry, DatasetVersion, Project
from . import athena_version, manifest, splits, validation
from .writers import ImageFile, TextFile, stream_zip

ACCEPTED = AssetAnnotation.STATUS_ACCEPTED
SUGGESTED = AssetAnnotation.STATUS_SUGGESTED
PROBLEM_LIMIT = 200

NO_ACCEPTED_MESSAGE = (
    "There are no accepted labels to export. Label at least one file first; labels marked "
    "Needs review are not ground truth and are never exported."
)
INTEGRITY_MESSAGE = (
    "This dataset version no longer matches its recorded checksum: its stored rows changed after it was created."
)
EXCLUDED_REASON = (
    "Labels marked Needs review are model proposals that nobody has accepted yet, so they are "
    "not ground truth and were left out."
)
EXPORT_FORMAT = "manifest"


class VersionConflict(Exception):
    """The project changed while the version was being prepared; the caller should retry."""


@dataclass
class GenericAnalysis:
    project: Project
    config: splits.SplitConfig
    assets: list[Asset]
    accepted: dict[Any, AssetAnnotation]
    suggested: int
    assets_total: int
    problems: list[validation.Problem] = field(default_factory=list)
    fingerprint: str = ""

    @property
    def ready(self) -> bool:
        return bool(self.accepted) and not self.problems

    @property
    def blockers(self) -> list[str]:
        if not self.accepted:
            return [NO_ACCEPTED_MESSAGE]
        lines: list[str] = []
        counts = Counter(problem.code for problem in self.problems)
        texts = {
            "label_unsupported_characters": (
                "{n} label(s) contain line breaks or control characters and cannot be exported."
            ),
            "annotation_kind_mismatch": "{n} label(s) are of a kind this project's data type does not use.",
            "asset_without_accepted": "{n} file(s) have no accepted label; label them or delete them first.",
            "file_missing": "{n} file(s) are missing from storage.",
            "hash_mismatch": "{n} file(s) changed on disk since upload, so their recorded checksum no longer matches.",
        }
        for code, template in texts.items():
            if counts.get(code):
                lines.append(template.format(n=counts[code]))
        return lines

    @property
    def excluded(self) -> dict[str, Any]:
        return {
            "suggested_annotations": self.suggested,
            "assets_without_accepted": self.assets_total - len(self.accepted),
            "reason": EXCLUDED_REASON,
        }


def expected_annotation_kind(project: Project) -> str:
    """The annotation kind this project's labels are expected to carry.

    The recipe's kind when the project has one, else the modality plugin's; "" when neither
    exists (the view refuses those projects before reaching here)."""
    from ..modalities import get_plugin
    from ..tasks import recipe_for_project

    recipe = recipe_for_project(project)
    if recipe is not None:
        return recipe.annotation_kind
    plugin = get_plugin(project.modality)
    return plugin.annotation_kind if plugin else ""


def primary_label(annotation: AssetAnnotation, project_labels: list[str]) -> str:
    """The project label an annotation names, or "" when it names no single one.

    Convention, stated for the Wave 1 agents: an annotation whose ``data`` holds a ``label``
    string (the shape every current plugin normalizes to) gets that string as its primary
    label; any other shape has none. Whatever the data holds is frozen verbatim regardless."""
    data = annotation.data if isinstance(annotation.data, dict) else None
    label = data.get("label") if data else None
    return label if isinstance(label, str) and label in project_labels else ""


def _accepted_annotations(project: Project):
    """The accepted generic annotations of this project, oldest first, with their assets loaded."""
    return (
        AssetAnnotation.objects.filter(asset__project=project, status=ACCEPTED)
        .order_by("id")
        .select_related("asset")
    )


def content_fingerprint(project: Project) -> str:
    """A hash of every accepted annotation (id, kind, data, last change) and its file's checksum.

    Same contract as the image service's fingerprint: two reads that return the same value saw
    the same accepted work, and ``create_version`` uses it to notice changes between looking
    and writing."""
    rows = _accepted_annotations(project).values_list(
        "id", "kind", "data", "updated_at", "asset__sha256"
    )
    digest = hashlib.sha256()
    for row in rows:
        digest.update(repr(row).encode())
    return digest.hexdigest()


def _generic_file_problem(found: validation.Problem | None) -> validation.Problem | None:
    """The shared checker's problem, reworded for files that are not pictures."""
    if found is None:
        return None
    if found.code == "file_missing":
        return validation.Problem("file_missing", "The file is missing from storage.", found.asset_id)
    return validation.Problem(
        "hash_mismatch",
        "The file no longer matches the checksum recorded when it was uploaded.",
        found.asset_id,
    )


def analyse(project: Project, config: splits.SplitConfig, *, use_cache: bool = False) -> GenericAnalysis:
    """Inspect a generic project without writing anything.

    Unlike the image path there is no ``file_check`` budget: every file is hashed on every
    analysis. ``use_cache`` (readiness only) lets file checks answer from the stat-keyed
    verification cache; creating a version never uses it."""
    kind = expected_annotation_kind(project)
    accepted_rows = list(_accepted_annotations(project))
    accepted: dict[Any, AssetAnnotation] = {}
    for annotation in accepted_rows:
        accepted.setdefault(annotation.asset_id, annotation)
    suggested = AssetAnnotation.objects.filter(asset__project=project, status=SUGGESTED).count()
    analysis = GenericAnalysis(
        project=project,
        config=config,
        assets=[annotation.asset for annotation in accepted.values()],
        accepted=accepted,
        suggested=suggested,
        assets_total=project.assets.count(),
        fingerprint=content_fingerprint(project),
    )
    problems = analysis.problems
    problems.extend(validation.unsafe_label_problems(project.labels))
    for annotation in accepted_rows:
        if annotation.kind != kind:
            problems.append(
                validation.Problem(
                    "annotation_kind_mismatch",
                    f"This file's labels are of a kind ({annotation.kind!r}) this project's data type does not use.",
                    str(annotation.asset_id),
                    str(annotation.id),
                )
            )
    labelled_ids = [asset.id for asset in analysis.assets]
    for asset in project.assets.exclude(id__in=labelled_ids):
        problems.append(
            validation.Problem("asset_without_accepted", "This file has no accepted label yet.", str(asset.id))
        )
    for asset in analysis.assets:
        found = _generic_file_problem(
            validation.file_problem(asset.storage_key, asset.sha256, str(asset.id), cached=use_cache)
        )
        if found:
            problems.append(found)
    return analysis


def _entries_for(analysis: GenericAnalysis) -> list[dict[str, Any]]:
    project_labels = list(analysis.project.labels)
    split_of = splits.assign_splits((asset.sha256 for asset in analysis.assets), analysis.config)
    entries = [
        {
            "asset_id": str(asset.id),
            "source_filename": asset.filename,
            "sha256": asset.sha256,
            "split": split_of[asset.sha256],
            "label": primary_label(analysis.accepted[asset.id], project_labels),
            "annotation": analysis.accepted[asset.id].data,
        }
        for asset in analysis.assets
    ]
    return sorted(entries, key=lambda item: (item["sha256"], item["asset_id"]))


def _split_notes(analysis: GenericAnalysis) -> list[str]:
    return splits.shortfall_notes(
        len({asset.sha256 for asset in analysis.assets}), analysis.config, _recipe_nouns(analysis.project)
    )


def _recipe_nouns(project) -> dict[str, str] | None:
    """The project's own words for its files (audio: sound clip; tables: table), or None.

    Deferred so importing the lifecycle (which imports this package) never cycles."""
    from ..training.lifecycle import recipe_nouns

    return recipe_nouns(project)


def _summarise(analysis: GenericAnalysis, entries: list[dict[str, Any]]) -> dict[str, Any]:
    split_counts = {name: {"entries": 0} for name in splits.SPLIT_NAMES}
    label_counts = {label: 0 for label in analysis.project.labels}
    for entry in entries:
        split_counts[entry["split"]]["entries"] += 1
        if entry["label"] in label_counts:
            label_counts[entry["label"]] += 1
    warnings: list[str] = []
    for name in splits.SPLIT_NAMES:
        if split_counts[name]["entries"] == 0 and getattr(analysis.config, name) > 0:
            warnings.append(
                f"The {manifest.SPLIT_TITLES[name]} group has 0 files; add more files with accepted labels."
            )
    for label in analysis.project.labels:
        if label_counts[label] == 0:
            warnings.append(f"Label {label!r} has no accepted examples.")
    return {
        "split_counts": split_counts,
        "label_counts": label_counts,
        "warnings": warnings,
        "split_notes": _split_notes(analysis),
    }


def _build_manifest(
    analysis: GenericAnalysis, *, dataset_id, version: int, created_at: str, created_by: int | None
) -> dict[str, Any]:
    entries = _entries_for(analysis)
    return {
        "schema": manifest.MANIFEST_SCHEMA,
        "dataset_id": str(dataset_id),
        "version": version,
        "created_at": created_at,
        "created_by": manifest.opaque_user(created_by),
        "athena_version": athena_version(),
        "project": {"id": str(analysis.project.id), "name": analysis.project.name},
        "label_names": list(analysis.project.labels),
        "split_config": {**analysis.config.as_dict(), "split_algorithm": splits.ALGORITHM},
        "counts": {
            "entry_count": len(entries),
            "annotation_count": len(entries),
            "total_bytes": sum(asset.size_bytes for asset in analysis.assets),
        },
        "excluded": analysis.excluded,
        "entries": entries,
        **_summarise(analysis, entries),
    }


def preview(analysis: GenericAnalysis) -> dict[str, Any]:
    """The split / label summary for readiness, built exactly like the manifest's."""
    if not analysis.accepted:
        return {}
    return _summarise(analysis, _entries_for(analysis))


def readiness_payload(analysis: GenericAnalysis) -> dict[str, Any]:
    """The full readiness answer for a generic project (the view returns it unchanged)."""
    return {
        "ready": analysis.ready,
        "blockers": analysis.blockers,
        "counts": {
            "assets_total": analysis.assets_total,
            "assets": len(analysis.accepted),
            "accepted_annotations": len(analysis.accepted),
            "suggested_annotations": analysis.suggested,
        },
        "excluded": analysis.excluded,
        "split_config": {**analysis.config.as_dict(), "split_algorithm": splits.ALGORITHM},
        "label_names": list(analysis.project.labels),
        "preview": preview(analysis),
        "problems": [problem.as_dict() for problem in analysis.problems[:PROBLEM_LIMIT]],
        "problem_count": len(analysis.problems),
        "problems_truncated": len(analysis.problems) > PROBLEM_LIMIT,
    }


def create_version(analysis: GenericAnalysis, user) -> DatasetVersion:
    """Freeze ``analysis`` as the next version. Caller has checked ``analysis.ready``.

    Same flow as the image service's ``create_version``: the project row is locked, the
    fingerprint must still match (nothing changed between looking and writing), and the whole
    write is one transaction, so a conflict leaves nothing behind."""
    project = analysis.project
    try:
        with transaction.atomic():
            Project.objects.select_for_update().get(pk=project.pk)
            if content_fingerprint(project) != analysis.fingerprint:
                raise VersionConflict("Labels changed while the dataset was being checked. Please try again.")
            number = (project.dataset_versions.aggregate(top=Max("version"))["top"] or 0) + 1
            version = DatasetVersion(
                project=project,
                version=number,
                created_by=user if getattr(user, "is_authenticated", False) else None,
                label_names=list(project.labels),
                image_count=0,
                box_count=0,
                excluded_suggested_count=analysis.suggested,
                excluded_image_count=analysis.assets_total - len(analysis.accepted),
                split_config={**analysis.config.as_dict(), "split_algorithm": splits.ALGORITHM},
            )
            document = _build_manifest(
                analysis,
                dataset_id=version.id,
                version=number,
                created_at=version.created_at.isoformat(),
                created_by=version.created_by_id,
            )
            version.manifest = document
            version.manifest_sha256 = manifest.sha256_hex(document)
            version.save()
            by_asset = {item["asset_id"]: item for item in document["entries"]}
            DatasetEntry.objects.bulk_create(
                DatasetEntry(
                    version=version,
                    asset=asset,
                    asset_uuid=asset.id,
                    filename=asset.filename,
                    storage_key=asset.storage_key,
                    sha256=asset.sha256,
                    split=by_asset[str(asset.id)]["split"],
                    label=by_asset[str(asset.id)]["label"],
                    annotation=by_asset[str(asset.id)]["annotation"],
                )
                for asset in analysis.assets
            )
    except IntegrityError as error:
        raise VersionConflict("Another dataset version was created at the same time. Please try again.") from error
    return version


def entries_from_rows(version: DatasetVersion) -> list[dict[str, Any]]:
    """Rebuild the manifest's ``entries`` list from the snapshot rows."""
    entries = [
        {
            "asset_id": str(row.asset_uuid),
            "source_filename": row.filename,
            "sha256": row.sha256,
            "split": row.split,
            "label": row.label,
            "annotation": row.annotation,
        }
        for row in version.entries.all()
    ]
    return sorted(entries, key=lambda item: (item["sha256"], item["asset_id"]))


def verify_integrity(version: DatasetVersion) -> list[dict[str, Any]]:
    """Return the manifest entries if rows and manifest still match the recorded checksum, else raise."""
    stored = version.manifest
    if manifest.sha256_hex(stored) != version.manifest_sha256:
        raise ValueError(INTEGRITY_MESSAGE)
    if entries_from_rows(version) != stored.get("entries"):
        raise ValueError(INTEGRITY_MESSAGE)
    return stored["entries"]


def file_problems(version: DatasetVersion) -> list[validation.Problem]:
    """Every frozen entry whose file is missing or no longer matches its checksum."""
    problems = []
    for row in version.entries.all():
        found = _generic_file_problem(
            validation.file_problem(row.storage_key, row.sha256, str(row.asset_uuid))
        )
        if found:
            problems.append(found)
    return problems


# -- export ---------------------------------------------------------------------------------

def extension_of(filename: str) -> str:
    """A path-safe extension for an export name: letters and digits only, "bin" when absent."""
    part = filename.rsplit(".", 1)[-1] if "." in filename else ""
    cleaned = "".join(character for character in part if character.isalnum()).lower()
    return cleaned[:16] or "bin"


def entry_layout(version: DatasetVersion) -> list[tuple[Any, str]]:
    """Every entry as ``(row, export path)``, in the export's deterministic order.

    The same naming rule the image writers use, applied to entries:
    ``entries/<split>/<n>_<sha12>.<ext>`` where ``n`` counts within the split. The training
    runner's ``export_entries`` / ``entry_ground_truth`` and the zip export share this so a
    backend can go from a materialized file back to its frozen ground truth."""
    rows = sorted(version.entries.all(), key=lambda row: (row.sha256, str(row.asset_uuid)))
    counters: Counter[str] = Counter()
    pairs = []
    for row in rows:
        counters[row.split] += 1
        name = f"{counters[row.split]:06d}_{row.sha256[:12]}.{extension_of(row.filename)}"
        pairs.append((row, f"entries/{row.split}/{name}"))
    return pairs


def export_files(version: DatasetVersion) -> list[Any]:
    """The ordered files of a generic export: the label list plus every raw asset file.

    A minimal but honest format: ``classes.txt`` (the project's labels, one per line) and one
    entry file per asset under ``entries/<split>/<n>_<sha12>.<ext>``; the streaming wrapper
    adds ``manifest.json`` carrying every label verbatim, so any tool can read what Athena
    froze. No format-specific annotation conversion happens."""
    verify_integrity(version)
    files: list[Any] = [TextFile("classes.txt", ("\n".join(version.label_names) + "\n").encode("utf-8"))]
    files.extend(
        ImageFile(path, row.storage_key, row.sha256) for row, path in entry_layout(version)
    )
    return files


def stream_export(version: DatasetVersion, fmt: str = EXPORT_FORMAT):
    """Stream the generic export as a zip, with the manifest wrapper the image exports use."""
    return stream_zip(fmt, version.manifest, version.manifest_sha256, export_files(version))
