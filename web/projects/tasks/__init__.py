"""The task-recipe registry, mirroring the training-backend registry in :mod:`projects.training`.

A recipe is looked up by ``key`` from a registry that is filled from (1) the built-in recipes
below, (2) ``settings.RECIPES`` (``"module:Class"`` strings, same style as
``settings.TRAINING["BACKENDS"]``), and (3) ``register_recipe()`` calls. A Wave 1 agent that
lands an audio or OCR plugin flips its placeholder to available (or replaces it wholesale)
with a one-line change here, or - better - registers its own recipe from its own module:

    from projects.tasks import register_recipe
    register_recipe(TaskRecipe(key="audio-classification", ...))

``available=False`` recipes are still listed (the console shows them as "Coming soon"); a
project created with one keeps its name and labels but its upload/teach tools refuse plainly.
"""

from __future__ import annotations

import importlib
import logging
import re

from django.conf import settings

from .base import TaskRecipe

logger = logging.getLogger(__name__)

KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

#: Vision detection. The one recipe that is fully available today (Wave 0).
VISION_DETECTION = TaskRecipe(
    key="vision-detection",
    title="Recognize objects in pictures",
    modality="image",
    summary="Teach Athena to spot items or surface defects in pictures and draw a box around each one.",
    annotation_kind="boxes",
    labels_help="One label per thing to spot, e.g. dent, scratch, missing_part.",
    available=True,
)

#: Sound classification (Wave 1, OS-AUD-001): WAV clips, one label per clip, taught by the
#: audio-wav-cnn backend. A project created with it can upload clips, label them, freeze
#: dataset versions and train today.
AUDIO_CLASSIFICATION = TaskRecipe(
    key="audio-classification",
    title="Recognize sounds or equipment faults",
    modality="audio",
    summary="Teach Athena to tell equipment sounds or unusual faults apart from short recordings.",
    annotation_kind="clip_labels",
    labels_help="One label per sound or fault, e.g. healthy_motor, bearing_fault, belt_slip.",
    available=True,
)
#: Handwriting OCR (Wave 1). The labels_help explains the one-label convention: the project's
#: single label is named "transcript" (it satisfies the snapshot convention's data["label"]),
#: and the actual corrected text lives in the annotation's "text" key. See
#: ``projects/modalities/ocr.py`` for the full contract.
HANDWRITING_OCR = TaskRecipe(
    key="handwriting-ocr",
    title="Read handwriting",
    modality="image",
    summary=(
        "Teach Athena to read handwritten text in pictures. You type the correct text for each "
        "picture; Athena learns to read it herself and is checked by character and word errors."
    ),
    annotation_kind="transcripts",
    labels_help=(
        "Keep exactly one label, named transcript. The picture's corrected text goes with it: "
        "open a picture and type what the handwriting says."
    ),
    available=True,
)

#: Placeholders for the remaining Wave 1 recipes. To make one available, register a recipe with
#: the same key from the plugin module that ships its modality plugin and training backend,
#: e.g. ``register_recipe(replace(...))`` here or ``settings.RECIPES += ["my_pkg.recipes:Table"]``.
TABULAR_PREDICTION = TaskRecipe(
    key="tabular-prediction",
    title="Predict from a spreadsheet",
    modality="table",
    summary=(
        "Upload one Excel or CSV file, choose the column to predict, and Athena learns to "
        "guess that column from the others. It is tested on rows it has never seen."
    ),
    annotation_kind="table_target",
    labels_help=(
        "For a table, the labels are the file's columns. Pick the one Athena should predict - "
        "the others are used as clues. Choose it after you upload the file."
    ),
    available=True,
)

BUILTIN_RECIPES = (VISION_DETECTION, AUDIO_CLASSIFICATION, HANDWRITING_OCR, TABULAR_PREDICTION)

#: Text classification (Wave 2, OS-NLP-001 - classification only; NER is future work): one
#: CSV of {text, label} rows, taught by the text-sklearn backend (bag-of-words/TF-IDF + a
#: linear classifier). A project created with it can upload its CSV, confirm the label list,
#: freeze dataset versions and train today.
TEXT_CLASSIFICATION = TaskRecipe(
    key="text-classification",
    title="Sort text by topic or outcome",
    modality="text",
    summary=(
        "Upload one CSV file of short texts and their labels, and Athena learns to sort new "
        "texts into those labels. It is checked on texts it has never seen."
    ),
    annotation_kind="text_dataset",
    labels_help=(
        "For text, the labels are the values of the file's label column - the categories to "
        "sort texts into. Confirm them after you upload the file."
    ),
    available=True,
)

BUILTIN_RECIPES = (VISION_DETECTION, AUDIO_CLASSIFICATION, HANDWRITING_OCR, TABULAR_PREDICTION, TEXT_CLASSIFICATION)

