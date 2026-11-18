from __future__ import annotations

import json
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

from django.core.exceptions import RequestDataTooBig, ValidationError
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Case, CharField, Count, Q, Value, When
from django.http import FileResponse, Http404, HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from . import permissions
from .annotations import annotation_plugin, image_project_uses_boxes
from .forms import ProjectCreateForm, normalize_label_names
from .modalities import get_plugin
from .modalities.base import UnsupportedModalityError
from .models import Asset, AssetAnnotation, AutoLabelJob, BoxAnnotation, FeedbackImport, Project
from .permissions import access
from .tasks import recipes as recipe_registry

MAX_REQUEST_BYTES = 1_048_576
MAX_IMAGE_UPLOADS = 20
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_BATCH_BYTES = 100 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000
IMAGE_FORMATS = {
    "JPEG": ("jpg", "image/jpeg"),
    # Pillow reports a JPEG with an MPF (multi-picture) header as MPO. Phones and
    # thermal cameras write these as ordinary .jpg files; the first frame is a normal JPEG.
    "MPO": ("jpg", "image/jpeg"),
    "PNG": ("png", "image/png"),
    "WEBP": ("webp", "image/webp"),
}
BOX_COORDINATE_FIELDS = ("x", "y", "width", "height")
BOX_COORDINATE_QUANTUM = Decimal("0.00000001")


def _safe_filename(upload_name: str) -> str:
    # Kept as a thin delegate: other modules (feedback import) and tests patch/read this name.
    from .modalities.image import safe_filename

    return safe_filename(upload_name)


def _validate_uploaded_image(upload) -> dict[str, Any]:
    """Kept as a thin delegate so existing patches keep working; the logic lives in the plugin.

    The old limits (``views.MAX_IMAGE_BYTES`` / ``views.MAX_IMAGE_PIXELS``) are re-imposed here
    so a test (or operator) patching the views-level constants still changes the behavior, and
    the plugin's own limits stay in one place for other callers.
    """
    from .modalities.image import ImageModalityPlugin

    try:
        metadata = ImageModalityPlugin().validate_upload(upload)
    except ValidationError as error:
        # The plugin raises Django's ValidationError; the historical contract of this helper
        # (and the wording tests) is ValueError with the same message.
        raise ValueError(error.messages[0]) from error
    if metadata["size_bytes"] > MAX_IMAGE_BYTES or upload.size > MAX_IMAGE_BYTES:
        raise ValueError("Each image must be 20 MB or smaller.")
    if metadata["width"] * metadata["height"] > MAX_IMAGE_PIXELS:
        raise ValueError("Image dimensions exceed the supported limit.")
    return metadata


@access()
@require_http_methods(["GET", "HEAD"])
@ensure_csrf_cookie
def dashboard(request: HttpRequest):
    return render(request, "projects/console.html")


@access()
@require_http_methods(["GET"])
def api_me(request: HttpRequest) -> JsonResponse:
    """What the signed-in person may do. The console reads this to switch off buttons they cannot use;
    the server still refuses the call itself. No username or e-mail address is included."""
    response = JsonResponse(permissions.describe(request.user))
    response["Cache-Control"] = "no-store"
    return response


@require_http_methods(["GET"])
@never_cache
def api_version(request: HttpRequest) -> JsonResponse:
    """The console's software version. Public (no sign-in): version disclosure is not a secret,
    and operators asked to report a bug need it without an account."""
    from athena_web.version import current

    response = JsonResponse({"name": "athena-console", "version": current()})
    response["Cache-Control"] = "no-store"
    return response


@access()
@require_http_methods(["GET"])
def api_recipes(request: HttpRequest) -> JsonResponse:
    """What Athena can be asked to learn: the task-recipe registry (single source of truth).

    Any signed-in person may read it (the new-project form needs it before any project exists).
    Unavailable recipes are listed too, with the plain reason, so the console can show them as
    "Coming soon" exactly as it does today.
    """
    response = JsonResponse({"recipes": [recipe.as_dict() for recipe in recipe_registry().values()]})
    response["Cache-Control"] = "no-store"
    return response


