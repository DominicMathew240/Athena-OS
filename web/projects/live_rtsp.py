"""One supported live RTSP camera integration (T10).

An installer connects an approved RTSP/RTSPS URL, optionally with a stored
SourceCredential password, and Athena probes the stream with ffprobe for a
short bounded window. Failures are classified so network, credential, codec
and allow-list problems stay distinguishable. No continuous live execution
or event production lives here — that remains later work (T19+ on live).
"""
from __future__ import annotations

import base64
import json
import re
import subprocess
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from django.conf import settings

# Exact match only — same rule family as T11 destinations.
SUPPORTED_SCHEMES = ("rtsp", "rtsps")
SUPPORTED_VIDEO_CODECS = frozenset({"h264"})
# Bounded probe: enough to open the session and read one stream header.
PROBE_TIMEOUT_SECONDS = 12
FRAME_TIMEOUT_SECONDS = 15
MAX_URL_LENGTH = 512
# Hostnames / IPv4 literals only; no userinfo in the stored URL.
_HOST_RE = re.compile(
    r"^(?:"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"|(?:\d{1,3}\.){3}\d{1,3}"
    r")$",
    re.IGNORECASE,
)

# Operator-facing categories. Messages stay free of secrets and of the raw URL.
CATEGORY_INVALID_URL = "invalid_url"
CATEGORY_NOT_APPROVED = "not_approved"
CATEGORY_CREDENTIAL = "credential"
CATEGORY_NETWORK = "network"
CATEGORY_UNSUPPORTED_CODEC = "unsupported_codec"
CATEGORY_UNAVAILABLE = "unavailable"
CATEGORY_SOURCE_KIND = "source_kind"
CATEGORY_OK = "ok"

_CREDENTIAL_MARKERS = (
    "401",
    "403",
    "unauthorized",
    "authentication failed",
    "authorization failed",
    "wrong password",
    "invalid password",
    "access denied",
    "not authorized",
)
_NETWORK_MARKERS = (
    "connection refused",
    "connection timed out",
    "timed out",
    "timeout",
    "no route to host",
    "network is unreachable",
    "name or service not known",
    "temporary failure in name resolution",
    "could not connect",
    "failed to connect",
    "server returned 5",
    "input/output error",
    "end of file",
    "error number -111",  # ECONNREFUSED often surfaces this way
    "error number -110",  # ETIMEDOUT
    "error number -113",  # EHOSTUNREACH
)


class LiveRtspError(ValueError):
    """A refused or failed live RTSP step with a stable operator category."""

    def __init__(self, message: str, *, category: str):
        super().__init__(message)
        self.category = category


@dataclass(frozen=True)
class LiveProbeResult:
    """What a successful connect/probe learned about the stream."""

    scheme: str
    host: str
    port: int | None
    path: str
    codec_name: str
    width: int
    height: int
    transport: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "scheme": self.scheme,
            "host": self.host,
            "port": self.port,
            "path": self.path,
            "codec_name": self.codec_name,
            "width": self.width,
            "height": self.height,
            "transport": self.transport,
            "supported_codecs": sorted(SUPPORTED_VIDEO_CODECS),
        }


def _approved_rtsp_hosts() -> set[str]:
    """Exact hosts allowed for RTSP/RTSPS from settings (lowercased)."""
    hosts: set[str] = set()
    for entry in getattr(settings, "ATHENA_APPROVED_RTSP_HOSTS", ()) or ():
        text = str(entry).strip().lower()
        if not text:
            continue
        if "://" in text:
            parts = urlsplit(text)
            if parts.hostname:
                hosts.add(parts.hostname.strip().lower())
            continue
        hosts.add(text)
    return hosts


def normalize_rtsp_url(url: str) -> tuple[str, str, int | None, str]:
    """Return (scheme, host, port, path) for a bare RTSP URL without credentials.

    Userinfo in the URL is refused so secrets stay in SourceCredential, not in
    request bodies or process listings of the URL string itself.
    """
    if not isinstance(url, str) or not url.strip():
        raise LiveRtspError("Provide an RTSP URL for this camera.", category=CATEGORY_INVALID_URL)
    raw = url.strip()
    if len(raw) > MAX_URL_LENGTH:
        raise LiveRtspError("That RTSP URL is too long.", category=CATEGORY_INVALID_URL)
    try:
        parts = urlsplit(raw)
    except ValueError as error:
        raise LiveRtspError("That is not a valid RTSP URL.", category=CATEGORY_INVALID_URL) from error
    scheme = (parts.scheme or "").lower()
    if scheme not in SUPPORTED_SCHEMES:
        raise LiveRtspError(
            "Only rtsp:// and rtsps:// camera URLs are supported.",
            category=CATEGORY_INVALID_URL,
        )
    if parts.username or parts.password or "@" in (parts.netloc or ""):
        raise LiveRtspError(
            "Do not put a username or password in the URL. Store the password as the source credential.",
            category=CATEGORY_INVALID_URL,
        )
    host = (parts.hostname or "").strip().lower()
    if not host or not _HOST_RE.match(host):
        raise LiveRtspError("The camera host in that URL is not valid.", category=CATEGORY_INVALID_URL)
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    return scheme, host, parts.port, path


