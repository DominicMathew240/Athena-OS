"""Encrypted-at-rest credential storage for operation sources (T11).

Secrets live in a dedicated key file under the private data home — never in the
repository and never derived from ``SECRET_KEY`` — and are stored Fernet-encrypted
with an AES128-CBC + HMAC construction. Only a hint of the secret is ever shown
back; the plaintext leaves this module solely through :func:`reveal`, which
existing callers must invoke from a guarded, owner-checked code path.

The key file is generated on first use with mode 0600. Rotating the file makes
existing credentials unreadable (``reveal`` returns ``None``); owners are asked
to re-enter them. This is the same failure behaviour as ``projects.vision.secrets``.
"""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

logger = logging.getLogger(__name__)

KEY_FILE_MODE = 0o600
_MAX_SECRET_LENGTH = 2000


def _key_path() -> Path:
    return Path(settings.DATA_HOME) / "operation-credentials.key"


def _load_or_create_key() -> bytes:
    path = _key_path()
    try:
        key = path.read_bytes()
    except FileNotFoundError:
        key = Fernet.generate_key()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Create with 0600; O_EXCL keeps a concurrent first use from clobbering.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, KEY_FILE_MODE)
        try:
            os.write(fd, key)
        finally:
            os.close(fd)
        logger.info("operation credential key created at %s", "operation-credentials.key")
        return key
    # A key file readable by others is a misconfiguration: refuse rather than use it.
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise PermissionError(
            "The operation credential key file permissions are too open; "
            f"run chmod 600 {_key_path()} and try again."
        )
    return key


_fernet: Fernet | None = None


def _fernet_instance() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_load_or_create_key())
    return _fernet


def encrypt(value: str) -> str:
    """Encrypt a secret for storage; empty secrets encrypt to ``""``."""
    if not value:
        return ""
    if len(value) > _MAX_SECRET_LENGTH:
        raise ValueError("The secret is too long to store.")
    return _fernet_instance().encrypt(value.encode()).decode()


def reveal(token: str) -> str | None:
    """The stored secret; ``""`` when none was stored, ``None`` when the key no longer matches."""
    if not token:
        return ""
    try:
        return _fernet_instance().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        logger.warning("an operation credential could not be decrypted; the key file may have been rotated")
        return None


def hint(value: str) -> str:
    """A non-secret hint (last four characters) safe to show to the owner."""
    return f"…{value[-4:]}" if len(value) >= 8 else ""
