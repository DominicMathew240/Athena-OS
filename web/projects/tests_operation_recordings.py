"""Real MP4 upload into a private operation draft; fixtures are synthetic."""

import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import Client, TransactionTestCase, override_settings

from .tests import grant


@skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg and ffprobe required for recorded-video tests")
class OperationRecordingTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tmp = tempfile.TemporaryDirectory(prefix="recording-fixture-")
        path = Path(cls.tmp.name) / "fixture.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=blue:s=64x48:r=5",
                "-t",
                "1",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                "-y",
                str(path),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        cls.video = path.read_bytes()
        path.unlink()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()
        super().tearDownClass()

    def setUp(self):
        self.media = tempfile.TemporaryDirectory(prefix="recording-media-")
        self.addCleanup(self.media.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=self.media.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.user = get_user_model().objects.create_user("recording-owner")
        self.client.force_login(self.user)
        response = self.client.post(
            "/api/operation-drafts", json.dumps({"name": "Door", "line": None}), content_type="application/json"
        )
        self.draft = response.json()["draft"]
        self.url = "/api/operation-drafts/" + self.draft["id"] + "/recording"

    def upload(self, data=None, revision="1", client=None):
        return (client or self.client).post(
            self.url,
            {
                "revision": revision,
                "video": SimpleUploadedFile("clip.mp4", self.video if data is None else data, content_type="video/mp4"),
            },
        )

    def test_upload_reopen_stream_and_remove(self):
        response = self.upload()
        self.assertEqual(response.status_code, 201, response.content)
        draft = response.json()["draft"]
        self.assertEqual(draft["recording"]["sha256"], hashlib.sha256(self.video).hexdigest())
        self.assertEqual(draft["recording"]["width"], 64)
        self.assertEqual(draft["revision"], 2)
        content = self.client.get(self.url + "/content", HTTP_RANGE="bytes=0-19")
        self.assertEqual(content.status_code, 206)
        self.assertEqual(b"".join(content.streaming_content), self.video[:20])
        self.assertEqual(content["Content-Range"], f"bytes 0-19/{len(self.video)}")
        self.assertEqual(
            self.client.get("/api/operation-drafts/" + draft["id"]).json()["draft"]["recording"]["sha256"],
            draft["recording"]["sha256"],
        )
        response = self.client.delete(self.url, HTTP_X_DRAFT_REVISION="2")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["draft"]["recording"])
        self.assertEqual(list(Path(settings.MEDIA_ROOT).rglob("*.mp4")), [])

    def test_isolation_validation_and_stale_revision(self):
        other = Client()
        other.force_login(get_user_model().objects.create_user("recording-other"))
        self.assertEqual(self.upload(client=other).status_code, 404)
        self.assertEqual(self.client.get(self.url + "/content").status_code, 404)
        self.assertEqual(self.upload(data=b"not a video").status_code, 400)
        self.assertEqual(self.upload(revision="3").status_code, 409)
        self.assertEqual(list(Path(settings.MEDIA_ROOT).rglob("*.mp4")), [])
        self.assertEqual(self.upload().status_code, 201)
        self.assertEqual(other.get(self.url + "/content").status_code, 404)
        self.assertEqual(self.upload(revision="2").status_code, 409)
        self.assertEqual(self.client.delete(self.url, HTTP_X_DRAFT_REVISION="1").status_code, 409)

    def test_line_is_bound_to_recording_revision_and_cleared_when_removed(self):
        self.assertEqual(self.upload().status_code, 201)
        draft_url = "/api/operation-drafts/" + self.draft["id"]
        line = {"start": [0.2, 0.5], "end": [0.8, 0.5], "name": "Front door line", "positive_label": "Entering", "negative_label": "Leaving"}
        events = [{"time": 0.9, "direction": "negative_to_positive"}]
        response = self.client.put(
            draft_url,
            json.dumps({"name": "Door", "line": line, "reference_events": events, "revision": 2}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get(draft_url).json()["draft"]["line"], line)
        self.assertEqual(self.client.get(draft_url).json()["draft"]["reference_events"], events)
        self.assertEqual(self.client.delete(self.url, HTTP_X_DRAFT_REVISION="2").status_code, 409)
        response = self.client.delete(self.url, HTTP_X_DRAFT_REVISION="3")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["draft"]["line"])
        self.assertIsNone(self.client.get(draft_url).json()["draft"]["line"])

    def test_ranges_and_cascade_cleanup(self):
        self.assertEqual(self.upload().status_code, 201)
        response = self.client.get(self.url + "/content", HTTP_RANGE="bytes=-10")
        self.assertEqual(response.status_code, 206)
        self.assertEqual(b"".join(response.streaming_content), self.video[-10:])
        for bad_range in ("bytes=99999999-", "bytes=-0", "bytes=1-2,4-5", "bytes=" + "9" * 100 + "-"):
            with self.subTest(value=bad_range):
                response = self.client.get(self.url + "/content", HTTP_RANGE=bad_range)
                self.assertEqual(response.status_code, 416)
                self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(self.client.delete("/api/operation-drafts/" + self.draft["id"]).status_code, 204)
        self.assertEqual(list(Path(settings.MEDIA_ROOT).rglob("*.mp4")), [])

    def test_storage_cleanup_waits_for_commit(self):
        from .models import OperationRecording

        self.assertEqual(self.upload().status_code, 201)
        recording = OperationRecording.objects.get()
        path = Path(settings.MEDIA_ROOT) / recording.storage_key
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                recording.delete()
                self.assertTrue(path.exists())
                raise RuntimeError("roll back")
        self.assertTrue(path.exists())
        self.assertTrue(OperationRecording.objects.exists())

    def test_missing_probe_and_race_do_not_leave_uploaded_files(self):
        from .models import OperationDraft
        from .operations.recordings import inspect_upload

        with patch("projects.operations.recordings.subprocess.run", side_effect=FileNotFoundError()):
            self.assertEqual(self.upload().status_code, 503)

        def changed_during_probe(upload):
            result = inspect_upload(upload)
            OperationDraft.objects.filter(id=self.draft["id"]).update(revision=2)
            return result

        with patch("projects.recording_views.recordings.inspect_upload", side_effect=changed_during_probe):
            self.assertEqual(self.upload().status_code, 409)
        self.assertEqual(list(Path(settings.MEDIA_ROOT).rglob("*.mp4")), [])

    def test_stale_upload_storage_failure_is_queued_for_retry(self):
        from django.core.management import call_command

        from .models import OperationDraft, OperationRecordingCleanup
        from .operations.recordings import inspect_upload

        def changed_during_probe(upload):
            result = inspect_upload(upload)
            OperationDraft.objects.filter(id=self.draft["id"]).update(revision=2)
            return result

        with patch("projects.recording_views.recordings.inspect_upload", side_effect=changed_during_probe):
            with patch("projects.operations.recordings.default_storage.delete", side_effect=OSError("offline")):
                response = self.upload()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(OperationRecordingCleanup.objects.count(), 1)
        call_command("cleanup_operation_recordings", verbosity=0)
        self.assertEqual(OperationRecordingCleanup.objects.count(), 0)
        self.assertEqual(list(Path(settings.MEDIA_ROOT).rglob("*.mp4")), [])

    def test_failed_deletion_keeps_a_retryable_cleanup_record(self):
        from django.core.management import call_command

        from .models import OperationRecording, OperationRecordingCleanup

        self.assertEqual(self.upload().status_code, 201)
        path = Path(settings.MEDIA_ROOT) / OperationRecording.objects.get().storage_key
        with patch("projects.operations.recordings.default_storage.delete", side_effect=OSError("offline")):
            response = self.client.delete(self.url, HTTP_X_DRAFT_REVISION="2")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(path.exists())
        self.assertEqual(OperationRecordingCleanup.objects.count(), 1)
        call_command("cleanup_operation_recordings", verbosity=0)
        self.assertFalse(path.exists())
        self.assertEqual(OperationRecordingCleanup.objects.count(), 0)

    def test_viewer_and_csrf_are_enforced(self):
        viewer = get_user_model().objects.create_user("recording-viewer")
        grant(viewer, "viewer")
        client = Client(enforce_csrf_checks=True)
        client.force_login(viewer)
        self.assertEqual(self.upload(client=client).status_code, 403)
        client.force_login(self.user)
        self.assertEqual(self.upload(client=client).status_code, 403)
