from __future__ import annotations

import hashlib
import json
import uuid
import warnings
from decimal import Decimal, InvalidOperation
from typing import Any

from django.core.exceptions import RequestDataTooBig, ValidationError
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import CharField, Case, Count, Q, Value, When
from django.http import FileResponse, Http404, HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods
from PIL import Image, UnidentifiedImageError

from .forms import ProjectCreateForm, normalize_label_names
from .models import Asset, AutoLabelJob, BoxAnnotation, Project

MAX_REQUEST_BYTES = 1_048_576
MAX_IMAGE_UPLOADS = 20
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_BATCH_BYTES = 100 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000
IMAGE_FORMATS = {
    "JPEG": ("jpg", "image/jpeg"),
    "PNG": ("png", "image/png"),
    "WEBP": ("webp", "image/webp"),
}
BOX_COORDINATE_FIELDS = ("x", "y", "width", "height")
BOX_COORDINATE_QUANTUM = Decimal("0.00000001")


@ensure_csrf_cookie
def dashboard(request: HttpRequest):
    return render(request, "projects/console.html")


def _project_data(project: Project) -> dict[str, Any]:
    return {
        "id": str(project.id),
        "name": project.name,
        "modality": project.modality,
        "custom_modality": project.custom_modality,
        "task": project.task,
        "labels": project.labels,
        # How many boxes use each label (labels with none are omitted); powers the label editor.
        "label_counts": getattr(project, "label_counts", {}),
        "image_count": getattr(project, "image_count", 0),
        "accepted_boxes": getattr(project, "accepted_boxes", 0),
        "pending_boxes": getattr(project, "pending_boxes", 0),
        # How many frozen snapshots exist; step 5 of the Get started list ticks only when this is above zero.
        "dataset_count": getattr(project, "dataset_count", 0),
        # Step 6 ("Teach the computer") ticks only when a person has approved a model; training alone never does.
        "approved_model_count": getattr(project, "approved_model_count", 0),
        "created_at": project.created_at.isoformat(),
    }


def _asset_data(asset: Asset) -> dict[str, Any]:
    return {
        "id": str(asset.id),
        "filename": asset.filename,
        "content_type": asset.content_type,
        "sha256": asset.sha256,
        "size_bytes": asset.size_bytes,
        "width": asset.width,
        "height": asset.height,
        "created_at": asset.created_at.isoformat(),
        "content_url": f"/api/projects/{asset.project_id}/assets/{asset.id}/content",
        "accepted_boxes": getattr(asset, "accepted_boxes", 0),
        "pending_boxes": getattr(asset, "pending_boxes", 0),
    }


def _projects_with_counts():
    accepted = Q(assets__annotations__status=BoxAnnotation.STATUS_ACCEPTED)
    suggested = Q(assets__annotations__status=BoxAnnotation.STATUS_SUGGESTED)
    return Project.objects.annotate(
        image_count=Count("assets", distinct=True),
        accepted_boxes=Count("assets__annotations", filter=accepted, distinct=True),
        pending_boxes=Count("assets__annotations", filter=suggested, distinct=True),
        dataset_count=Count("dataset_versions", distinct=True),
        approved_model_count=Count("model_versions", filter=Q(model_versions__stage="approved"), distinct=True),
        # Django ignores Meta.ordering on grouped queries, so newest-first must be explicit.
    ).order_by("-created_at", "-id")


def _label_counts(project_ids=None) -> dict[Any, dict[str, int]]:
    """Box counts per label, grouped by project id, in one query."""
    rows = BoxAnnotation.objects.all()
    if project_ids is not None:
        rows = rows.filter(asset__project_id__in=project_ids)
    grouped: dict[Any, dict[str, int]] = {}
    for row in rows.values("asset__project_id", "label").annotate(total=Count("id")):
        grouped.setdefault(row["asset__project_id"], {})[row["label"]] = row["total"]
    return grouped


def _with_label_counts(projects):
    projects = list(projects)
    counts = _label_counts([project.id for project in projects])
    for project in projects:
        project.label_counts = {
            label: counts.get(project.id, {})[label]
            for label in project.labels
            if label in counts.get(project.id, {})
        }
    return projects


def _assets_with_counts(project: Project):
    return project.assets.annotate(
        accepted_boxes=Count("annotations", filter=Q(annotations__status=BoxAnnotation.STATUS_ACCEPTED)),
        pending_boxes=Count("annotations", filter=Q(annotations__status=BoxAnnotation.STATUS_SUGGESTED)),
    ).order_by("created_at", "id")  # annotate(Count) ignores Meta.ordering, so say it here: a stable list after reload


