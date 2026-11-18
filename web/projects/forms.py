from __future__ import annotations

import re
from typing import Any

from django import forms

from .models import MODALITY_CHOICES
from .tasks import DEFAULT_RECIPE_KEY, get_recipe

MAX_LABELS = 50
MAX_LABEL_LENGTH = 64
RECIPE_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def normalize_label_names(labels: Any) -> list[str]:
    """Trim and validate a list of label names, returning them in the given order.

    Every message is written for the person using the console: it says what is wrong
    and what to change. Raises ``forms.ValidationError`` on the first problem.
    """
    if labels is None:
        return []
    if not isinstance(labels, list):
        raise forms.ValidationError("Labels must be sent as a list of names.")
    if len(labels) > MAX_LABELS:
        raise forms.ValidationError(
            f"A project can have at most {MAX_LABELS} labels. Remove some labels and try again."
        )
    normalized: list[str] = []
    seen: dict[str, str] = {}
    for label in labels:
        if not isinstance(label, str):
            raise forms.ValidationError("Each label must be plain text.")
        label = label.strip()
        if not label:
            raise forms.ValidationError("A label name is empty. Type a name or remove that row.")
        if len(label) > MAX_LABEL_LENGTH:
            raise forms.ValidationError(
                f"“{label[:20]}…” is too long. Label names can be up to {MAX_LABEL_LENGTH} characters."
            )
        if not label.isprintable():
            raise forms.ValidationError(
                f"“{label[:20]}” has a line break or hidden character. Use letters, numbers, and spaces."
            )
        key = label.casefold()
        if key in seen:
            raise forms.ValidationError(
                f"“{label}” appears twice (capital letters do not count as different). "
                "Give each label its own name."
            )
        seen[key] = label
        normalized.append(label)
    return normalized


class ProjectCreateForm(forms.Form):
    name = forms.CharField(max_length=120, strip=True)
    modality = forms.ChoiceField(choices=MODALITY_CHOICES)
    custom_modality = forms.CharField(max_length=80, required=False, strip=True)
    task = forms.CharField(max_length=120, strip=True)
    labels = forms.JSONField(required=False)
    # The recipe key picked in the guided form (see projects.tasks); optional and validated
    # against the registry so a stale console cannot store a made-up key.
    recipe = forms.CharField(max_length=64, required=False, strip=True)

    def clean_recipe(self):
        key = self.cleaned_data.get("recipe") or ""
        if not key:
            return ""
        if not RECIPE_KEY_PATTERN.match(key):
            raise forms.ValidationError("Choose a task from the list, or leave it empty.")
        recipe = get_recipe(key)
        if recipe is None:
            raise forms.ValidationError("That task is not known here. Reload the page and pick again.")
        if recipe.modality != self.cleaned_data.get("modality"):
            raise forms.ValidationError("That task needs a different kind of files. Pick the task again.")
        return key

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("modality") == "custom" and not cleaned.get("custom_modality"):
            self.add_error("custom_modality", "Enter a name for the custom data type.")
        if cleaned.get("modality") != "custom":
            cleaned["custom_modality"] = ""

        try:
            cleaned["labels"] = normalize_label_names(cleaned.get("labels"))
        except forms.ValidationError as error:
            self.add_error("labels", error)
        return cleaned


def default_recipe_for(modality: str) -> str:
    """The recipe key stored when a caller did not pick one: the vision default for image
    projects, nothing otherwise (unavailable recipes are never assigned silently)."""
    return DEFAULT_RECIPE_KEY if modality == "image" else ""
