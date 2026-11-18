"""Recipe-aware annotation dispatch: which annotation style a project actually uses.

Wave 0 wired the generic :class:`~projects.models.AssetAnnotation` endpoints to the project's
*modality plugin*, which is right for a modality that owns its annotation style end to end
(audio clips, tables). The image modality is different: its plugin's style is boxes, but a
recipe can ask for something else on the same pictures (handwriting OCR wants transcripts).
The two styles use different endpoints and different data, so the wrong dispatch would send
an OCR project's transcripts to the box endpoints and refuse them.

The rule in one sentence: **the recipe's annotation style wins when the project has one; the
modality plugin's is the fallback** — the same rule ``datasets.generic.expected_annotation_kind``
already applies to snapshots. Image projects without a recipe keep the vision default, so
every existing project behaves exactly as before.
"""

from __future__ import annotations

from .modalities import get_annotation_plugin, get_plugin
from .modalities.base import ModalityPlugin
from .models import Project
from .tasks import recipe_for_project


def annotation_plugin(project: Project) -> ModalityPlugin | None:
    """The plugin whose ``annotation_kind`` / ``validate_annotation`` this project's labels use.

    A project whose recipe names a *different* annotation kind than its modality plugin gets
    the plugin registered for that kind (see :mod:`projects.modalities.registry`). Everything
    else, including every image project without a recipe, gets its modality's own plugin,
    unchanged.
    """
    plugin = get_plugin(project.modality)
    recipe = recipe_for_project(project)
    if plugin is None or recipe is None:
        return plugin
    if recipe.annotation_kind and recipe.annotation_kind != plugin.annotation_kind:
        return get_annotation_plugin(recipe.annotation_kind)
    return plugin


def image_project_uses_boxes(project: Project) -> bool:
    """Whether an image project's annotation endpoints are the box ones.

    ``True`` unless the project's recipe says otherwise; only a recipe can differ from the
    image default, and a missing recipe means the vision default, which is boxes.
    """
    if project.modality != "image":
        return False
    recipe = recipe_for_project(project)
    if recipe is None:
        return True
    return recipe.annotation_kind == "boxes"
