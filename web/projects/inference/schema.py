"""Strict validation of an inference app's configuration.

Unknown keys are rejected so a typo never silently changes what operators see. A request may send
a partial config; it is merged onto the defaults (create) or the stored config (update) and the
*whole* result is validated.
"""

from __future__ import annotations

import copy
import math
from typing import Any

ACCEPTED_INPUTS = ("image/jpeg", "image/png", "image/webp")
DECISION_OPTIONS = ("confirm", "reject", "needs_second_look")
STATE_KEYS = ("clear", "flagged", "needs_review")
CONFIG_KEYS = {
    "task_title",
    "accepted_inputs",
    "displayed_labels",
    "confidence_threshold",
    "status_texts",
    "decision_controls",
    "human_review_required",
}
DISCLAIMER = (
    "This is a model's output, not a final determination. It can be wrong. "
    "A person must check it before anything is decided or acted on."
)
DEFAULT_STATUS_TEXTS = {
    "clear": "Nothing flagged by the model",
    "flagged": "Model flagged something to check",
    "needs_review": "Needs review by a person",
}
MAX_TEXT = 200


class ConfigError(ValueError):
    """The configuration is invalid; the message says which field and what is allowed."""


def default_config(name: str, labels: list[str]) -> dict[str, Any]:
    return {
        "task_title": name,
        "accepted_inputs": list(ACCEPTED_INPUTS),
        "displayed_labels": list(labels),
        "confidence_threshold": 0.5,
        "status_texts": dict(DEFAULT_STATUS_TEXTS),
        "decision_controls": {"options": list(DECISION_OPTIONS), "required_note_on_override": True},
        "human_review_required": True,
    }


def _text(value: Any, field: str, limit: int = MAX_TEXT) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{field} must be text.")
    value = value.strip()
    if not value or len(value) > limit or not value.isprintable():
        raise ConfigError(f"{field} must be 1–{limit} characters of plain text.")
    return value


def merged(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(patch, dict):
        raise ConfigError("config must be an object.")
    unknown = set(patch) - CONFIG_KEYS
    if unknown:
        raise ConfigError(f"Unsupported config field: {', '.join(sorted(map(str, unknown)))}.")
    result = copy.deepcopy(base)
    for key, value in patch.items():
        if key in ("status_texts", "decision_controls"):
            if not isinstance(value, dict):
                raise ConfigError(f"{key} must be an object.")
            result[key] = {**result.get(key, {}), **value}
        else:
            result[key] = value
    return result


def validate_config(config: dict[str, Any], model_labels: list[str]) -> dict[str, Any]:
    """Return the normalized config or raise ``ConfigError``."""
    if set(config) != CONFIG_KEYS:
        missing = CONFIG_KEYS - set(config)
        raise ConfigError(f"Config is missing: {', '.join(sorted(missing))}." if missing else "Unsupported config field.")
    title = _text(config["task_title"], "task_title", 120)

    inputs = config["accepted_inputs"]
    if not isinstance(inputs, list) or not inputs or len(set(inputs)) != len(inputs) or any(
        item not in ACCEPTED_INPUTS for item in inputs
    ):
        raise ConfigError(f"accepted_inputs must be a non-empty list drawn from: {', '.join(ACCEPTED_INPUTS)}.")

    shown = config["displayed_labels"]
    if not isinstance(shown, list) or not shown or len(set(map(str, shown))) != len(shown):
        raise ConfigError("displayed_labels must be a non-empty list of label names without repeats.")
    outside = [label for label in shown if label not in model_labels]
    if outside:
        raise ConfigError(
            f"displayed_labels must be labels the model knows ({', '.join(model_labels)}); not recognised: "
            f"{', '.join(map(str, outside))}."
        )

    threshold = config["confidence_threshold"]
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ConfigError("confidence_threshold must be a number from 0 to 1.")

    texts = config["status_texts"]
    if not isinstance(texts, dict) or set(texts) != set(STATE_KEYS):
        raise ConfigError(f"status_texts must give exactly: {', '.join(STATE_KEYS)}.")
    texts = {key: _text(texts[key], f"status_texts.{key}") for key in STATE_KEYS}

    controls = config["decision_controls"]
    if not isinstance(controls, dict) or set(controls) != {"options", "required_note_on_override"}:
        raise ConfigError("decision_controls must have options and required_note_on_override.")
    options = controls["options"]
    if (
        not isinstance(options, list)
        or not options
        or len(set(options)) != len(options)
        or any(item not in DECISION_OPTIONS for item in options)
    ):
        raise ConfigError(f"decision_controls.options must be a non-empty list drawn from: {', '.join(DECISION_OPTIONS)}.")
    if not isinstance(controls["required_note_on_override"], bool):
        raise ConfigError("decision_controls.required_note_on_override must be true or false.")

    if not isinstance(config["human_review_required"], bool):
        raise ConfigError("human_review_required must be true or false.")
    if config["human_review_required"] and not ({"confirm", "reject"} & set(options)):
        raise ConfigError(
            "When human review is required, decision_controls.options must include confirm or reject so a person can record a decision."
        )
    return {
        "task_title": title,
        "accepted_inputs": [item for item in ACCEPTED_INPUTS if item in inputs],
        "displayed_labels": [label for label in model_labels if label in shown],
        "confidence_threshold": float(threshold),
        "status_texts": texts,
        "decision_controls": {
            "options": [item for item in DECISION_OPTIONS if item in options],
            "required_note_on_override": controls["required_note_on_override"],
        },
        "human_review_required": config["human_review_required"],
    }
