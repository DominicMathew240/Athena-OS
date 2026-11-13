"""Talk to any OpenAI-compatible vision-language model (Ollama, vLLM, LM Studio, OpenAI, OpenRouter, ...).

Only ``/chat/completions`` (and ``/models`` for a quick reachability check) are used, so the built-in Qwen
model on your own server and a person's own endpoint go through exactly the same code.
"""

from __future__ import annotations

import base64
import io
import json
import re
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from PIL import Image, ImageOps

MAX_SIDE = 1008  # Qwen-VL works on 28-pixel patches; a multiple of 28 keeps its box coordinates on our grid
PATCH = 28
SEVERITIES = ("info", "low", "medium", "high", "critical")
VERDICTS = ("clear", "attention", "critical")


class VisionError(Exception):
    """A failure explained in words a non-specialist can act on."""

    def __init__(self, message: str, kind: str = "error") -> None:
        super().__init__(message)
        self.message = message
        self.kind = kind


@dataclass(frozen=True)
class Connection:
    source: str  # "builtin" or "own"
    label: str
    base_url: str
    model: str
    api_key: str = ""

    @property
    def host(self) -> str:
        return urlparse(self.base_url).netloc or self.base_url

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.model)


def normalise_base_url(value: str) -> str:
    url = (value or "").strip().rstrip("/")
    for suffix in ("/chat/completions", "/completions"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
    return url


def validate_base_url(value: str) -> str:
    url = normalise_base_url(value)
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise VisionError("Enter the full address, starting with http:// or https://, for example https://api.openai.com/v1.", "invalid")
    if len(url) > 300:
        raise VisionError("That address is too long.", "invalid")
    return url


def _headers(conn: Connection) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if conn.api_key:
        headers["Authorization"] = f"Bearer {conn.api_key}"
    return headers


def _explain_http(conn: Connection, response: httpx.Response) -> VisionError:
    detail = ""
    try:
        body = response.json()
        error = body.get("error") if isinstance(body, dict) else None
        detail = (error.get("message") if isinstance(error, dict) else error) or body.get("message") or ""
    except (ValueError, AttributeError):
        detail = response.text[:200]
    detail = str(detail)[:240]
    code = response.status_code
    if code in (401, 403):
        return VisionError(f"The AI service at {conn.host} refused the API key. Check the key on the AI model page.", "auth")
    if code == 404:
        return VisionError(
            f"The AI service at {conn.host} does not know the model “{conn.model}”, or the address is wrong. "
            "Check the model name and that the address ends in /v1.", "not_found")
    if code == 429:
        return VisionError(f"The AI service at {conn.host} says it is too busy or your quota is used up. Try again in a minute.", "rate_limit")
    if code == 413:
        return VisionError("The pictures were too large for this AI service. Try fewer pictures.", "too_large")
    message = f"The AI service at {conn.host} answered with an error ({code})."
    return VisionError(f"{message} {detail}".strip(), "http")


def chat(conn: Connection, messages: list, *, max_tokens: int, timeout: float, json_mode: bool = True) -> str:
    if not conn.configured:
        raise VisionError("No AI model is set up yet. Open the AI model page to connect one.", "not_configured")
    payload = {"model": conn.model, "messages": messages, "temperature": 0.2, "max_tokens": max_tokens}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=10.0)) as client:
            response = client.post(f"{conn.base_url}/chat/completions", headers=_headers(conn), json=payload)
            if json_mode and response.status_code in (400, 422) and "response_format" in response.text:
                payload.pop("response_format")  # some servers do not support JSON mode; the prompt asks for JSON anyway
                response = client.post(f"{conn.base_url}/chat/completions", headers=_headers(conn), json=payload)
    except httpx.TimeoutException as error:
        raise VisionError(
            f"The AI at {conn.host} took longer than {int(timeout)} seconds to answer. A busy or slow server can do "
            "this; try again, or try fewer pictures.", "timeout") from error
    except httpx.HTTPError as error:
        raise VisionError(
            f"Athena could not reach the AI at {conn.host}. Check the address on the AI model page, or ask your "
            "administrator whether the Qwen server is running.", "unreachable") from error
    if response.status_code >= 400:
        raise _explain_http(conn, response)
    try:
        body = response.json()
        content = body["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as error:
        raise VisionError(f"The AI service at {conn.host} sent an answer Athena could not read.", "bad_response") from error
    if isinstance(content, list):  # some servers return content parts
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return str(content or "")


def test_connection(conn: Connection) -> dict:
    started = time.monotonic()
    reply = chat(
        conn,
        [{"role": "user", "content": "Reply with the single word: ready"}],
        max_tokens=10,
        timeout=45,
        json_mode=False,
    )
    return {
        "ok": True,
        "latency_ms": int((time.monotonic() - started) * 1000),
        "reply": reply.strip()[:60],
        "message": f"Connected to {conn.model} at {conn.host}.",
    }


def ping(conn: Connection, timeout: float = 3.0) -> bool:
    if not conn.configured:
        return False
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(f"{conn.base_url}/models", headers=_headers(conn))
        return response.status_code < 500 and response.status_code not in (401, 403)
    except httpx.HTTPError:
        return False


# --- pictures ------------------------------------------------------------------------------------------

def _round_to_patch(value: float) -> int:
    return max(PATCH * 2, int(round(value / PATCH)) * PATCH)


def prepare_image(raw: bytes) -> tuple[str, int, int]:
    """A JPEG data URL sized for the model, and the width and height the model will see."""
    with Image.open(io.BytesIO(raw)) as image:
        image = ImageOps.exif_transpose(image).convert("RGB")
        width, height = image.size
        scale = min(1.0, MAX_SIDE / max(width, height))
        target = (_round_to_patch(width * scale), _round_to_patch(height * scale))
        if target != image.size:
            image = image.resize(target, Image.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85)
    encoded = base64.b64encode(buffer.getvalue()).decode()
    return f"data:image/jpeg;base64,{encoded}", target[0], target[1]


# --- the question --------------------------------------------------------------------------------------

SYSTEM_PROMPT = """You are Athena, a careful visual inspection assistant for operations, facility and industrial teams.
The people reading your answer are supervisors and field staff, not computer-vision experts.
Look closely at the pictures and answer in plain, friendly English. Explain the context: what is happening,
why it matters, and what to do next. Never invent things you cannot see; when unsure, say so and lower the confidence.
Never identify people; describe them only by position and clothing.

Reply with ONLY one JSON object (no markdown, no text before or after) in exactly this shape:
{
  "verdict": "clear" | "attention" | "critical",
  "headline": "one short sentence that directly answers the question",
  "summary": "2 to 4 plain sentences with the context behind the answer",
  "scene": "one or two sentences describing the place, equipment and conditions (lighting, angle, weather)",
  "counts": [{"label": "short name", "value": 0, "note": "optional short note"}],
  "findings": [{"title": "short name", "detail": "what it is and where, in plain words", "severity": "info" | "low" | "medium" | "high" | "critical", "confidence": 0.0, "image": 1, "box": [x1, y1, x2, y2]}],
  "actions": ["a practical next step", "..."],
  "limits": "what you could not see clearly or are unsure about"
}
Rules: "value" is a whole number. "confidence" is between 0 and 1. "image" is the picture number the finding is in.
"box" is the area of the finding in that picture as pixel coordinates [left, top, right, bottom] using the picture size
given below, or null when it is not one place. List the most important findings first, at most 12.
If there is nothing wrong, "findings" may be empty and the verdict is "clear"."""


def build_messages(template: dict, instructions: str, pictures: list[dict], source: str) -> list:
    """``pictures``: dicts with ``data_url``, ``width``, ``height`` and optional ``frame_time``."""
    lines = [f"Inspection type: {template['name']}."]
    question = template.get("question") or ""
    if question:
        lines.append(f"Question: {question}")
    if instructions:
        label = "Question" if not question else "Extra instructions from the person"
        lines.append(f"{label}: {instructions}")
    lines.append(f"What to look for: {template['focus']}")
    if template.get("counts"):
        lines.append("Report these counts (add others if useful): " + ", ".join(template["counts"]) + ".")
    if source == "video" and len(pictures) > 1:
        times = ", ".join(
            f"picture {index} at {picture['frame_time']:.1f}s" for index, picture in enumerate(pictures, 1)
            if picture.get("frame_time") is not None
        )
        lines.append(
            f"The {len(pictures)} pictures are frames from one video, in time order ({times}). Use the changes between "
            "frames to tell movement and direction, and count each real-world thing once."
        )
    elif len(pictures) > 1:
        lines.append(f"There are {len(pictures)} pictures of the same inspection. Combine what you see in all of them.")
    sizes = "; ".join(f"picture {i} is {p['width']}x{p['height']} pixels" for i, p in enumerate(pictures, 1))
    lines.append(f"Picture sizes: {sizes}.")
    content: list = [{"type": "text", "text": "\n".join(lines)}]
    for picture in pictures:
        content.append({"type": "image_url", "image_url": {"url": picture["data_url"]}})
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": content}]


# --- the answer ----------------------------------------------------------------------------------------

def _extract_json(text: str) -> dict:
    cleaned = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", cleaned, flags=re.S)
    if fenced:
        cleaned = fenced.group(1)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise VisionError("The AI answered, but not in the report format Athena needs. Try again, or try another model.", "bad_response")
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as error:
        raise VisionError("The AI answered, but its report was incomplete. Try again, or try another model.", "bad_response") from error
    if not isinstance(data, dict):
        raise VisionError("The AI answered, but not in the report format Athena needs.", "bad_response")
    return data


def _text(value, limit: int) -> str:
    return str(value).strip()[:limit] if isinstance(value, (str, int, float)) and str(value).strip() else ""


def _number(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = re.search(r"-?\d+(\.\d+)?", value)
        return float(match.group()) if match else None
    return None


def _box(raw, width: int, height: int) -> list[float] | None:
    """[x, y, w, h] as fractions of the picture, or None."""
    if isinstance(raw, dict):
        raw = [raw.get(k) for k in ("x1", "y1", "x2", "y2")]
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    values = [_number(v) for v in raw]
    if any(v is None for v in values):
        return None
    x1, y1, x2, y2 = values
    biggest = max(values)
    if biggest <= 1.0:  # already fractions
        sx, sy = 1.0, 1.0
    elif (x2 > width or y2 > height) and biggest <= 1000:  # a 0-1000 grid
        sx, sy = 1000.0, 1000.0
    else:  # pixels
        sx, sy = float(width), float(height)
    left, right = sorted((x1 / sx, x2 / sx))
    top, bottom = sorted((y1 / sy, y2 / sy))
    left, top = max(0.0, min(1.0, left)), max(0.0, min(1.0, top))
    right, bottom = max(0.0, min(1.0, right)), max(0.0, min(1.0, bottom))
    if right - left < 0.005 or bottom - top < 0.005:
        return None
    return [round(left, 4), round(top, 4), round(right - left, 4), round(bottom - top, 4)]


def parse_result(text: str, sizes: list[tuple[int, int]]) -> dict:
    data = _extract_json(text)
    counts = []
    for item in data.get("counts") or []:
        if not isinstance(item, dict):
            continue
        label, value = _text(item.get("label"), 60), _number(item.get("value"))
        if label and value is not None:
            counts.append({"label": label, "value": int(round(value)), "note": _text(item.get("note"), 160)})
    findings = []
    for item in (data.get("findings") or [])[:12]:
        if not isinstance(item, dict):
            continue
        title = _text(item.get("title") or item.get("label"), 80)
        if not title:
            continue
        severity = _text(item.get("severity"), 12).lower()
        severity = severity if severity in SEVERITIES else "medium"
        confidence = _number(item.get("confidence"))
        if confidence is not None and confidence > 1:
            confidence = confidence / 100
        image = _number(item.get("image"))
        image_index = int(image) if image and 1 <= int(image) <= len(sizes) else 1
        width, height = sizes[image_index - 1] if sizes else (1, 1)
        findings.append({
            "title": title,
            "detail": _text(item.get("detail") or item.get("description"), 600),
            "severity": severity,
            "confidence": round(max(0.0, min(1.0, confidence)), 2) if confidence is not None else None,
            "image": image_index,
            "box": _box(item.get("box") or item.get("bbox") or item.get("box_2d"), width, height),
        })
    verdict = _text(data.get("verdict"), 12).lower()
    if verdict not in VERDICTS:
        worst = {f["severity"] for f in findings}
        verdict = "critical" if worst & {"critical", "high"} else "attention" if worst & {"medium", "low"} else "clear"
    actions = [_text(a, 300) for a in (data.get("actions") or data.get("recommendations") or []) if _text(a, 300)][:8]
    headline = _text(data.get("headline"), 240) or _text(data.get("summary"), 240) or "Athena finished looking at your pictures."
    return {
        "verdict": verdict,
        "headline": headline,
        "summary": _text(data.get("summary"), 1200),
        "scene": _text(data.get("scene"), 600),
        "counts": counts[:8],
        "findings": findings,
        "actions": actions,
        "limits": _text(data.get("limits"), 600),
    }
