"""Durable recorded-video imports for one operation source (T09).

Source recordings are durable source material: whole files kept privately with
checksum, dimensions, duration, capture time and ingestion time. A file that
cannot decode is refused at import — it must not look like a stored, healthy
recording. Nothing here substitutes sampled stills for the video.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings

from .models import OperationSourceRecording
from .tests import grant

REQUIRE_FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")


def make_clip(directory, *, seconds=1, creation_time=None):
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=blue:s=64x48:r=5",
        "-t", str(seconds), "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
    ]
    if creation_time:
        command += ["-metadata", f"creation_time={creation_time}"]
    path = Path(directory) / "clip.mp4"
    subprocess.run(command + ["-y", str(path)], check=True, capture_output=True, timeout=30)
    data = path.read_bytes()
    path.unlink()
    return data


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="source-recording-test-"))
@skipUnless(REQUIRE_FFMPEG, "ffmpeg and ffprobe required for source-recording tests")
class SourceRecordingApiTests(TestCase):
    def setUp(self):
        import shutil

        # Files survive DB rollback between tests; start every test with empty storage.
        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)
        Path(settings.MEDIA_ROOT).mkdir(parents=True, exist_ok=True)
        self.owner = get_user_model().objects.create_user("source-owner")
        grant(self.owner, "labeler")
        self.client.force_login(self.owner)
        site = self.client.post(
            "/api/operation-sites",
            '{"name": "Kuala Lumpur shop", "timezone_name": "Asia/Kuala_Lumpur"}',
            content_type="application/json",
        ).json()["site"]
        self.source = self.client.post(
            "/api/operation-sources",
            f'{{"site_id": "{site["id"]}", "name": "Front entrance", "kind": "recorded_video"}}',
            content_type="application/json",
        ).json()["source"]
        self.source_id = self.source["id"]
        self.tmp = tempfile.mkdtemp(prefix="source-recording-fixture-")
        self.video = make_clip(self.tmp, creation_time="2026-11-01T09:30:00Z")

    def test_import_preserves_the_whole_file_with_both_clocks(self):
        import hashlib

        response = self.client.post(
            f"/api/operation-sources/{self.source_id}/recordings",
            {"video": SimpleUploadedFile("entrance.mp4", self.video, content_type="video/mp4")},
        )
        self.assertEqual(response.status_code, 201, response.content)
        recording = response.json()["recording"]
        self.assertEqual(recording["sha256"], hashlib.sha256(self.video).hexdigest())
        self.assertEqual(recording["size_bytes"], len(self.video))
        self.assertEqual(recording["width"], 64)
        self.assertEqual(recording["height"], 48)
        self.assertEqual(recording["duration_seconds"], 1.0)
        # The recording's own clock and Athena's ingestion clock are both present.
        self.assertEqual(recording["captured_at"], "2026-11-01T09:30:00+00:00")
        self.assertIsNotNone(recording["ingested_at"])
        # The stored file is the whole recording, byte for byte.
        from django.core.files.storage import default_storage

        with default_storage.open(
            OperationSourceRecording.objects.get(id=recording["id"]).storage_key, "rb"
        ) as stored:
            self.assertEqual(stored.read(), self.video)
        # The listing shows the same import.
        listed = self.client.get(f"/api/operation-sources/{self.source_id}/recordings").json()["recordings"]
        self.assertEqual([row["id"] for row in listed], [recording["id"]])

    def test_a_container_without_creation_time_imports_with_null_capture_time(self):
        plain = make_clip(self.tmp)  # no creation_time metadata
        response = self.client.post(
            f"/api/operation-sources/{self.source_id}/recordings",
            {"video": SimpleUploadedFile("plain.mp4", plain, content_type="video/mp4")},
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertIsNone(response.json()["recording"]["captured_at"])

    def test_a_undecodable_recording_is_refused_and_never_stored(self):
        # Valid MP4 header and ffprobe-readable metadata, but the sample data is
        # corrupted: the decode pass must refuse it and nothing may be stored.
        broken = bytearray(self.video)
        for offset in range(len(broken) // 2, len(broken) - 1):
            broken[offset] = broken[offset] ^ 0xFF
        response = self.client.post(
            f"/api/operation-sources/{self.source_id}/recordings",
            {"video": SimpleUploadedFile("broken.mp4", bytes(broken), content_type="video/mp4")},
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("could not be decoded", response.json()["error"])
        self.assertEqual(OperationSourceRecording.objects.count(), 0)
        self.assertEqual(list(Path(settings.MEDIA_ROOT).rglob("*.mp4")), [])

    def test_bounds_are_enforced_with_plain_messages(self):
        for name, data, message in (
            ("not-an-mp4.mp4", b"not a video at all" * 10, "not a supported MP4"),
            ("empty.mp4", b"", "under 50 MB"),
        ):
            with self.subTest(upload=name):
                response = self.client.post(
                    f"/api/operation-sources/{self.source_id}/recordings",
                    {"video": SimpleUploadedFile(name, data, content_type="video/mp4")},
                )
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn(message, response.json()["error"])
        self.assertEqual(OperationSourceRecording.objects.count(), 0)

    def test_two_files_at_once_is_refused(self):
        upload = SimpleUploadedFile("a.mp4", self.video, content_type="video/mp4")
        response = self.client.post(
            f"/api/operation-sources/{self.source_id}/recordings",
            {"video": upload, "video2": upload},
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("one MP4", response.json()["error"])

    def test_only_recorded_video_sources_can_hold_recordings(self):
        site = self.client.get("/api/operation-sites").json()["sites"][0]
        camera = self.client.post(
            "/api/operation-sources",
            f'{{"site_id": "{site["id"]}", "name": "Lobby camera", "kind": "camera"}}',
            content_type="application/json",
        ).json()["source"]
        response = self.client.post(
            f"/api/operation-sources/{camera['id']}/recordings",
            {"video": SimpleUploadedFile("clip.mp4", self.video, content_type="video/mp4")},
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("recorded-video", response.json()["error"])

    def test_source_recordings_are_private_to_the_owner(self):
        response = self.client.post(
            f"/api/operation-sources/{self.source_id}/recordings",
            {"video": SimpleUploadedFile("clip.mp4", self.video, content_type="video/mp4")},
        )
        recording = response.json()["recording"]
        stranger = Client()
        stranger.force_login(get_user_model().objects.create_user("source-stranger"))
        self.assertEqual(stranger.get(f"/api/operation-sources/{self.source_id}/recordings").status_code, 404)
        self.assertEqual(
            stranger.get(recording["content_url"]).status_code, 404
        )
        # The owner streams the whole file and byte ranges from private storage.
        content = self.client.get(recording["content_url"])
        self.assertEqual(content.status_code, 200)
        self.assertEqual(b"".join(content.streaming_content), self.video)
        partial = self.client.get(recording["content_url"], HTTP_RANGE="bytes=0-99")
        self.assertEqual(partial.status_code, 206)
        self.assertEqual(partial["Content-Range"], f"bytes 0-99/{len(self.video)}")

    def test_owner_with_viewer_role_cannot_import_but_can_list(self):
        # Role and ownership are separate gates: the owner keeps read access
        # while the viewer role refuses the import.
        grant(self.owner, "viewer")
        self.assertEqual(self.client.get(f"/api/operation-sources/{self.source_id}/recordings").status_code, 200)
        response = self.client.post(
            f"/api/operation-sources/{self.source_id}/recordings",
            {"video": SimpleUploadedFile("clip.mp4", self.video, content_type="video/mp4")},
        )
        self.assertEqual(response.status_code, 403, response.content)
        self.assertEqual(OperationSourceRecording.objects.count(), 0)