def _annotation_data(annotation: BoxAnnotation) -> dict[str, Any]:
    return {
        "id": str(annotation.id),
        "label": annotation.label,
        "origin": annotation.origin,
        "status": annotation.status,
        "confidence": annotation.confidence,
        "model_name": annotation.model_name,
        "reviewed_at": annotation.reviewed_at.isoformat() if annotation.reviewed_at else None,
        "x": float(annotation.x),
        "y": float(annotation.y),
        "width": float(annotation.width),
        "height": float(annotation.height),
        "created_at": annotation.created_at.isoformat(),
        "updated_at": annotation.updated_at.isoformat(),
    }


def _validated_annotation_data(
    request: HttpRequest, project: Project, current: BoxAnnotation | None = None
) -> dict[str, Any]:
    if request.content_type != "application/json":
        raise ValueError("Send annotation details as JSON.")
    try:
        body = request.body
    except RequestDataTooBig as error:
        raise ValueError("Annotation request is too large.") from error
    if len(body) > MAX_REQUEST_BYTES:
        raise ValueError("Annotation request is too large.")
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError("Annotation details must be valid JSON.") from error
    if not isinstance(payload, dict):
        raise TypeError("Annotation details must be a JSON object.")
    allowed_fields = {"label", *BOX_COORDINATE_FIELDS}
    if current is not None:
        allowed_fields.add("status")
    if payload.keys() - allowed_fields:
        raise ValueError("Annotation contains an unsupported field.")

    data: dict[str, Any] = {
        "label": current.label if current else None,
        **{
            field: getattr(current, field) if current else None
            for field in BOX_COORDINATE_FIELDS
        },
        **payload,
    }
    if not isinstance(data["label"], str) or data["label"] not in project.labels:
        raise ValueError("Choose a label defined in this project.")

    coordinates: dict[str, Decimal] = {}
    for field in BOX_COORDINATE_FIELDS:
        value = data[field]
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
            raise TypeError("Box coordinates must be numbers between 0 and 1.")
        try:
            coordinate = Decimal(str(value)).quantize(BOX_COORDINATE_QUANTUM)
        except InvalidOperation as error:
            raise ValueError("Box coordinates must be finite numbers.") from error
        if not coordinate.is_finite():
            raise ValueError("Box coordinates must be finite numbers.")
        coordinates[field] = coordinate

    one = Decimal(1)
    x, y, width, height = (coordinates[field] for field in BOX_COORDINATE_FIELDS)
    if x < 0 or y < 0 or width <= 0 or height <= 0:
        raise ValueError("Boxes must have a positive size and start inside the image.")
    if width > one or height > one or x + width > one or y + height > one:
        raise ValueError("Boxes must remain entirely inside the image.")
    result: dict[str, Any] = {"label": data["label"], **coordinates}
    if "status" in payload:
        status = payload["status"]
        if status not in (BoxAnnotation.STATUS_ACCEPTED, BoxAnnotation.STATUS_SUGGESTED):
            raise ValueError("Status must be accepted or suggested.")
        if status == BoxAnnotation.STATUS_SUGGESTED and current.origin != BoxAnnotation.ORIGIN_MODEL:
            raise ValueError("Only model proposals can be marked as needing review.")
        result["status"] = status
    return result


def _safe_filename(upload_name: str) -> str:
    basename = (upload_name or "image").replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join(character for character in basename if character.isprintable()).strip()
    return cleaned[:255] or "image"


def _validate_uploaded_image(upload) -> dict[str, Any]:
    if upload.size <= 0:
        raise ValueError("Image files cannot be empty.")
    if upload.size > MAX_IMAGE_BYTES:
        raise ValueError("Each image must be 20 MB or smaller.")

    digest = hashlib.sha256()
    size_bytes = 0
    for chunk in upload.chunks():
        size_bytes += len(chunk)
        if size_bytes > MAX_IMAGE_BYTES:
            raise ValueError("Each image must be 20 MB or smaller.")
        digest.update(chunk)
    upload.seek(0)

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(upload) as image:
                image_format = image.format
                width, height = image.size
                if image_format not in IMAGE_FORMATS:
                    raise ValueError("Only JPEG, PNG, and WebP images are supported.")
                if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                    raise ValueError("Image dimensions exceed the supported limit.")
                image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ValueError("The uploaded file is not a valid supported image.") from error
    finally:
        upload.seek(0)

    extension, content_type = IMAGE_FORMATS[image_format]
    return {
        "filename": _safe_filename(upload.name),
        "sha256": digest.hexdigest(),
        "size_bytes": size_bytes,
        "width": width,
        "height": height,
        "extension": extension,
        "content_type": content_type,
    }