def check_rtsp_host(host: str) -> str:
    """Return the approved host or raise with category not_approved."""
    approved = _approved_rtsp_hosts()
    key = host.strip().lower()
    if not approved:
        raise LiveRtspError(
            "No RTSP camera hosts are approved on this server. Ask the operator to set ATHENA_APPROVED_RTSP_HOSTS.",
            category=CATEGORY_NOT_APPROVED,
        )
    if key not in approved:
        raise LiveRtspError(
            "That camera host is not on the approved RTSP list for this server.",
            category=CATEGORY_NOT_APPROVED,
        )
    return key


def build_authenticated_url(
    *,
    scheme: str,
    host: str,
    port: int | None,
    path: str,
    username: str | None,
    password: str | None,
) -> str:
    """Build the one-shot URL passed to ffprobe. Never log the return value."""
    netloc = host if port is None else f"{host}:{port}"
    user = (username or "").strip()
    secret = password if password is not None else ""
    if user or secret:
        # quote keeps special characters out of the authority grammar.
        auth = f"{quote(user, safe='')}:{quote(secret, safe='')}"
        netloc = f"{auth}@{netloc}"
    return urlunsplit((scheme, netloc, path or "/", "", ""))


def _classify_ffprobe_failure(stderr: str, *, had_password: bool) -> tuple[str, str]:
    """Map ffprobe stderr to (category, plain message). Never echo stderr."""
    text = (stderr or "").lower()
    if any(marker in text for marker in _CREDENTIAL_MARKERS):
        return (
            CATEGORY_CREDENTIAL,
            "The camera refused the username or password. Check the source credential and try again.",
        )
    if any(marker in text for marker in _NETWORK_MARKERS):
        return (
            CATEGORY_NETWORK,
            "Athena could not reach the camera on the network. Check placement, firewall rules and that the camera is online.",
        )
    if had_password and ("401" in text or "403" in text or "auth" in text):
        return (
            CATEGORY_CREDENTIAL,
            "The camera refused the username or password. Check the source credential and try again.",
        )
    # Ambiguous open failure with a password set is still more often network than codec.
    return (
        CATEGORY_NETWORK,
        "The camera stream could not be opened. Check the URL, network path and credentials, then try again.",
    )


def _run_ffprobe(url: str) -> dict[str, Any]:
    """Probe stream headers only. ``url`` may contain credentials — never log it."""
    command = [
        "ffprobe",
        "-v",
        "error",
        "-rtsp_transport",
        "tcp",
        "-timeout",
        str(PROBE_TIMEOUT_SECONDS * 1_000_000),  # microseconds for RTSP
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_name,width,height",
        "-of",
        "json",
        url,
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            timeout=PROBE_TIMEOUT_SECONDS + 2,
            check=False,
        )
    except FileNotFoundError as error:
        raise LiveRtspError(
            "Live camera checking is unavailable. Ask an administrator to install ffprobe.",
            category=CATEGORY_UNAVAILABLE,
        ) from error
    except subprocess.TimeoutExpired as error:
        raise LiveRtspError(
            "The camera did not answer in time. Check the network path to the camera.",
            category=CATEGORY_NETWORK,
        ) from error
    if completed.returncode != 0:
        category, message = _classify_ffprobe_failure(
            completed.stderr.decode("utf-8", errors="replace"),
            had_password="@" in url.split("://", 1)[-1].split("/", 1)[0],
        )
        raise LiveRtspError(message, category=category)
    try:
        payload = json.loads(completed.stdout.decode("utf-8"))
        stream = payload["streams"][0]
        return {
            "codec_name": str(stream["codec_name"]).lower(),
            "width": int(stream["width"]),
            "height": int(stream["height"]),
        }
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise LiveRtspError(
            "The camera answered but did not report a usable video stream.",
            category=CATEGORY_NETWORK,
        ) from error


