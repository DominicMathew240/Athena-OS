"""The task-recipe contract: what Athena is asked to learn, independent of the file format.

A *recipe* is the operator-facing answer to "What do you want Athena to learn?". It names the
data kind (a :data:`~projects.models.MODALITY_CHOICES` value), the annotation style its labels
use (``annotation_kind``, e.g. ``"boxes"`` for image detection or ``"transcripts"`` for OCR)
and, when the recipe cannot run yet, a plain-language reason. Everything downstream (the
guided project form, annotation validation, training-job selection) reads the registry, so a
new modality lands without modality-specific branches in core code.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TaskRecipe:
    #: Stable identifier used in URLs and stored on projects. Lower-case letters, digits, "-".
    key: str
    #: Short operator-facing title, e.g. "Recognize objects in pictures".
    title: str
    #: The data kind this recipe works on; one of ``Project.MODALITY_CHOICES``'s values.
    modality: str
    #: One or two plain sentences saying what the project will do with the data.
    summary: str
    #: Which annotation style this recipe labels use (a slug shared with the modality plugin).
    annotation_kind: str
    #: Plain-language guidance for what the labels mean in this recipe (shown while labeling).
    labels_help: str
    #: Whether a project of this recipe can be set up and used today.
    available: bool = True
    #: Why not, in one sentence, when ``available`` is false. Always plain language.
    unavailable_reason: str = ""

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "title": self.title,
            "modality": self.modality,
            "summary": self.summary,
            "annotation_kind": self.annotation_kind,
            "labels_help": self.labels_help,
            "available": self.available,
            "unavailable_reason": self.unavailable_reason,
        }
