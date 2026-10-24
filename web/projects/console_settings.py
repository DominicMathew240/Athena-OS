"""Console-wide switches an administrator can change in the console. Today there is one: ready-made model downloads.

**The web process never fetches weights.** The switch only stores a setting (a :class:`~projects.models.ConsoleSetting`
row) and writes a :class:`~projects.models.ConsoleSettingEvent`. The teaching helper (``manage.py train_worker``),
which is the process that downloads, reads the setting itself, from the same database, each time a run starts
(:func:`projects.training.torchvision_frcnn.ensure_pretrained_weights`) and each time it reports for duty.

Precedence for "may ready-made models be downloaded?":

1. a stored console setting, on or off, wins -> ``source == "console"``. So an administrator can also switch
   downloads *off* in the console while the environment says on;
2. otherwise the environment variable ``ATHENA_TRAINING_ALLOW_DOWNLOAD`` when it is set (any non-blank value;
   ``1``/``true``/``yes`` mean on, anything else off) -> ``source == "environment"``;
3. otherwise off -> ``source == "default"``.

If the database cannot be read (a helper started by a script with no database, a connection that dropped) the
environment decides, as it did before this switch existed.

The licence stance does not change: the per-run ``acknowledge_pretrained_terms`` tick is still required on every
run, on the server. The tick asked for here is the administrator's own, once, when switching downloads on.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.apps import apps
from django.utils import timezone

TRAINING_DOWNLOADS = "training_downloads"  # the key of the ready-made-model download switch
TRAINING_DOWNLOAD_ENV = "ATHENA_TRAINING_ALLOW_DOWNLOAD"
TRUE_WORDS = {"1", "true", "yes"}
ON, OFF = "on", "off"
SOURCE_CONSOLE, SOURCE_ENVIRONMENT, SOURCE_DEFAULT = "console", "environment", "default"
MIN_NOTE_CHARS = 3
NOTE_MAX_CHARS = 300  # the length of ConsoleSettingEvent.note; the Access page's note box has the same maxlength (tests pin both)
ADMIN_ROLE = "administrator"

FETCHED_BY = (
    "The teaching helper (the program started with `python manage.py train_worker`) fetches the files, once, from "
    "download.pytorch.org when a run needs them. This page only records whether it may: the console itself never "
    "downloads anything."
)
PRECEDENCE = (
    "A choice made here wins over the setting ATHENA_TRAINING_ALLOW_DOWNLOAD in the helper’s environment, in both "
    "directions. The environment setting is only used until someone chooses here. Once someone has chosen here, this "
    "page can only switch downloads on or off; it cannot hand the decision back to the environment setting."
)
NOTE_CHARACTERS = "The note contains characters that cannot be saved."
JOINERS = "\u200c\u200d"  # zero-width (non-)joiner: part of emoji sequences and of Persian/Indic spelling
ACKNOWLEDGE_ASK = (
    "To switch ready-made model downloads on, tick the box saying you have read the licence note, and write a short "
    "note saying why (at least 3 characters)."
)
STALE_EDIT = (
    "Someone changed this setting while you had the page open (downloads are now {now}), so nothing was changed. "
    "Reload the page to see it, then choose again."
)


@dataclass(frozen=True)
class Resolved:
    allow: bool
    source: str  # "console" | "environment" | "default"
    stored: Any = None  # the ConsoleSetting row, when there is one
    environment_value: str | None = None  # the raw variable, stripped; None when it is not set


class SettingError(Exception):
    """A change that was refused, with a sentence a person can read."""

    def __init__(self, message: str, status: int = 400, **extra: Any) -> None:
        super().__init__(message)
        self.message, self.status, self.extra = message, status, extra


def environment_value() -> str | None:
    """The raw variable (stripped), or ``None`` when it is unset or blank."""
    raw = os.environ.get(TRAINING_DOWNLOAD_ENV)
    raw = raw.strip() if raw is not None else ""
    return raw or None


def _stored(key: str = TRAINING_DOWNLOADS):
    from .models import ConsoleSetting

    row = ConsoleSetting.objects.filter(pk=key).first()
    return row if row is not None and row.value in (ON, OFF) else None


def _read_stored():
    """The stored row, or ``None`` - also when the database cannot be read (then the environment decides)."""
    try:
        if not apps.ready:
            return None
        return _stored()
    except (DatabaseError, ImproperlyConfigured):
        # Only a connection that really is dead is dropped (so a long-lived helper reconnects on its next read). A
        # usable one - a lock, a timeout, an in-memory database - is left alone: nothing is closed under the caller.
        if not connection.in_atomic_block and connection.connection is not None and connection.errors_occurred:
            if not connection.is_usable():
                connection.close()
        return None


def training_downloads() -> Resolved:
    """May ready-made models be downloaded right now, and who said so? Safe to call from any process."""
    row = _read_stored()
    env = environment_value()
    if row is not None:
        return Resolved(row.value == ON, SOURCE_CONSOLE, row, env)
    if env is not None:
        return Resolved(env.lower() in TRUE_WORDS, SOURCE_ENVIRONMENT, None, env)
    return Resolved(False, SOURCE_DEFAULT, None, None)


def training_downloads_allowed() -> bool:
    return training_downloads().allow


def _label(allow: bool) -> str:
    return ON if allow else OFF


def clean_note(value: Any, *, required: bool) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise SettingError("The note must be text.")
    text = " ".join(value.split())
    if any(not (ch.isprintable() or ch in JOINERS) for ch in text):
        raise SettingError(NOTE_CHARACTERS)  # NUL, BEL, ESC, bidi overrides and the like: plain text only
    limit = NOTE_MAX_CHARS
    if len(text) > limit:
        raise SettingError(f"The note is too long ({limit} characters at most).")
    if required and len(text) < MIN_NOTE_CHARS:
        raise SettingError(ACKNOWLEDGE_ASK)
    return text


def set_training_downloads(
    allow: bool, *, actor, acknowledge_terms: Any = False, note: Any = "", based_on: bool | None = None
):
    """Store the switch. Returns the new :class:`ConsoleSettingEvent`, or ``None`` when nothing changed.

    Turning on needs ``acknowledge_terms is True`` and a note of at least three characters. ``based_on`` (what the
    caller last saw, as a boolean) is compared with what is in force now, under a lock; a mismatch is a 409.
    Choosing a value when none is stored yet always counts as a change (it makes the console the source), even if
    the environment already gave that value.
    """
    from .models import ConsoleSetting, ConsoleSettingEvent
    from . import permissions

    if not isinstance(allow, bool):
        raise SettingError("“allow” must be true or false.")
    if allow:
        if acknowledge_terms is not True:
            raise SettingError(ACKNOWLEDGE_ASK)
        note = clean_note(note, required=True)
    else:
        note = clean_note(note, required=False)
    actor_pk = getattr(actor, "pk", None)
    with transaction.atomic():
        row = ConsoleSetting.objects.select_for_update().filter(pk=TRAINING_DOWNLOADS).first()
        if row is None:
            try:
                with transaction.atomic():
                    row = ConsoleSetting.objects.create(key=TRAINING_DOWNLOADS, value="", updated_at=timezone.now())
            except IntegrityError:
                row = ConsoleSetting.objects.select_for_update().get(pk=TRAINING_DOWNLOADS)
        before = _resolve_with(row)
        if based_on is not None and based_on != before.allow:
            raise SettingError(STALE_EDIT.format(now=_label(before.allow)), 409, allow=before.allow, source=before.source)
        if row.value == _label(allow):
            return None
        row.value = _label(allow)
        row.updated_at = timezone.now()
        row.updated_by = actor if actor_pk else None
        row.save()
        return ConsoleSettingEvent.objects.create(
            key=TRAINING_DOWNLOADS,
            actor=actor if actor_pk else None,
            actor_ref=permissions.opaque_id(actor) or "" if actor_pk else "",
            actor_role=ADMIN_ROLE if getattr(actor, "is_superuser", False) else (permissions.role_of(actor) or ""),
            before=_label(before.allow),
            before_source=before.source,
            after=_label(allow),
            terms_acknowledged=bool(allow and acknowledge_terms is True),
            note=note,
            created_at=row.updated_at,
        )


def _resolve_with(row) -> Resolved:
    """Like :func:`training_downloads`, but for a row we already hold (a blank value counts as no row)."""
    env = environment_value()
    if row is not None and row.value in (ON, OFF):
        return Resolved(row.value == ON, SOURCE_CONSOLE, row, env)
    if env is not None:
        return Resolved(env.lower() in TRUE_WORDS, SOURCE_ENVIRONMENT, None, env)
    return Resolved(False, SOURCE_DEFAULT, None, None)


def event_data(event) -> dict[str, Any]:
    return {
        "id": event.pk,
        "when": event.created_at.isoformat(),
        "by": event.actor_ref,
        "role": event.actor_role,
        "before": event.before,
        "before_source": event.before_source,
        "after": event.after,
        "terms_acknowledged": event.terms_acknowledged,
        "note": event.note,
    }


def licence_text() -> dict[str, Any]:
    """The licence wording the Teach panel shows today, taken from the backend unchanged."""
    from .training.torchvision_frcnn import GENERAL_TERMS, START_OPTION_TEXT, TorchvisionFasterRCNN

    return {
        "general_terms": GENERAL_TERMS,
        "options": [
            {"value": value, "title": START_OPTION_TEXT[value]["title"], "licence_plain": START_OPTION_TEXT[value]["licence_plain"]}
            for value in ("imagenet_backbone", "coco")
        ],
        "backend_note": TorchvisionFasterRCNN.license_note,
    }


def summary_sentence(resolved: Resolved) -> str:
    if resolved.source == SOURCE_CONSOLE:
        if resolved.allow:
            return "Downloading ready-made models is switched on, by an administrator in the console."
        return "Downloading ready-made models is switched off, by an administrator in the console."
    if resolved.source == SOURCE_ENVIRONMENT:
        state = "on" if resolved.allow else "off"
        return (
            f"Downloading ready-made models is switched {state} by the setting {TRAINING_DOWNLOAD_ENV} in the "
            "environment. No one has chosen in the console yet."
        )
    return "Downloading ready-made models is switched off, the built-in default. No one has chosen yet."


def describe(*, can_change: bool) -> dict[str, Any]:
    """Everything the Teach panel and the administrator's switch need, in plain language."""
    resolved = training_downloads()
    row = resolved.stored
    return {
        "allow": resolved.allow,
        "source": resolved.source,
        "stored": {"allow": row.value == ON, "changed_at": row.updated_at.isoformat()} if row is not None else None,
        "environment": {
            "variable": TRAINING_DOWNLOAD_ENV,
            "set": resolved.environment_value is not None,
            "allow": None if resolved.environment_value is None else resolved.environment_value.lower() in TRUE_WORDS,
        },
        "can_change": can_change,
        "summary": summary_sentence(resolved),
        "precedence": PRECEDENCE,
        "fetched_by": FETCHED_BY,
        "licence": licence_text(),
    }
