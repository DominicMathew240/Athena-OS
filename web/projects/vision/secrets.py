"""Encrypt a person's API key at rest with a key derived from DJANGO_SECRET_KEY.

If the secret key changes, old values can no longer be read; the person is asked to enter their key again.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _fernet() -> Fernet:
    digest = hashlib.sha256(f"athena-ai-connection:{settings.SECRET_KEY}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode() if value else ""


def decrypt(token: str) -> str | None:
    """The stored key, ``""`` when none was stored, ``None`` when it can no longer be read."""
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return None


def hint(value: str) -> str:
    return value[-4:] if len(value) >= 8 else ""
