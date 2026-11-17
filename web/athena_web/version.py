"""The console's software version, from one source of truth (``settings.RELEASE_VERSION``).

A tiny module of its own so every surface (API, health probe, footer) imports the same
function instead of reaching into settings internals.
"""

from __future__ import annotations

import os


def current() -> str:
    """The version string. ``ATHENA_VERSION`` may only add a packaging suffix on top of
    the released version; it can never mask or replace it."""
    from django.conf import settings

    override = os.environ.get("ATHENA_VERSION", "").strip()
    if override and override.startswith(settings.RELEASE_VERSION):
        return override
    return settings.RELEASE_VERSION
