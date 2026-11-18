"""Modality plugin registry: which data kinds are supported, and their plugin instances.

Mirrors the training-backend registry: built-ins, ``settings.MODALITY_PLUGINS``
(``"module:Class"`` strings), and ``register_plugin()`` calls. Core code asks
``get_plugin(modality)`` and either uses it or refuses plainly when there is none.
"""

from __future__ import annotations

import importlib
import logging

from django.conf import settings

from .audio import AudioModalityPlugin
from .base import ModalityPlugin, UnsupportedModalityError
from .image import ImageModalityPlugin

logger = logging.getLogger(__name__)

BUILTIN_PLUGINS = (
    "projects.modalities.image:ImageModalityPlugin",
    "projects.modalities.audio:AudioModalityPlugin",
)

_registry: dict[str, type[ModalityPlugin]] = {}
_instances: dict[str, ModalityPlugin] = {}
_loaded = False


def register_plugin(cls: type[ModalityPlugin], *, replace: bool = False) -> type[ModalityPlugin]:
    modality = getattr(cls, "modality", "")
    if not isinstance(modality, str) or not modality:
        raise ValueError("A modality plugin needs a non-empty `modality`.")
    if modality in _registry and _registry[modality] is not cls and not replace:
        raise ValueError(f"A modality plugin for {modality!r} is already registered.")
    _registry[modality] = cls
    _instances.pop(modality, None)
    return cls


def unregister_plugin(modality: str) -> None:
    _registry.pop(modality, None)
    _instances.pop(modality, None)


def _load_path(path: str) -> type[ModalityPlugin]:
    module_name, _, class_name = path.partition(":")
    return getattr(importlib.import_module(module_name), class_name)


def _load_all() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    for path in BUILTIN_PLUGINS + tuple(getattr(settings, "MODALITY_PLUGINS", []) or []):
        try:
            cls = _load_path(path)
            register_plugin(cls, replace=True)
        except Exception:  # noqa: BLE001 - one bad plugin must not hide the others
            logger.exception("Skipping modality plugin %s", path)


def reload_plugins() -> None:
    """Forget what was discovered (tests, and operators installing a plugin without restarting)."""
    global _loaded
    _loaded = False


def available_modalities() -> list[str]:
    _load_all()
    return sorted(_registry)


def get_plugin(modality: str) -> ModalityPlugin | None:
    _load_all()
    if modality not in _instances:
        cls = _registry.get(modality)
        if cls is None:
            return None
        _instances[modality] = cls()
    return _instances[modality]


def plugin_or_refusal(modality: str) -> ModalityPlugin:
    """The plugin for ``modality``, or raise :class:`UnsupportedModalityError` (plain message)."""
    plugin = get_plugin(modality)
    if plugin is None:
        raise UnsupportedModalityError(modality)
    return plugin


__all__ = [
    "ModalityPlugin",
    "UnsupportedModalityError",
    "available_modalities",
    "get_plugin",
    "plugin_or_refusal",
    "register_plugin",
    "reload_plugins",
    "unregister_plugin",
]
