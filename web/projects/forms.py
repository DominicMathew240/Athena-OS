from __future__ import annotations

from typing import Any

from django import forms

from .models import MODALITY_CHOICES

MAX_LABELS = 50
MAX_LABEL_LENGTH = 64


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