@require_http_methods(["GET", "POST"])
def project_collection(request: HttpRequest) -> JsonResponse:
    if request.method == "GET":
        response = JsonResponse(
            {"projects": [_project_data(p) for p in _with_label_counts(_projects_with_counts())]}
        )
        response["Cache-Control"] = "no-store"
        return response

    if request.content_type != "application/json":
        return JsonResponse({"error": "Send project details as JSON."}, status=415)
    try:
        body = request.body
    except RequestDataTooBig:
        return JsonResponse({"error": "Project request is too large."}, status=413)
    if len(body) > MAX_REQUEST_BYTES:
        return JsonResponse({"error": "Project request is too large."}, status=413)
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Request body must be valid JSON."}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"error": "Project details must be a JSON object."}, status=400)

    form = ProjectCreateForm(payload)
    if not form.is_valid():
        field, errors = next(iter(form.errors.items()))
        return JsonResponse({"error": f"{field}: {errors[0]}"}, status=400)

    project = Project.objects.create(**form.cleaned_data)
    return JsonResponse({"project": _project_data(project)}, status=201)


class LabelEditError(Exception):
    """A label edit that cannot be applied; ``message`` is shown to the operator as-is."""

    def __init__(self, message: str, status: int = 400, **extra: Any) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.extra = extra


def _quoted(names) -> str:
    return ", ".join(f"“{name}”" for name in names)


def _read_label_edit(request: HttpRequest) -> dict[str, Any]:
    if request.content_type != "application/json":
        raise LabelEditError("Send the label changes as JSON.", 415)
    try:
        body = request.body
    except RequestDataTooBig:
        raise LabelEditError("That label list is too large. Remove some labels and try again.", 413)
    if len(body) > MAX_REQUEST_BYTES:
        raise LabelEditError("That label list is too large. Remove some labels and try again.", 413)
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise LabelEditError("The label changes were not valid JSON. Reload the page and try again.")
    if not isinstance(payload, dict):
        raise LabelEditError("The label changes must be a JSON object with a \"labels\" list.")
    unsupported = payload.keys() - {"labels", "reassign"}
    if unsupported:
        raise LabelEditError(
            f"Only labels can be changed here. Remove {_quoted(sorted(unsupported))} and try again."
        )
    if "labels" not in payload:
        raise LabelEditError("Send the full list of labels to save.")
    return payload


def _plan_label_edit(project: Project, payload: dict[str, Any]) -> tuple[list[str], dict[str, str], list[str]]:
    """Work out (new label list, old -> new box-label moves, removed labels) or raise."""
    existing = list(project.labels)
    items = payload["labels"]
    if not isinstance(items, list):
        raise LabelEditError("Labels must be sent as a list of names.")

    requested: list[str] = []
    was_for: list[str | None] = []
    for item in items:
        if isinstance(item, dict):
            if item.keys() - {"name", "was"} or not isinstance(item.get("name"), str):
                raise LabelEditError("Each label needs a text name. Check the label list and try again.")
            was = item.get("was")
            if was is not None and not isinstance(was, str):
                raise LabelEditError("Each label needs a text name. Check the label list and try again.")
            requested.append(item["name"])
            was_for.append(was)
        else:
            requested.append(item)  # validated (type, emptiness, length) below
            was_for.append(item if isinstance(item, str) and item in existing else None)

    try:
        names = normalize_label_names(requested)
    except ValidationError as error:
        raise LabelEditError(" ".join(error.messages))

    used_was: set[str] = set()
    for was in was_for:
        if was is None:
            continue
        if was not in existing:
            raise LabelEditError(
                f"The label “{was}” no longer exists in this project. "
                "Reload the page to see the current labels, then make your change again."
            )
        if was in used_was:
            raise LabelEditError(
                f"The label “{was}” was listed twice. Reload the page and make your change again."
            )
        used_was.add(was)

    renames = {was: name for was, name in zip(was_for, names) if was is not None and was != name}
    removed = [label for label in existing if label not in used_was]
    return names, renames, removed


