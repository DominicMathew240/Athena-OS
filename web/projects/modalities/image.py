"""The image modality plugin: JPEG/PNG/WebP pictures with box annotations.

This is the validation that used to live in ``projects.views`` (``_validate_uploaded_image``
and ``_safe_filename``), moved here unchanged so every other modality can mirror it. The
messages and limits are pinned by the console's tests; do not reword them casually.
"""

from __future__ import annotations

import warnings
from typing import Any

from django.core.exceptions import ValidationError
from PIL import Image, UnidentifiedImageError

from .base import ModalityPlugin, check_not_empty, digest_upload

MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000
IMAGE_FORMATS = {
    "JPEG": ("jpg", "image/jpeg"),
    # Pillow reports a JPEG with an MPF (multi-picture) header as MPO. Phones and
    # thermal cameras write these as ordinary .jpg files; the first frame is a normal JPEG.
    "MPO": ("jpg", "image/jpeg"),
    "PNG": ("png", "image/png"),
    "WEBP": ("webp", "image/webp"),
}


def safe_filename(upload_name: str) -> str:
    basename = (upload_name or "image").replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join(character for character in basename if character.isprintable()).strip()
    return cleaned[:255] or "image"


class ImageModalityPlugin(ModalityPlugin):
    modality = "image"
    asset_noun = "picture"
    annotation_kind = "boxes"

    def validate_upload(self, upload) -> dict[str, Any]:
        check_not_empty(upload, "Image files")
        if upload.size > MAX_IMAGE_BYTES:
            raise ValidationError("Each image must be 20 MB or smaller.")

        sha256, size_bytes = digest_upload(upload, max_bytes=MAX_IMAGE_BYTES,
                                           limit_message="Each image must be 20 MB or smaller.")

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(upload) as image:
                    image_format = image.format
                    width, height = image.size
                    if image_format not in IMAGE_FORMATS:
                        raise ValidationError("Only JPEG, PNG, and WebP images are supported.")
                    if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                        raise ValidationError("Image dimensions exceed the supported limit.")
                    image.load()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
            raise ValidationError("The uploaded file is not a valid supported image.") from error
        finally:
            upload.seek(0)

        extension, content_type = IMAGE_FORMATS[image_format]
        return {
            "filename": safe_filename(upload.name),
            "sha256": sha256,
            "size_bytes": size_bytes,
            "width": width,
            "height": height,
            "extension": extension,
            "content_type": content_type,
        }

    def asset_view_metadata(self, asset) -> dict[str, Any]:
        # Width and height are real Asset columns and already in the shared payload.
        return {}
