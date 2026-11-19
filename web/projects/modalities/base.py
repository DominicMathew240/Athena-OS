"""The modality-plugin contract: how one data kind is validated, stored, annotated and shown.

A plugin owns everything modality-specific about an asset: which files are accepted (and their
metadata), which annotation bodies are valid, and what the asset list says about each asset.
Core code (views, models, the training runner) talks to plugins through this interface only,
so a new data kind lands as a plugin plus a recipe, not as ``if modality == ...`` branches.

Importing this module must stay light (no torch, no audio frameworks): the web process calls
``validate_upload`` and ``validate_annotation`` on every request. Anything heavy belongs inside
a method, imported lazily, and should self-skip in tests when its framework is missing (the
torchvision backend pattern).
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from typing import Any

from django.core.exceptions import ValidationError


def check_not_empty(upload, what: str) -> None:
    """Refuse an empty upload in the plugin's own words (``what`` plural, e.g. "Image files")."""
    if upload.size <= 0:
        raise ValidationError(f"{what} cannot be empty.")


def digest_upload(upload, *, max_bytes: int, limit_message: str) -> tuple[str, int]:
    """Stream one upload through SHA-256: returns ``(hex digest, size in bytes)``.

    Every plugin that stores files hashes them the same way - chunk by chunk, so a huge upload
    never sits whole in memory, with the size limit enforced again mid-stream (the client's
    ``Content-Length`` is not trusted). ``limit_message`` is the plugin's own plain sentence for
    a file past its limit. The upload is rewound to its start, ready for the format check that
    follows."""
    digest = hashlib.sha256()
    size_bytes = 0
    for chunk in upload.chunks():
        size_bytes += len(chunk)
        if size_bytes > max_bytes:
            raise ValidationError(limit_message)
        digest.update(chunk)
    upload.seek(0)
    return digest.hexdigest(), size_bytes


class ModalityPlugin(ABC):
    """Subclass this and register it (see :mod:`projects.modalities.registry`)."""

    #: Which data kind this plugin handles; one of ``Project.MODALITY_CHOICES``'s values.
    modality: str = ""
    #: What this modality's assets are called in operator-facing sentences, e.g. "picture".
    asset_noun: str = "file"

    # -- uploads --------------------------------------------------------------------------------
    @abstractmethod
    def validate_upload(self, upload) -> dict[str, Any]:
        """Check one uploaded file and return its asset metadata.

        The dict must carry ``filename``, ``sha256``, ``size_bytes``, ``extension`` and
        ``content_type`` (these become Asset columns plus the storage-key extension); anything
        else is stored in ``Asset.metadata`` (image adds ``width``/``height`` as columns, audio
        adds ``duration_seconds``/``sample_rate``, tables add a column summary).

        Raise ``ValidationError`` with a plain-language message on a bad file; the view names
        the file and refuses the whole batch.
        """

    #: Extension used for storage keys, without the dot (e.g. "png"). Defaults to whatever
    #: ``validate_upload`` reported, which is correct for every real plugin.
    storage_suffix: str = ""

    # -- annotations ----------------------------------------------------------------------------
    @property
    def annotation_kind(self) -> str:
        """The slug of the annotation style this modality accepts (e.g. "boxes")."""
        return ""

    def validate_annotation(self, payload: dict[str, Any], project) -> dict[str, Any]:
        """Check one JSON annotation body and return the normalized ``AssetAnnotation.data``.

        The default refuses: a modality whose plugin has not implemented annotations yet. A
        plugin that does implement it returns a JSON-serializable dict; the view stores it
        verbatim. Raise ``ValidationError`` with a plain-language message on a bad body.
        """
        raise ValidationError("This project's data type does not have labeling tools yet.")

    # -- listings -------------------------------------------------------------------------------
    def asset_view_metadata(self, asset) -> dict[str, Any]:
        """What the asset list API returns for one asset, beyond the shared fields.

        The default shows the stored ``Asset.metadata`` (duration, sample rate, table columns,
        ...); image keeps its width/height columns and returns nothing extra here.
        """
        return dict(asset.metadata or {})

    # -- helper ---------------------------------------------------------------------------------
    def max_uploads(self) -> int:
        """How many files one upload request may carry. The view enforces it."""
        return 20

    def max_batch_bytes(self) -> int:
        """How much one upload request may carry in total. The view enforces it."""
        return 100 * 1024 * 1024


class UnsupportedModalityError(LookupError):
    """No plugin is registered for a project's data kind. ``message`` is plain language."""

    def __init__(self, modality: str) -> None:
        super().__init__(modality)
        self.modality = modality
        self.message = "This project's data type isn't supported yet. Its files and labels cannot be changed here."
