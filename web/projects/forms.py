from __future__ import annotations

from django import forms

from .models import MODALITY_CHOICES

MAX_LABELS = 50


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

        labels = cleaned.get("labels")
        if labels is None:
            labels = []
        if not isinstance(labels, list):
            self.add_error("labels", "Labels must be a list of names.")
            return cleaned
        if len(labels) > MAX_LABELS:
            self.add_error("labels", f"A project can have at most {MAX_LABELS} labels.")
            return cleaned

        normalized: list[str] = []
        seen: set[str] = set()
        for label in labels:
            if not isinstance(label, str):
                self.add_error("labels", "Each label must be text.")
                return cleaned
            label = label.strip()
            if not label or len(label) > 64:
                self.add_error("labels", "Each label must contain 1 to 64 characters.")
                return cleaned
            key = label.casefold()
            if key in seen:
                self.add_error("labels", "Labels must be unique within a project.")
                return cleaned
            seen.add(key)
            normalized.append(label)
        cleaned["labels"] = normalized
        return cleaned
