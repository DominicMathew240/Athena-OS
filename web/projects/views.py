from __future__ import annotations

import hashlib
import json
import uuid
import warnings
from decimal import Decimal, InvalidOperation
from typing import Any

from django.core.exceptions import RequestDataTooBig
from django.core.files.storage import default_storage
from django.db import transaction
from django.http import FileResponse, Http404, HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods
from PIL import Image, UnidentifiedImageError

from .forms import ProjectCreateForm
from .models import Asset, BoxAnnotation, Project

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
    return render(request, "prototype/index.html")


def _project_data(project: Project) -> dict[str, Any]:
    return {
        "id": str(project.id),
        "name": project.name,
        "modality": project.modality,
        "custom_modality": project.custom_modality,
        "task": project.task,
        "labels": project.labels,
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
    }


def _annotation_data(annotation: BoxAnnotation) -> dict[str, Any]:
    return {
        "id": str(annotation.id),
        "label": annotation.label,
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
    return {"label": data["label"], **coordinates}


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
            {"projects": [_project_data(project) for project in Project.objects.all()]}
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


@require_http_methods(["DELETE"])
def project_detail(request: HttpRequest, project_id) -> JsonResponse:
    project = get_object_or_404(Project, id=project_id)
    for asset in project.assets.all():
        default_storage.delete(asset.storage_key)
    project.delete()
    return JsonResponse({"deleted": True})


@require_http_methods(["GET", "POST"])
def project_assets(request: HttpRequest, project_id) -> JsonResponse:
    project = get_object_or_404(Project, id=project_id)
    if project.modality != "image":
        return JsonResponse({"error": "Image files can only be added to image projects."}, status=400)

    if request.method == "GET":
        assets = [_asset_data(asset) for asset in project.assets.all()]
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

    try:
        validated = [(upload, _validate_uploaded_image(upload)) for upload in uploads]
    except ValueError as error:
        return JsonResponse({"error": str(error)}, status=400)

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
    default_storage.delete(asset.storage_key)
    asset.delete()
    return JsonResponse({"deleted": True})


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
    annotation = BoxAnnotation.objects.create(asset=asset, **data)
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
    for field, value in data.items():
        setattr(annotation, field, value)
    annotation.save(update_fields=["label", *BOX_COORDINATE_FIELDS, "updated_at"])
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
