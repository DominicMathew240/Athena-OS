"""Bounded metadata preflight for one locally uploaded MP4.

No claim of complete decode, frame coverage, or model readiness is made here.
Only the forced MP4 demuxer and local file protocol are enabled. The ffprobe
executable is an optional deployment prerequisite; uploaded names are never used
as filesystem paths or command arguments.
"""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.files.storage import default_storage

MAX_VIDEO_BYTES = 50 * 1024 * 1024
MAX_SECONDS = 300
MAX_PIXELS = 1920 * 1080


class RecordingError(ValueError):
    def __init__(self, message, *, unavailable=False):
        super().__init__(message)
        self.unavailable = unavailable


def inspect_upload(upload):
    if upload.size <= 0 or upload.size > MAX_VIDEO_BYTES:
        raise RecordingError("Choose an MP4 recording under 50 MB.")
    directory = Path(settings.DATA_HOME) / "recording-preflight"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    digest = hashlib.sha256()
    count = 0
    try:
        with tempfile.NamedTemporaryFile(prefix="video-", suffix=".mp4", dir=directory) as temp:
            for chunk in upload.chunks():
                count += len(chunk)
                if count > MAX_VIDEO_BYTES:
                    raise RecordingError("Choose an MP4 recording under 50 MB.")
                digest.update(chunk)
                temp.write(chunk)
            temp.flush()
            temp.seek(0)
            header = temp.read(12)
            if count != upload.size or len(header) < 12 or header[4:8] != b"ftyp":
                raise RecordingError("The file is not a supported MP4 recording.")
            try:
                result = subprocess.run(
                    ["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe", "-f", "mov",
                     "-enable_drefs", "0", "-use_absolute_path", "0",
                     "-select_streams", "v:0", "-show_entries",
                     "stream=codec_name,width,height:stream_tags=rotate:stream_side_data=rotation:"
                     "format=duration:format_tags=creation_time",
                     "-of", "json", temp.name],
                    capture_output=True, check=True, timeout=15,
                )
                data = json.loads(result.stdout)
                stream = data["streams"][0]
                width, height = int(stream["width"]), int(stream["height"])
                duration = float(data["format"]["duration"])
                rotations = [stream.get("tags", {}).get("rotate", 0)] + [
                    item.get("rotation", 0) for item in stream.get("side_data_list", [])
                ]
                rotated = any(float(value) % 360 != 0 for value in rotations)
                captured_at = _parse_creation_time(data["format"].get("tags", {}).get("creation_time"))
            except FileNotFoundError as error:
                raise RecordingError("Video checking is unavailable. Ask an administrator to install ffprobe.", unavailable=True) from error
            except (subprocess.TimeoutExpired, subprocess.CalledProcessError, ValueError, TypeError, KeyError, IndexError, json.JSONDecodeError) as error:
                raise RecordingError("The MP4 could not be read. Choose a valid H.264 MP4.") from error
            if stream.get("codec_name") != "h264" or width < 1 or height < 1 or width > 1920 or height > 1080 or width * height > MAX_PIXELS:
                raise RecordingError("Choose an H.264 MP4 no larger than 1920×1080 pixels.")
            if not math.isfinite(duration) or duration <= 0 or duration > MAX_SECONDS:
                raise RecordingError("Choose a recording no longer than 5 minutes.")
            if rotated:
                raise RecordingError("Rotated video is not supported yet. Export it with rotation applied to the picture.")
            if not decode_ok(temp.name):
                raise RecordingError(
                    "This recording's pictures could not be decoded. Re-export it as a standard H.264 MP4 and try again."
                )
            return {
                "sha256": digest.hexdigest(), "size_bytes": count, "width": width, "height": height,
                "duration_seconds": duration, "captured_at": captured_at,
            }
    finally:
        upload.seek(0)


def _parse_creation_time(value):
    """MP4 creation_time tags are RFC 3339 strings in UTC; anything unparsable is simply absent."""
    from datetime import datetime

    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def decode_ok(path, *, timeout=60):
    """A real decode pass: demuxing is not enough, the pictures must decode.

    A container can carry valid metadata while its samples are corrupt; a
    recording like that would look imported and then fail at replay time. The
    full file is decoded once here so a broken import is reported at import,
    not later. No frames are kept — the output is discarded.
    """
    try:
        result = subprocess.run(
            ["ffmpeg", "-v", "error", "-xerror", "-nostdin", "-protocol_whitelist", "file",
             "-i", str(path), "-f", "null", "-"],
            capture_output=True, timeout=timeout,
        )
    except FileNotFoundError as error:
        raise RecordingError(
            "Video checking is unavailable. Ask an administrator to install ffmpeg.", unavailable=True
        ) from error
    except subprocess.TimeoutExpired as error:
        raise RecordingError("Checking this recording took too long. Try a shorter or smaller file.") from error
    return result.returncode == 0


def try_cleanup(cleanup_id, *, using="default"):
    """Attempt a queued deletion; leave its durable row for a future retry on failure."""
    import logging

    from ..models import OperationRecordingCleanup

    cleanup = OperationRecordingCleanup.objects.using(using).filter(pk=cleanup_id).first()
    if cleanup is None:
        return True
    try:
        default_storage.delete(cleanup.storage_key)
    except Exception:
        logging.getLogger(__name__).error("Could not remove a deleted recording from private storage")
        return False
    OperationRecordingCleanup.objects.using(using).filter(pk=cleanup_id).delete()
    return True


def queue_cleanup(storage_key, *, using="default"):
    """Persist a retryable storage deletion and try it after the transaction commits."""
    from django.db import transaction

    from ..models import OperationRecordingCleanup

    cleanup, _ = OperationRecordingCleanup.objects.using(using).get_or_create(storage_key=storage_key)
    transaction.on_commit(lambda: try_cleanup(cleanup.pk, using=using), using=using)


def delete_storage_file(sender, instance, using, **kwargs):
    """Queue cleanup when a recording is deleted, including via a cascade."""
    queue_cleanup(instance.storage_key, using=using)