def _project_data(project: Project) -> dict[str, Any]:
    return {
        "id": str(project.id),
        "name": project.name,
        "modality": project.modality,
        "custom_modality": project.custom_modality,
        "task": project.task,
        "recipe": project.recipe,
        "labels": project.labels,
        # How many boxes use each label (labels with none are omitted); powers the label editor.
        "label_counts": getattr(project, "label_counts", {}),
        "image_count": getattr(project, "image_count", 0),
        "accepted_boxes": getattr(project, "accepted_boxes", 0),
        "pending_boxes": getattr(project, "pending_boxes", 0),
        # How many pictures have at least one box still waiting for a person (the Overview says "pictures to review").
        "pending_pictures": getattr(project, "pending_pictures", 0),
        # How many frozen snapshots exist; step 5 of the Overview checklist ticks only when this is above zero.
        "dataset_count": getattr(project, "dataset_count", 0),
        # Step 6 ("Teach the computer") ticks only when a person has approved a model; training alone never does.
        "approved_model_count": getattr(project, "approved_model_count", 0),
        "created_at": project.created_at.isoformat(),
    }


def _asset_data(asset: Asset, from_observation=None) -> dict[str, Any]:
    plugin = get_plugin(asset.project.modality)
    extra = plugin.asset_view_metadata(asset) if plugin else {}
    return {
        # Where the picture came from. Only operator-feedback pictures are known by a link table; the rest were
        # uploaded. ``from_observation`` is the check-screen result a feedback picture was taken from.
        "origin": "operator_feedback" if from_observation else "upload",
        "from_observation": str(from_observation) if from_observation else None,
        "id": str(asset.id),
        "filename": asset.filename,
        "content_type": asset.content_type,
        "sha256": asset.sha256,
        "size_bytes": asset.size_bytes,
        "width": asset.width,
        "height": asset.height,
        "metadata": extra,
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
        pending_pictures=Count("assets", filter=suggested, distinct=True),
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
            label: counts.get(project.id, {})[label] for label in project.labels if label in counts.get(project.id, {})
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
        **{field: getattr(current, field) if current else None for field in BOX_COORDINATE_FIELDS},
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


@access(POST="label")
@require_http_methods(["GET", "POST"])
def project_collection(request: HttpRequest) -> JsonResponse:
    if request.method == "GET":
        response = JsonResponse({"projects": [_project_data(p) for p in _with_label_counts(_projects_with_counts())]})
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

    data = form.cleaned_data
    recipe_key = data.pop("recipe") or ""
    if not recipe_key:
        # No recipe picked: image projects keep the vision default, others get none.
        from .forms import default_recipe_for

        recipe_key = default_recipe_for(data["modality"])
    project = Project.objects.create(recipe=recipe_key or None, **data)
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
        raise LabelEditError('The label changes must be a JSON object with a "labels" list.')
    unsupported = payload.keys() - {"labels", "reassign", "based_on"}
    if unsupported:
        raise LabelEditError(f"Only labels can be changed here. Remove {_quoted(sorted(unsupported))} and try again.")
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
            raise LabelEditError(f"The label “{was}” was listed twice. Reload the page and make your change again.")
        used_was.add(was)

    renames = {was: name for was, name in zip(was_for, names) if was is not None and was != name}
    removed = [label for label in existing if label not in used_was]
    return names, renames, removed


@access(PATCH="label", DELETE="train")
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
            based_on = payload.get("based_on")
            if based_on is not None and (
                not isinstance(based_on, list) or not all(isinstance(item, str) for item in based_on)
            ):
                raise LabelEditError("“based_on” must be the list of label names you were looking at.")
            if based_on is not None and based_on != list(project.labels):
                raise LabelEditError(
                    "Someone changed this project's labels while you had the page open, so nothing was saved. "
                    "Reload the page to see their changes, then make your change again.",
                    409,
                )
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
                parts = [f"“{label}” ({count} {'box' if count == 1 else 'boxes'})" for label, count in blocked.items()]
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
                boxes_updated = BoxAnnotation.objects.filter(asset__project=project, label__in=list(moves)).update(
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


@access(POST="label")
@require_http_methods(["GET", "POST"])
def project_assets(request: HttpRequest, project_id) -> JsonResponse:
    project = get_object_or_404(Project, id=project_id)
    plugin = get_plugin(project.modality)
    if plugin is None:
        # A data kind with no registered plugin. The refusal is plain language on purpose.
        return JsonResponse({"error": UnsupportedModalityError(project.modality).message}, status=400)

    if request.method == "GET":
        taken_from = dict(
            FeedbackImport.objects.filter(asset__project=project).values_list("asset_id", "observation_id")
        )
        assets = [_asset_data(asset, taken_from.get(asset.id)) for asset in _assets_with_counts(project)]
        response = JsonResponse({"assets": assets})
        response["Cache-Control"] = "no-store"
        return response

    uploads = request.FILES.getlist("images")
    if not uploads:
        return JsonResponse({"error": "Select one or more images to upload."}, status=400)
    max_uploads = plugin.max_uploads() if plugin.modality != "image" else MAX_IMAGE_UPLOADS
    if len(uploads) > max_uploads:
        return JsonResponse({"error": f"Upload no more than {max_uploads} images at a time."}, status=400)
    max_batch = plugin.max_batch_bytes() if plugin.modality != "image" else MAX_IMAGE_BATCH_BYTES
    if sum(upload.size for upload in uploads) > max_batch:
        return JsonResponse({"error": "The total image batch must be 100 MB or smaller."}, status=400)

    validated = []
    for upload in uploads:
        try:
            if plugin.modality == "image":
                # Byte-compatible with the pre-plugin behavior (tests patch this name).
                metadata = _validate_uploaded_image(upload)
            else:
                metadata = plugin.validate_upload(upload)
            validated.append((upload, metadata))
        except (ValueError, ValidationError) as error:
            # Name the file, so the person knows which one to leave out. Nothing from this batch is saved.
            message = error.messages[0] if isinstance(error, ValidationError) else str(error)
            return JsonResponse(
                {
                    "error": f"“{_safe_filename(upload.name)}”: {message} "
                    "Nothing was added; take that file out and try again."
                },
                status=400,
            )

    saved_keys: list[str] = []
    created: list[Asset] = []
    try:
        with transaction.atomic():
            for upload, metadata in validated:
                asset_id = uuid.uuid4()
                extension = metadata.pop("extension", None) or plugin.storage_suffix or "bin"
                # Modality-specific facts (duration, columns, ...) go to Asset.metadata; the
                # known Asset columns (width/height for images) are passed as fields.
                reserved = {"filename", "sha256", "size_bytes", "width", "height", "content_type"}
                extra = {key: value for key, value in metadata.items() if key not in reserved}
                fields = {key: value for key, value in metadata.items() if key in reserved}
                # Images always have width/height; other modalities have no picture size, and
                # the columns are NOT NULL, so they store 0 rather than lying with a fake size.
                if "width" not in fields:
                    fields["width"] = fields["height"] = 0
                requested_key = f"projects/{project.id}/assets/{asset_id}.{extension}"
                upload.seek(0)
                storage_key = default_storage.save(requested_key, upload)
                saved_keys.append(storage_key)
                created.append(
                    Asset.objects.create(
                        id=asset_id,
                        project=project,
                        storage_key=storage_key,
                        metadata=extra,
                        **fields,
                    )
                )
    except Exception:
        for storage_key in saved_keys:
            default_storage.delete(storage_key)
        raise

    return JsonResponse({"assets": [_asset_data(asset) for asset in created]}, status=201)


@access(DELETE="label")
@require_http_methods(["DELETE"])
def project_asset_detail(request: HttpRequest, project_id, asset_id) -> JsonResponse:
    project = get_object_or_404(Project, id=project_id)
    if get_plugin(project.modality) is None:
        return JsonResponse({"error": UnsupportedModalityError(project.modality).message}, status=400)
    asset = get_object_or_404(Asset, id=asset_id, project=project)
    from .datasets.deletion import delete_asset  # 409s when frozen dataset versions contain the picture

    return delete_asset(request, project, asset)


def _asset_annotation_data(annotation: AssetAnnotation) -> dict[str, Any]:
    return {
        "id": str(annotation.id),
        "kind": annotation.kind,
        "data": annotation.data,
        "origin": annotation.origin,
        "status": annotation.status,
        "confidence": annotation.confidence,
        "model_name": annotation.model_name,
        "reviewed_at": annotation.reviewed_at.isoformat() if annotation.reviewed_at else None,
        "created_at": annotation.created_at.isoformat(),
        "updated_at": annotation.updated_at.isoformat(),
    }


def _read_annotation_json(request: HttpRequest) -> dict[str, Any]:
    """The request body of an annotation write, as a JSON object (plain-language errors)."""
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
    return payload


def _asset_annotation_status_change(payload: dict[str, Any], current: AssetAnnotation) -> dict[str, Any]:
    """The optional ``status`` of an edit, with the same review rule boxes use."""
    if "status" not in payload:
        return {}
    status = payload["status"]
    if status not in (AssetAnnotation.STATUS_ACCEPTED, AssetAnnotation.STATUS_SUGGESTED):
        raise ValueError("Status must be accepted or suggested.")
    if status == AssetAnnotation.STATUS_SUGGESTED and current.origin != AssetAnnotation.ORIGIN_MODEL:
        raise ValueError("Only model proposals can be marked as needing review.")
    return {"status": status}


@access(POST="label")
@require_http_methods(["GET", "POST"])
def project_asset_annotations(request: HttpRequest, project_id, asset_id) -> JsonResponse:
    asset = get_object_or_404(Asset.objects.select_related("project"), id=asset_id, project_id=project_id)
    # The recipe decides the annotation style, not the file type alone: an image project whose
    # recipe asks for transcripts (handwriting OCR) uses these generic endpoints, while every
    # other image project - no recipe, or the vision one - keeps the box endpoints as before.
    if image_project_uses_boxes(asset.project):
        return _box_annotations(request, asset)
    plugin = annotation_plugin(asset.project)
    if plugin is None:
        return JsonResponse({"error": UnsupportedModalityError(asset.project.modality).message}, status=400)
    if request.method == "GET":
        response = JsonResponse(
            {"annotations": [_asset_annotation_data(item) for item in asset.asset_annotations.all()]}
        )
        response["Cache-Control"] = "no-store"
        return response
    try:
        payload = _read_annotation_json(request)
        # The plugin sees only the annotation itself, never the review-flow field.
        data = plugin.validate_annotation({k: v for k, v in payload.items() if k != "status"}, asset.project)
    except (TypeError, ValueError) as error:
        return JsonResponse({"error": str(error)}, status=400)
    except ValidationError as error:
        return JsonResponse({"error": error.messages[0]}, status=400)
    annotation = AssetAnnotation.objects.create(
        asset=asset, kind=plugin.annotation_kind, data=data, created_by=request.user
    )
    return JsonResponse({"annotation": _asset_annotation_data(annotation)}, status=201)


def _box_annotations(request: HttpRequest, asset: Asset) -> JsonResponse:
    """The image path, unchanged: box annotations exactly as before recipes existed."""
    if request.method == "GET":
        response = JsonResponse({"annotations": [_annotation_data(item) for item in asset.annotations.all()]})
        response["Cache-Control"] = "no-store"
        return response
    try:
        data = _validated_annotation_data(request, asset.project)
    except (TypeError, ValueError) as error:
        return JsonResponse({"error": str(error)}, status=400)
    annotation = BoxAnnotation.objects.create(asset=asset, created_by=request.user, **data)
    return JsonResponse({"annotation": _annotation_data(annotation)}, status=201)


@access(PATCH="label", DELETE="label")
@require_http_methods(["PATCH", "DELETE"])
def box_annotation_detail(request: HttpRequest, project_id, asset_id, annotation_id) -> JsonResponse:
    asset = get_object_or_404(
        Asset.objects.select_related("project"),
        id=asset_id,
        project_id=project_id,
        project__modality="image",
    )
    if not image_project_uses_boxes(asset.project):
        # A recipe like handwriting-OCR uses the generic transcript endpoints instead; the box
        # detail URL is not theirs (mirrors the guard on the collection endpoint).
        raise Http404("This project does not use box annotations.")
    annotation = get_object_or_404(BoxAnnotation, id=annotation_id, asset=asset)
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


@access(PATCH="label", DELETE="label")
@require_http_methods(["PATCH", "DELETE"])
def asset_annotation_detail(request: HttpRequest, project_id, annotation_id) -> JsonResponse:
    """The non-image twin of ``box_annotation_detail``: same shape, same review rules.

    The URL carries only the annotation id (the asset is reached through the annotation), and
    the project is checked through the asset, so one project's id never resolves another's row.
    """
    annotation = get_object_or_404(
        AssetAnnotation.objects.select_related("asset", "asset__project"),
        id=annotation_id,
        asset__project_id=project_id,
    )
    asset = annotation.asset
    if image_project_uses_boxes(asset.project):
        # Image projects with box annotations keep their own endpoint; this one is not theirs.
        raise Http404("Image projects use box annotations.")
    plugin = annotation_plugin(asset.project)
    if plugin is None:
        return JsonResponse({"error": UnsupportedModalityError(asset.project.modality).message}, status=400)
    if request.method == "DELETE":
        annotation.delete()
        return JsonResponse({"deleted": True})
    try:
        payload = _read_annotation_json(request)
        status_change = _asset_annotation_status_change(payload, annotation)
        # The plugin sees only the annotation itself, never the review-flow field.
        data = plugin.validate_annotation({**annotation.data, **{k: v for k, v in payload.items() if k != "status"}},
                                          asset.project)
        if not isinstance(data, dict):
            raise TypeError("Annotation details must be a JSON object.")
    except (TypeError, ValueError) as error:
        return JsonResponse({"error": str(error)}, status=400)
    except ValidationError as error:
        return JsonResponse({"error": error.messages[0]}, status=400)
    annotation.data = data
    update_fields = ["data", "updated_at"]
    was_suggested = annotation.status == AssetAnnotation.STATUS_SUGGESTED
    new_status = status_change.get("status")
    if new_status is None and was_suggested:
        new_status = AssetAnnotation.STATUS_ACCEPTED  # A person edited it, so a person reviewed it.
    if new_status is not None and new_status != annotation.status:
        annotation.status = new_status
        if new_status == AssetAnnotation.STATUS_ACCEPTED:
            annotation.reviewed_by = request.user
            annotation.reviewed_at = timezone.now()
        else:
            annotation.reviewed_by = None
            annotation.reviewed_at = None
        update_fields += ["status", "reviewed_by", "reviewed_at"]
    annotation.save(update_fields=update_fields)
    return JsonResponse({"annotation": _asset_annotation_data(annotation)})


@access()
@require_http_methods(["GET"])
def project_asset_content(request: HttpRequest, project_id, asset_id) -> FileResponse:
    project = get_object_or_404(Project, id=project_id)
    if get_plugin(project.modality) is None:
        raise Http404("No plugin supports this project's data type.")
    asset = get_object_or_404(Asset, id=asset_id, project=project)
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
