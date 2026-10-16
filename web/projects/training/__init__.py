"""Opt-in model training behind a replaceable backend interface.

Nothing here imports torch. Backends are looked up by name from a registry that is filled from
(1) built-in backends, (2) ``settings.TRAINING["BACKENDS"]`` (``"module:Class"`` strings),
(3) the ``athena.training_backends`` entry-point group, and (4) ``register_backend()`` calls.
A third party can ship a backend as a separate package that declares the entry point; no core
file needs editing.
"""

from __future__ import annotations

import importlib
import logging
import os
import re
from importlib import metadata
from pathlib import Path
from typing import Any

from django.conf import settings

from .base import Availability, BackendUnavailable, BoxPrediction, TrainingBackend

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "athena.training_backends"
BUILTIN_BACKENDS = ("projects.training.torchvision_frcnn:TorchvisionFasterRCNN",)
DEFAULT_BACKEND = "torchvision-fasterrcnn"
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,78}$")

_registry: dict[str, type[TrainingBackend]] = {}
_loaded = False


def training_settings() -> dict[str, Any]:
    """Effective training settings: defaults < ``settings.TRAINING`` < ``ATHENA_TRAINING_*`` env."""
    config: dict[str, Any] = {
        "DEFAULT_BACKEND": os.environ.get("ATHENA_TRAINING_BACKEND", DEFAULT_BACKEND),
        "BACKENDS": [],
        "RUN_TIMEOUT_SECONDS": 6 * 3600,
        "STALE_HEARTBEAT_SECONDS": 600,
        "INFERENCE_TIMEOUT_SECONDS": 120,
        "INFERENCE_QUEUE_LIMIT": 50,
        "MAX_LOG_CHARS": 20_000,
        "SCRATCH_DIR": "",
    }
    if os.environ.get("ATHENA_TRAINING_RUN_TIMEOUT"):
        config["RUN_TIMEOUT_SECONDS"] = int(os.environ["ATHENA_TRAINING_RUN_TIMEOUT"])
    config.update(getattr(settings, "TRAINING", {}) or {})
    return config


def data_home() -> Path:
    return Path(settings.DATA_HOME)


def scratch_root() -> Path:
    configured = training_settings()["SCRATCH_DIR"]
    return Path(configured) if configured else data_home() / "training-scratch"


def models_root() -> Path:
    return data_home() / "models"


def register_backend(cls: type[TrainingBackend], *, replace: bool = False) -> type[TrainingBackend]:
    name = getattr(cls, "name", "")
    if not isinstance(name, str) or not NAME_PATTERN.match(name):
        raise ValueError("A training backend needs a `name` of lower-case letters, digits, '-' or '_'.")
    if name in _registry and _registry[name] is not cls and not replace:
        raise ValueError(f"A training backend named {name!r} is already registered.")
    _registry[name] = cls
    return cls


def unregister_backend(name: str) -> None:
    _registry.pop(name, None)


def _load_path(path: str) -> type[TrainingBackend]:
    module_name, _, class_name = path.partition(":")
    return getattr(importlib.import_module(module_name), class_name)


def _load_all() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    candidates: list[tuple[str, Any]] = [(path, path) for path in BUILTIN_BACKENDS]
    candidates += [(path, path) for path in training_settings()["BACKENDS"]]
    try:
        candidates += [(entry.name, entry) for entry in metadata.entry_points(group=ENTRY_POINT_GROUP)]
    except Exception:  # noqa: BLE001 - a broken packaging environment must not break the console
        logger.exception("Could not read %s entry points", ENTRY_POINT_GROUP)
    for label, source in candidates:
        try:
            cls = _load_path(source) if isinstance(source, str) else source.load()
            if cls.name not in _registry:
                register_backend(cls)
        except Exception:  # noqa: BLE001 - one bad plugin must not hide the others
            logger.exception("Skipping training backend %s", label)


def reload_backends() -> None:
    """Forget what was discovered (tests, and operators installing a plugin without restarting)."""
    global _loaded
    _loaded = False


def backend_classes() -> dict[str, type[TrainingBackend]]:
    _load_all()
    return dict(sorted(_registry.items()))


def get_backend(name: str) -> TrainingBackend:
    _load_all()
    try:
        return _registry[name]()
    except KeyError:
        raise ValueError(f"No training backend named {name!r} is installed.") from None


def describe_backend(backend: TrainingBackend) -> dict[str, Any]:
    availability = backend.check_available()
    return {
        "name": backend.name,
        "description": backend.description,
        "license_note": backend.license_note,
        "dataset_format": backend.dataset_format,
        "constraints": list(backend.constraints),
        "default_config": backend.default_config(),
        "available_in_web_process": availability.ok,
        "availability": availability.as_dict(),
    }


__all__ = [
    "Availability",
    "BackendUnavailable",
    "BoxPrediction",
    "TrainingBackend",
    "backend_classes",
    "describe_backend",
    "get_backend",
    "register_backend",
    "unregister_backend",
    "reload_backends",
    "training_settings",
]