@require_http_methods(["PATCH", "DELETE"])
def project_detail(request: HttpRequest, project_id) -> JsonResponse:
    if request.method == "PATCH":
        return _update_project_labels(request, project_id)
    project = get_object_or_404(Project, id=project_id)
    for asset in project.assets.all():
        default_storage.delete(asset.storage_key)
    project.delete()
    return JsonResponse({"deleted": True})


def _update_project_labels(request: HttpRequest, project_id) -> JsonResponse:
    """Replace a project's label list: add, rename, reorder, or remove labels.

    Renames are applied to existing boxes in the same transaction. A label that still has
    boxes can only be removed together with a ``reassign`` entry saying where its boxes go.
    """
    project = get_object_or_404(Project, id=project_id)
    try:
        payload = _read_label_edit(request)
        with transaction.atomic():
            project = Project.objects.select_for_update().get(id=project.id)
            names, renames, removed = _plan_label_edit(project, payload)

            reassign = payload.get("reassign", {})
            if not isinstance(reassign, dict) or not all(
                isinstance(source, str) and isinstance(target, str) for source, target in reassign.items()
            ):
                raise LabelEditError("“reassign” must say, for each removed label, which label takes its boxes.")
            for source, target in reassign.items():
                if source not in removed:
                    raise LabelEditError(
                        f"Boxes can only be moved away from a label you are removing, and “{source}” is not being removed."
                    )
                if target not in names:
                    raise LabelEditError(
                        f"Choose a label from the new list to move the “{source}” boxes to; “{target}” is not in it."
                    )

            if (renames or removed) and AutoLabelJob.objects.filter(
                project=project, status__in=AutoLabelJob.ACTIVE_STATUSES
            ).exists():
                raise LabelEditError(
                    "Auto-suggest is still running for this project. Renaming or removing labels now "
                    "could leave new boxes with an old name. Wait for it to finish (or cancel it), then try again. "
                    "Adding and reordering labels is fine right now.",
                    409,
                )

            usage = {
                row["label"]: row["total"]
                for row in BoxAnnotation.objects.filter(asset__project=project, label__in=removed)
                .values("label")
                .annotate(total=Count("id"))
            }
            blocked = {label: usage[label] for label in removed if label in usage and label not in reassign}
            if blocked:
                parts = [
                    f"“{label}” ({count} {'box' if count == 1 else 'boxes'})"
                    for label, count in blocked.items()
                ]
                if len(blocked) == 1:
                    label, count = next(iter(blocked.items()))
                    message = (
                        f"“{label}” is still used by {count} {'box' if count == 1 else 'boxes'}. "
                        "Choose another label to move those boxes to, or delete the boxes first."
                    )
                else:
                    message = (
                        f"These labels are still used by boxes: {', '.join(parts)}. "
                        "Choose another label to move each set of boxes to, or delete the boxes first."
                    )
                raise LabelEditError(message, 409, in_use=blocked)

            moves = {**renames, **reassign}
            boxes_updated = 0
            if moves:
                boxes_updated = BoxAnnotation.objects.filter(
                    asset__project=project, label__in=list(moves)
                ).update(
                    label=Case(
                        *[When(label=old, then=Value(new)) for old, new in moves.items()],
                        output_field=CharField(),
                    ),
                    updated_at=timezone.now(),
                )
            project.labels = names
            project.save(update_fields=["labels"])
    except LabelEditError as error:
        return JsonResponse({"error": error.message, **error.extra}, status=error.status)

    saved = _with_label_counts([_projects_with_counts().get(id=project.id)])[0]
    return JsonResponse({"project": _project_data(saved), "boxes_updated": boxes_updated})


