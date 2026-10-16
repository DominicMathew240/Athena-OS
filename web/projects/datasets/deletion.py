"""Deleting a picture without silently breaking the dataset versions that contain it."""

from __future__ import annotations

from django.core.files.storage import default_storage
from django.db import transaction
from django.http import HttpRequest, JsonResponse
from django.utils import timezone

from ..models import Asset, DatasetVersion, Project
from . import manifest

QUERY_FLAG = "remove_from_snapshots"

HINT = (
    "Dataset versions are frozen copies that point at the original picture files. Deleting this "
    "picture would make those versions impossible to export. To delete it anyway, repeat the request "
    f"with ?{QUERY_FLAG}=1: the picture is removed and each listed version is permanently marked "
    "incomplete (it keeps its manifest and checksum, but can no longer be exported)."
)


def snapshots_containing(asset: Asset) -> list[DatasetVersion]:
    return list(
        DatasetVersion.objects.filter(project_id=asset.project_id, images__asset_uuid=asset.id)
        .distinct()
        .order_by("version")
    )


def delete_asset(request: HttpRequest, project: Project, asset: Asset) -> JsonResponse:
    """Delete ``asset`` (row and file); refuse with 409 if frozen versions contain it, unless asked."""
    remove = request.GET.get(QUERY_FLAG) == "1"
    with transaction.atomic():
        # Same lock create_version takes, so a version cannot be frozen between check and delete.
        Project.objects.select_for_update().get(pk=project.pk)
        versions = snapshots_containing(asset)
        if versions and not remove:
            return JsonResponse(
                {
                    "error": (
                        "This picture is part of frozen dataset version(s) "
                        + ", ".join(f"v{v.version}" for v in versions)
                        + ", so it was not deleted."
                    ),
                    "snapshots": [v.version for v in versions],
                    "hint": HINT,
                },
                status=409,
            )
        actor = manifest.opaque_user(request.user.pk if request.user.is_authenticated else None) or "unknown"
        note = (
            f"{timezone.now().isoformat()}: picture {asset.filename!r} (id {asset.id}) was permanently "
            f"deleted from the project by {actor}."
        )
        for version in versions:
            DatasetVersion.mark_incomplete(version.id, note)
        storage_key = asset.storage_key
        asset.delete()
    # After the commit: a rolled-back delete must never have removed the file.
    default_storage.delete(storage_key)
    return JsonResponse({"deleted": True, "snapshots_marked_incomplete": [v.version for v in versions]})
