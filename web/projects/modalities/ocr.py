"""The handwriting-OCR annotation plugin: picture files, transcript annotations.

An OCR project is an *image* project (the picture upload path, its limits and its messages are
the image plugin's, inherited unchanged), but its annotation is not a box: it is the corrected
text the handwriting on the picture says. The annotation body is

    {"label": "transcript", "text": "what the picture says, typed by a person"}

Why the ``label`` key at all: the frozen-snapshot convention (``datasets.generic``) reads
``data["label"]`` as the annotation's *primary project label* — a string that must be one of
the project's label names. A transcript is free text, not a class name, and project labels are
at most 120 characters, so the transcript itself cannot be the label. The sanctioned fit: the
project is created with a single label named ``transcript`` (the recipe's guidance says to keep
exactly that one), ``data["label"]`` = ``"transcript"`` satisfies the snapshot convention, and
``data["text"]`` carries the actual transcript that training reads from
``DatasetEntry.annotation["text"]``.

Importing this module registers the plugin (by annotation kind, through
``register_plugin`` — the ``image`` *modality* stays the base ``ImageModalityPlugin``) and
replaces the ``handwriting-ocr`` recipe placeholder with the available recipe. The recipe
dispatch in ``projects.annotations`` finds the plugin through the kind lookup.
"""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError

from .image import ImageModalityPlugin

#: The one project label an OCR project is expected to carry (see the module docstring).
TRANSCRIPT_LABEL = "transcript"
#: A transcript is a person's typed correction of one handwritten entry; longer than this is
#: almost certainly the wrong tool (a document, not a line).
MAX_TRANSCRIPT_CHARS = 1000


class OcrImagePlugin(ImageModalityPlugin):
    """Image validation exactly as the image builtin does it; annotations are transcripts."""

    modality = "image"
    asset_noun = "picture"
    annotation_kind = "transcripts"

    def validate_annotation(self, payload: dict[str, Any], project) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValidationError("A transcript needs the label and the text.")
        unknown = set(payload) - {"label", "text"}
        if unknown:
            raise ValidationError(
                "A transcript only has a label and a text. Remove "
                + ", ".join(sorted(map(str, unknown)))
                + " and try again."
            )
        label = payload.get("label")
        if not isinstance(label, str) or label != TRANSCRIPT_LABEL:
            raise ValidationError(
                f"The label of a transcript must be exactly {TRANSCRIPT_LABEL!r}. "
                "Keep that one label in this project and put the handwriting's text in 'text'."
            )
        if TRANSCRIPT_LABEL not in getattr(project, "labels", []):
            raise ValidationError(
                f"This project needs a label named {TRANSCRIPT_LABEL!r} before transcripts can be saved."
            )
        text = payload.get("text")
        if not isinstance(text, str):
            raise ValidationError("The transcript text must be text.")
        text = text.strip()
        if not text:
            raise ValidationError("Type what the handwriting says before saving.")
        if len(text) > MAX_TRANSCRIPT_CHARS:
            raise ValidationError(
                f"The transcript is too long (limit {MAX_TRANSCRIPT_CHARS} characters). "
                "Split the picture into smaller pieces and transcribe each one."
            )
        if any(ord(character) < 32 and character not in "\n\r\t" for character in text):
            raise ValidationError("The transcript contains unsupported control characters.")
        return {"label": TRANSCRIPT_LABEL, "text": text}


# Importing this module is what turns the Wave 1 OCR slice on: the plugin gets its by-kind
# registry entry (the "image" modality stays the builtin image plugin) and the recipe
# placeholder is replaced by the available recipe. Deliberately last, after the class.
from ..tasks import HANDWRITING_OCR, register_recipe  # noqa: E402
from .registry import register_plugin as _register_plugin  # noqa: E402

_register_plugin(OcrImagePlugin)
register_recipe(HANDWRITING_OCR, replace=True)
