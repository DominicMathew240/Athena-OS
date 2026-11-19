"""Modality plugins: one pluggable class per data kind (image today; audio, tables, ... next).

``projects.modalities.base`` is the contract, ``projects.modalities.registry`` the lookup. A
Wave 1 agent registers a new modality's plugin with ``register_plugin(MyPlugin)`` from its own
module (or via ``settings.MODALITY_PLUGINS``); no core file needs editing.
"""

from __future__ import annotations

from .base import ModalityPlugin, UnsupportedModalityError
from .registry import (
    available_modalities,
    get_annotation_plugin,
    get_plugin,
    plugin_or_refusal,
    register_annotation_plugin,
    register_plugin,
    reload_plugins,
    unregister_plugin,
)

__all__ = [
    "ModalityPlugin",
    "UnsupportedModalityError",
    "available_modalities",
    "get_annotation_plugin",
    "get_plugin",
    "plugin_or_refusal",
    "register_annotation_plugin",
    "register_plugin",
    "reload_plugins",
    "unregister_plugin",
]
