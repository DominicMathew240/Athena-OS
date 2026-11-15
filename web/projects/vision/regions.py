"""Marked areas an industry person draws on a picture (parking bays, a pipe, a shelf)."""

from __future__ import annotations

import json
import re

MAX_REGIONS = 24
MIN_SIZE = 0.005
MAX_RAW_BYTES = 8192
SAFE_LABEL = re.compile(r"^[\w ./-]{1,40}$", re.UNICODE)


def parse_regions(raw) -> list[dict]:
    """Read boxes as fractions of the picture. Empty input means no marks."""
    if raw in (None, "", b"", [], ()):
        return []
    if isinstance(raw, (bytes, bytearray)):
        if len(raw) > MAX_RAW_BYTES:
            raise ValueError("Too many marked areas.")
        raw = raw.decode("utf-8")
    if isinstance(raw, str):
        if len(raw.encode("utf-8")) > MAX_RAW_BYTES:
            raise ValueError("Too many marked areas.")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ValueError("The marked areas were not valid JSON.") from error
    else:
        payload = raw
    if not isinstance(payload, list):
        raise ValueError("Marked areas must be a list of boxes.")
    if len(payload) > MAX_REGIONS:
        raise ValueError(f"Mark no more than {MAX_REGIONS} areas.")
    marks = []
    for index, item in enumerate(payload, 1):
        if not isinstance(item, dict):
            raise ValueError("Each marked area must be a box.")
        try:
            x, y, w, h = float(item["x"]), float(item["y"]), float(item["w"]), float(item["h"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("Each box needs x, y, w and h as numbers between 0 and 1.") from error
        if w < MIN_SIZE or h < MIN_SIZE or x < 0 or y < 0 or x + w > 1.0001 or y + h > 1.0001:
            raise ValueError("Each box must sit inside the picture.")
        image = item.get("image", 1)
        try:
            image = int(image)
        except (TypeError, ValueError) as error:
            raise ValueError("The picture number for a box must be a whole number.") from error
        if image < 1:
            raise ValueError("The picture number for a box must be 1 or more.")
        label = str(item.get("label") or index).strip()[:40] or str(index)
        if not SAFE_LABEL.fullmatch(label):
            raise ValueError("Area names can only use letters, numbers, spaces, dots, slashes and hyphens.")
        marks.append({
            "x": round(x, 4),
            "y": round(y, 4),
            "w": round(w, 4),
            "h": round(h, 4),
            "label": label,
            "image": image,
        })
    return marks


def prompt_lines(marks: list[dict], template_key: str) -> str:
    if not marks:
        return ""
    if template_key == "parking":
        lead = (
            "The person marked these areas on the picture as parking bays "
            "(left, top, width, height as fractions of the picture, 0 to 1). "
            "Treat each box as one bay. Count free and taken using these bays. If a bay is empty, say so."
        )
    else:
        lead = (
            "The person marked these areas on the picture "
            "(left, top, width, height as fractions of the picture, 0 to 1). "
            "Treat each box as one place to inspect. If a marked area is empty, say so."
        )
    rows = [
        f"{index}. {mark['label']}: {mark['x']:.2f}, {mark['y']:.2f}, {mark['w']:.2f}, {mark['h']:.2f} "
        f"(picture {mark['image']})"
        for index, mark in enumerate(marks, 1)
    ]
    return lead + "\n" + "\n".join(rows)
