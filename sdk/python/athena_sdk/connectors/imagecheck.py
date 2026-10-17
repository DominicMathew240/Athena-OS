# SPDX-License-Identifier: Apache-2.0
"""Cheap, dependency-free structural checks for JPEG / PNG / WebP bytes.

These catch the obvious problems at the edge (wrong type, a file still being written).
They are *not* a decoder: the console re-validates every image with a real decoder, and
its verdict is final.
"""

from __future__ import annotations

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
PNG_IEND = b"\x00\x00\x00\x00IEND\xaeB`\x82"
EXTENSIONS = {
    "image/jpeg": (".jpg", ".jpeg"),
    "image/png": (".png",),
    "image/webp": (".webp",),
}


def sniff(payload: bytes) -> str | None:
    """Content type from magic bytes, or ``None``."""
    if payload[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if payload[:8] == PNG_MAGIC:
        return "image/png"
    if payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "image/webp"
    return None


def truncation_problem(payload: bytes, content_type: str) -> str | None:
    """A reason the bytes look like an incomplete file, else ``None``."""
    if content_type == "image/png":
        if not payload.endswith(PNG_IEND):
            return "PNG has no IEND trailer (file looks incomplete)"
    elif content_type == "image/jpeg":
        if not payload.rstrip(b"\x00").endswith(b"\xff\xd9"):
            return "JPEG has no end-of-image marker (file looks incomplete)"
    elif content_type == "image/webp":
        declared = int.from_bytes(payload[4:8], "little") + 8
        if len(payload) < declared:
            return "WebP is shorter than its RIFF header declares (file looks incomplete)"
    return None


def extension_matches(name: str, content_type: str) -> bool:
    return name.lower().endswith(EXTENSIONS.get(content_type, ()))
