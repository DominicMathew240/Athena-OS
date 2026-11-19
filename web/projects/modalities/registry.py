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
from .table import TableModalityPlugin

logger = logging.getLogger(__name__)

BUILTIN_PLUGINS = (
    "projects.modalities.image:ImageModalityPlugin",
    "projects.modalities.audio:AudioModalityPlugin",
    "projects.modalities.table:TableModalityPlugin",
)

#: Recipe-dispatched plugins that no modality loads on its own: importing the module registers
#: them (by annotation kind, and the recipe it replaces). Kept here rather than in
#: ``BUILTIN_PLUGINS`` because their classes share a modality with a builtin plugin and are
#: registered for their *kind*, not their modality — a plain builtin entry would try to
#: register the same modality twice (and register_plugin would need replace=True to win).
BUILTIN_ANNOTATION_PLUGINS = (
    "projects.modalities.ocr",  # importing it registers OcrImagePlugin and the OCR recipe
)

#: Plugins looked up by *annotation kind* instead of modality. A recipe may ask an image
#: project for a different annotation style than the image plugin's boxes (handwriting OCR
#: wants transcripts); those plugins register here so dispatch stays a lookup, not an
#: ``if recipe == ...`` branch. ``register_plugin`` fills this table automatically. The
#: class is stored, not an instance, and instances are kept here too (a by-kind plugin is
#: not necessarily the modality's registered one, so ``get_plugin(modality)`` must not be
#: used to build them).
_annotation_registry: dict[str, type[ModalityPlugin]] = {}
_annotation_instances: dict[str, ModalityPlugin] = {}

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
    # Same class, indexed by its annotation kind too: a recipe that names this kind finds it
    # without knowing (or competing with) the modality its files are validated by. Only a
    # class whose kind differs from the modality's registered plugin is kept, so the builtin
    # image plugin (boxes) does not shadow the OCR plugin (transcripts) or the other way round.
    _register_annotation_plugin(cls)
    return cls


def register_annotation_plugin(cls: type[ModalityPlugin]) -> type[ModalityPlugin]:
    """Register a plugin for its *annotation kind* only, leaving the modality slot alone.

    A recipe-dispatched plugin (handwriting OCR on image files) shares a modality with a
    builtin plugin that must keep the modality's slot: uploads stay validated by the builtin,
    annotations dispatch by kind. This is the by-kind half of ``register_plugin`` without the
    modality half.
    """
    if not isinstance(getattr(cls, "modality", ""), str) or not cls.modality:
        raise ValueError("A modality plugin needs a non-empty `modality`.")
    return _register_annotation_plugin(cls)


def _register_annotation_plugin(cls: type[ModalityPlugin]) -> type[ModalityPlugin]:
    kind = getattr(cls(), "annotation_kind", "")
    if isinstance(kind, str) and kind:
        _annotation_registry[kind] = cls
        _annotation_instances.pop(kind, None)
    return cls


def unregister_plugin(modality: str) -> None:
    cls = _registry.pop(modality, None)
    _instances.pop(modality, None)
    if cls is not None:
        kind = getattr(cls(), "annotation_kind", "")
        if isinstance(kind, str) and kind and _annotation_registry.get(kind) is cls:
            _annotation_registry.pop(kind, None)
            _annotation_instances.pop(kind, None)


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
    # The by-kind plugins (a recipe asking an image project for transcripts, say) register
    # themselves as an import side effect, so importing the module is the whole load. Without
    # this, a live server never imports them and every recipe-dispatched annotation is refused
    # with "data type isn't supported yet" — the tests masked it by importing the module in setUp.
    for module_name in BUILTIN_ANNOTATION_PLUGINS:
        try:
            importlib.import_module(module_name)
        except Exception:  # noqa: BLE001 - one bad plugin must not hide the others
            logger.exception("Skipping annotation plugin module %s", module_name)


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


def get_annotation_plugin(kind: str) -> ModalityPlugin | None:
    """The plugin whose annotations are of ``kind`` (e.g. ``"transcripts"``), or ``None``.

    Two lookups: the by-kind table ``register_plugin`` fills (a class that shares a modality
    with another plugin but annotates differently lives only here), then the registered
    modality plugins whose kind matches (a modality's own plugin is always found through it)."""
    _load_all()
    cls = _annotation_registry.get(kind)
    if cls is not None:
        if kind not in _annotation_instances:
            _annotation_instances[kind] = cls()
        return _annotation_instances[kind]
    for modality in sorted(_registry):
        plugin = get_plugin(modality)
        if getattr(plugin, "annotation_kind", "") == kind:
            return plugin
    return None


__all__ = [
    "ModalityPlugin",
    "UnsupportedModalityError",
    "available_modalities",
    "get_annotation_plugin",
    "get_plugin",
    "plugin_or_refusal",
    "register_plugin",
    "reload_plugins",
    "unregister_plugin",
]