#: Signal classification (Wave 2, OS-SIG-001 - classification only; forecasting and anomaly
#: detection are future work): one CSV of labelled series (wide format: a series column, one
#: value column per time step, a label column), taught by the signal-sklearn backend (window
#: statistics + a linear classifier). A project created with it can upload its CSV, confirm
#: the label list, freeze dataset versions and train today.
SIGNAL_CLASSIFICATION = TaskRecipe(
    key="signal-classification",
    title="Tell signal patterns apart",
    modality="time_series",
    summary=(
        "Upload one CSV file of measurement series and their labels, and Athena learns to sort "
        "new series into those labels. It is checked on series it has never seen."
    ),
    annotation_kind="signal_dataset",
    labels_help=(
        "For signals, the labels are the values of the file's label column - the kinds of "
        "series to tell apart. Confirm them after you upload the file."
    ),
    available=True,
)

BUILTIN_RECIPES = (
    VISION_DETECTION, AUDIO_CLASSIFICATION, HANDWRITING_OCR, TABULAR_PREDICTION,
    TEXT_CLASSIFICATION, SIGNAL_CLASSIFICATION,
)

#: Document fields (Wave 2, OS-DOC-001 - table/CSV only; no PDF, no OCR, no layout analysis,
#: and no model training: extraction here is rule-based validation and normalization, and the
#: slice's "evaluation" is the version-to-version table comparison in
#: ``projects.training.document_eval``, not a trained model). The recipe shares the ``text``
#: modality with text classification (the console's "Text / documents" kind): its plugin is
#: registered by annotation kind (``document_fields``) and dispatches through the recipe, the
#: OCR pattern, so text classification keeps its own plugin untouched.
DOCUMENT_FIELDS = TaskRecipe(
    key="document-fields",
    title="Check fields in document tables",
    modality="text",
    summary=(
        "Upload one CSV (or Excel) file of structured records, define the fields it must carry "
        "(text, number or date), and Athena validates every cell. Two saved versions can be "
        "compared field by field to score extraction accuracy."
    ),
    annotation_kind="document_fields",
    labels_help=(
        "For documents, each label is one field to check, written as name:type - the type is "
        "text, number or date. A label without a type is a text field."
    ),
    available=True,
)

BUILTIN_RECIPES = (
    VISION_DETECTION, AUDIO_CLASSIFICATION, HANDWRITING_OCR, TABULAR_PREDICTION,
    TEXT_CLASSIFICATION, SIGNAL_CLASSIFICATION, DOCUMENT_FIELDS,
)

#: The recipe assumed for image projects created before recipes existed, and for any image
#: project whose caller did not pick one.
DEFAULT_RECIPE_KEY = "vision-detection"

_registry: dict[str, TaskRecipe] = {}
_loaded = False


def register_recipe(recipe: TaskRecipe, *, replace: bool = False) -> TaskRecipe:
    key = getattr(recipe, "key", "")
    if not isinstance(key, str) or not KEY_PATTERN.match(key):
        raise ValueError("A recipe needs a `key` of lower-case letters, digits and '-' (at most 64 characters).")
    if key in _registry and not replace:
        raise ValueError(f"A recipe named {key!r} is already registered.")
    _registry[key] = recipe
    return recipe


def unregister_recipe(key: str) -> None:
    _registry.pop(key, None)


def _load_path(path: str) -> TaskRecipe:
    module_name, _, attribute = path.partition(":")
    if not attribute:
        raise ValueError(f"Recipe paths look like 'module:attribute', got {path!r}.")
    return getattr(importlib.import_module(module_name), attribute)


def _load_all() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    for recipe in BUILTIN_RECIPES:
        try:
            register_recipe(recipe, replace=True)
        except ValueError:
            logger.exception("Skipping built-in recipe %s", recipe.key)
    for path in getattr(settings, "RECIPES", []) or []:
        try:
            register_recipe(_load_path(path))
        except Exception:  # noqa: BLE001 - one bad recipe must not hide the others
            logger.exception("Skipping task recipe %s", path)


def reload_recipes() -> None:
    """Forget what was discovered (tests, and operators installing a plugin without restarting)."""
    global _loaded
    _loaded = False


def recipes() -> dict[str, TaskRecipe]:
    _load_all()
    return dict(sorted(_registry.items()))


def get_recipe(key: str) -> TaskRecipe | None:
    _load_all()
    return _registry.get(key)


def recipe_for_project(project) -> TaskRecipe | None:
    """The recipe a project was created with; ``None`` when it has none or it is unknown.

    ``Project.recipe`` holds the recipe *key* (``Project.task`` stays the human-readable text
    the person typed or the recipe pre-filled)."""
    key = getattr(project, "recipe", None)
    return get_recipe(key) if key else None


__all__ = [
    "TaskRecipe",
    "BUILTIN_RECIPES",
    "DEFAULT_RECIPE_KEY",
    "get_recipe",
    "recipe_for_project",
    "recipes",
    "register_recipe",
    "reload_recipes",
    "unregister_recipe",
]
