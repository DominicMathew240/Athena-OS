"""Model-assisted labelling: backends are loaded only inside the worker process."""

from __future__ import annotations

import importlib
from typing import Any

from django.conf import settings

from .base import AutoLabeler, Proposal

__all__ = ["AutoLabeler", "Proposal", "get_autolabeler", "is_enabled", "describe"]


def is_enabled() -> bool:
    return settings.AUTOLABEL["BACKEND"] not in {"", "disabled", "off"}


def describe() -> dict[str, Any]:
    config = settings.AUTOLABEL
    if not is_enabled():
        return {"enabled": False, "backend": "disabled"}
    return {
        "enabled": True,
        "backend": config["BACKEND"],
        "segmenter": config["SAM_MODEL"],
        "classifier": config["CLASSIFIER_MODEL"],
    }


def get_autolabeler() -> AutoLabeler:
    """Build the configured backend. ``BACKEND`` may also be a ``module:Class`` path."""
    backend = settings.AUTOLABEL["BACKEND"]
    if backend == "sam":
        from .sam_clip import SamClipAutoLabeler

        return SamClipAutoLabeler(settings.AUTOLABEL)
    if ":" in backend:
        module_name, class_name = backend.split(":", 1)
        return getattr(importlib.import_module(module_name), class_name)(settings.AUTOLABEL)
    raise ValueError(f"Unknown auto-label backend: {backend!r}")