@require_http_methods(["GET", "POST"])
def project_assets(request: HttpRequest, project_id) -> JsonResponse:
    project = get_object_or_404(Project, id=project_id)
    if project.modality != "image":
        return JsonResponse({"error": "Image files can only be added to image projects."}, status=400)

    if request.method == "GET":
        assets = [_asset_data(asset) for asset in _assets_with_counts(project)]
        response = JsonResponse({"assets": assets})
        response["Cache-Control"] = "no-store"
        return response

    uploads = request.FILES.getlist("images")
    if not uploads:
        return JsonResponse({"error": "Select one or more images to upload."}, status=400)
    if len(uploads) > MAX_IMAGE_UPLOADS:
        return JsonResponse({"error": f"Upload no more than {MAX_IMAGE_UPLOADS} images at a time."}, status=400)
    if sum(upload.size for upload in uploads) > MAX_IMAGE_BATCH_BYTES:
        return JsonResponse({"error": "The total image batch must be 100 MB or smaller."}, status=400)

    validated = []
    for upload in uploads:
        try:
            validated.append((upload, _validate_uploaded_image(upload)))
        except ValueError as error:
            # Name the file, so the person knows which one to leave out. Nothing from this batch is saved.
            return JsonResponse({"error": f"“{_safe_filename(upload.name)}”: {error} Nothing was added; take that file out and try again."}, status=400)

    saved_keys: list[str] = []
    created: list[Asset] = []
    try:
        with transaction.atomic():
            for upload, metadata in validated:
                asset_id = uuid.uuid4()
                requested_key = f"projects/{project.id}/assets/{asset_id}.{metadata['extension']}"
                upload.seek(0)
                storage_key = default_storage.save(requested_key, upload)
                saved_keys.append(storage_key)
                created.append(
                    Asset.objects.create(
                        id=asset_id,
                        project=project,
                        storage_key=storage_key,
                        **{key: value for key, value in metadata.items() if key != "extension"},
                    )
                )
    except Exception:
        for storage_key in saved_keys:
            default_storage.delete(storage_key)
        raise

    return JsonResponse({"assets": [_asset_data(asset) for asset in created]}, status=201)


@require_http_methods(["DELETE"])
def project_asset_detail(request: HttpRequest, project_id, asset_id) -> JsonResponse:
    project = get_object_or_404(Project, id=project_id, modality="image")
    asset = get_object_or_404(Asset, id=asset_id, project=project)
    from .datasets.deletion import delete_asset  # 409s when frozen dataset versions contain the picture

    return delete_asset(request, project, asset)


@require_http_methods(["GET", "POST"])
def project_asset_annotations(
    request: HttpRequest, project_id, asset_id
) -> JsonResponse:
    asset = get_object_or_404(
        Asset.objects.select_related("project"),
        id=asset_id,
        project_id=project_id,
        project__modality="image",
    )
    if request.method == "GET":
        response = JsonResponse(
            {"annotations": [_annotation_data(item) for item in asset.annotations.all()]}
        )
        response["Cache-Control"] = "no-store"
        return response
    try:
        data = _validated_annotation_data(request, asset.project)
    except (TypeError, ValueError) as error:
        return JsonResponse({"error": str(error)}, status=400)
    annotation = BoxAnnotation.objects.create(asset=asset, created_by=request.user, **data)
    return JsonResponse({"annotation": _annotation_data(annotation)}, status=201)


@require_http_methods(["PATCH", "DELETE"])
def box_annotation_detail(
    request: HttpRequest, project_id, asset_id, annotation_id
) -> JsonResponse:
    asset = get_object_or_404(
        Asset.objects.select_related("project"),
        id=asset_id,
        project_id=project_id,
        project__modality="image",
    )
    annotation = get_object_or_404(
        BoxAnnotation, id=annotation_id, asset=asset
    )
    if request.method == "DELETE":
        annotation.delete()
        return JsonResponse({"deleted": True})
    try:
        data = _validated_annotation_data(request, asset.project, current=annotation)
    except (TypeError, ValueError) as error:
        return JsonResponse({"error": str(error)}, status=400)
    was_suggested = annotation.status == BoxAnnotation.STATUS_SUGGESTED
    new_status = data.pop("status", None)
    if new_status is None and was_suggested:
        new_status = BoxAnnotation.STATUS_ACCEPTED  # A person edited it, so a person reviewed it.
    for field, value in data.items():
        setattr(annotation, field, value)
    update_fields = ["label", *BOX_COORDINATE_FIELDS, "updated_at"]
    if new_status is not None and new_status != annotation.status:
        annotation.status = new_status
        if new_status == BoxAnnotation.STATUS_ACCEPTED:
            annotation.reviewed_by = request.user
            annotation.reviewed_at = timezone.now()
        else:
            annotation.reviewed_by = None
            annotation.reviewed_at = None
        update_fields += ["status", "reviewed_by", "reviewed_at"]
    annotation.save(update_fields=update_fields)
    return JsonResponse({"annotation": _annotation_data(annotation)})


@require_http_methods(["GET"])
def project_asset_content(request: HttpRequest, project_id, asset_id) -> FileResponse:
    asset = get_object_or_404(
        Asset, id=asset_id, project_id=project_id, project__modality="image"
    )
    try:
        response = FileResponse(
            default_storage.open(asset.storage_key, "rb"),
            content_type=asset.content_type,
        )
    except FileNotFoundError as error:
        raise Http404("Image file not found.") from error
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "no-store"
    return response