def _grab_preview_jpeg(url: str) -> bytes:
    """Grab a single JPEG frame for installer preview. Never log ``url``."""
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-rtsp_transport",
        "tcp",
        "-timeout",
        str(FRAME_TIMEOUT_SECONDS * 1_000_000),
        "-i",
        url,
        "-frames:v",
        "1",
        "-f",
        "image2pipe",
        "-vcodec",
        "mjpeg",
        "pipe:1",
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            timeout=FRAME_TIMEOUT_SECONDS + 2,
            check=False,
        )
    except FileNotFoundError as error:
        raise LiveRtspError(
            "Live camera preview is unavailable. Ask an administrator to install ffmpeg.",
            category=CATEGORY_UNAVAILABLE,
        ) from error
    except subprocess.TimeoutExpired as error:
        raise LiveRtspError(
            "The camera did not deliver a preview frame in time.",
            category=CATEGORY_NETWORK,
        ) from error
    if completed.returncode != 0 or not completed.stdout:
        category, message = _classify_ffprobe_failure(
            completed.stderr.decode("utf-8", errors="replace"),
            had_password="@" in url.split("://", 1)[-1].split("/", 1)[0],
        )
        # Prefer a preview-specific network wording when classification is generic network.
        if category == CATEGORY_NETWORK:
            message = "The camera stream opened for checking but a preview frame could not be captured."
        raise LiveRtspError(message, category=category)
    # Basic JPEG SOI check so we never return garbage as an image.
    if completed.stdout[:2] != b"\xff\xd8":
        raise LiveRtspError(
            "The camera returned data that is not a picture preview.",
            category=CATEGORY_NETWORK,
        )
    return completed.stdout


def probe_rtsp(
    *,
    url: str,
    username: str | None = None,
    password: str | None = None,
    include_preview: bool = True,
) -> dict[str, Any]:
    """Validate allow-list, probe codec/size, optionally return a JPEG preview.

    Returns a JSON-ready dict. Raises :class:`LiveRtspError` with ``category``.
    """
    scheme, host, port, path = normalize_rtsp_url(url)
    check_rtsp_host(host)
    auth_url = build_authenticated_url(
        scheme=scheme,
        host=host,
        port=port,
        path=path,
        username=username,
        password=password,
    )
    try:
        meta = _run_ffprobe(auth_url)
    finally:
        # Drop the local reference promptly; do not keep auth URLs around.
        del auth_url

    codec = meta["codec_name"]
    width = meta["width"]
    height = meta["height"]
    if codec not in SUPPORTED_VIDEO_CODECS:
        raise LiveRtspError(
            f"This camera sends {codec or 'an unknown codec'}, but only H.264 is supported for live preview.",
            category=CATEGORY_UNSUPPORTED_CODEC,
        )
    if width < 1 or height < 1 or width > 1920 or height > 1080:
        raise LiveRtspError(
            "Live preview supports streams up to 1920×1080 pixels. Reconfigure the camera or use a sub-stream.",
            category=CATEGORY_UNSUPPORTED_CODEC,
        )

    result = LiveProbeResult(
        scheme=scheme,
        host=host,
        port=port,
        path=path,
        codec_name=codec,
        width=width,
        height=height,
        transport="tcp",
    )
    payload: dict[str, Any] = {
        "ok": True,
        "category": CATEGORY_OK,
        "message": "Camera connected. Live preview is ready.",
        "stream": result.as_dict(),
        "preview_jpeg_base64": None,
    }
    if include_preview:
        auth_url = build_authenticated_url(
            scheme=scheme,
            host=host,
            port=port,
            path=path,
            username=username,
            password=password,
        )
        try:
            frame = _grab_preview_jpeg(auth_url)
            payload["preview_jpeg_base64"] = base64.b64encode(frame).decode("ascii")
        finally:
            del auth_url
    return payload


def supported_live_rtsp_summary() -> dict[str, Any]:
    """Public, secret-free description of what this build supports."""
    return {
        "schemes": list(SUPPORTED_SCHEMES),
        "video_codecs": sorted(SUPPORTED_VIDEO_CODECS),
        "transport": "tcp",
        "max_resolution": {"width": 1920, "height": 1080},
        "approved_host_count": len(_approved_rtsp_hosts()),
        "network_placement": (
            "Cameras must be reachable from the Athena host on the private or site network. "
            "Only hosts listed in ATHENA_APPROVED_RTSP_HOSTS may be contacted. "
            "Prefer a dedicated camera VLAN; do not expose RTSP to the public internet."
        ),
        "credentials": (
            "Put the camera password in the source credential (T11). "
            "Do not embed userinfo in the RTSP URL. Username may be sent with the preview request."
        ),
    }
