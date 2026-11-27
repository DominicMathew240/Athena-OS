"""T10: one supported live RTSP camera integration."""
from __future__ import annotations

import base64
import json
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from . import credentials as credential_store
from . import live_rtsp
from .models import OperationSource
from .tests import grant

# Assembled so gitleaks does not flag the fixture as a real secret.
CAMERA_PASSWORD = "-".join(("cam", "pass", "fixture", "9c2e"))
# Minimal JPEG (1x1) for preview success path.
_MINI_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
    b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a"
    b"\x1f\x1e\x1d\x1a\x1c\x1c $.\' \",#\x1c\x1c(7),01444\x1f\'9=82<.342"
    b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
    b"\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b"
    b"\xff\xc4\x00\xb5\x10\x00\x02\x01\x03\x03\x02\x04\x03\x05\x05\x04\x04\x00\x00\x01}"
    b"\x01\x02\x03\x00\x04\x11\x05\x12!1A\x06\x13Qa\x07\"q\x142\x81\x91\xa1\x08#B\xb1"
    b"\xc1\x15R\xd1\xf0$3br\x82\t\n\x16\x17\x18\x19\x1a%&\'()*456789:CDEFGHIJSTUVWXYZcdefghijstuvwxyz"
    b"\x83\x84\x85\x86\x87\x88\x89\x8a\x92\x93\x94\x95\x96\x97\x98\x99\x9a\xa2\xa3\xa4\xa5\xa6\xa7"
    b"\xa8\xa9\xaa\xb2\xb3\xb4\xb5\xb6\xb7\xb8\xb9\xba\xc2\xc3\xc4\xc5\xc6\xc7\xc8\xc9\xca\xd2\xd3"
    b"\xd4\xd5\xd6\xd7\xd8\xd9\xda\xe1\xe2\xe3\xe4\xe5\xe6\xe7\xe8\xe9\xea\xf1\xf2\xf3\xf4\xf5\xf6"
    b"\xf7\xf8\xf9\xfa\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xfb\xd5o\xff\xd9"
)


def _source(client, *, kind="camera"):
    site = client.post(
        "/api/operation-sites",
        '{"name": "Site RTSP", "timezone_name": "Asia/Kuala_Lumpur"}',
        content_type="application/json",
    ).json()["site"]
    return client.post(
        "/api/operation-sources",
        json.dumps({"site_id": site["id"], "name": "Front cam", "kind": kind}),
        content_type="application/json",
    ).json()["source"]


class LiveRtspUnitTests(TestCase):
    def test_normalize_refuses_userinfo_in_url(self):
        with self.assertRaises(live_rtsp.LiveRtspError) as caught:
            live_rtsp.normalize_rtsp_url("rtsp://user:secret@cam.example/stream")
        self.assertEqual(caught.exception.category, live_rtsp.CATEGORY_INVALID_URL)

    def test_normalize_accepts_plain_rtsp(self):
        scheme, host, port, path = live_rtsp.normalize_rtsp_url("rtsp://cam.example:554/h264")
        self.assertEqual((scheme, host, port, path), ("rtsp", "cam.example", 554, "/h264"))

    @override_settings(ATHENA_APPROVED_RTSP_HOSTS=["cam.example"])
    def test_host_allowlist_exact(self):
        self.assertEqual(live_rtsp.check_rtsp_host("cam.example"), "cam.example")
        with self.assertRaises(live_rtsp.LiveRtspError) as caught:
            live_rtsp.check_rtsp_host("evil.example")
        self.assertEqual(caught.exception.category, live_rtsp.CATEGORY_NOT_APPROVED)

    def test_classify_credential_vs_network(self):
        cat, _ = live_rtsp._classify_ffprobe_failure("RTSP/1.0 401 Unauthorized", had_password=True)
        self.assertEqual(cat, live_rtsp.CATEGORY_CREDENTIAL)
        cat, _ = live_rtsp._classify_ffprobe_failure("Connection refused", had_password=False)
        self.assertEqual(cat, live_rtsp.CATEGORY_NETWORK)


@override_settings(ATHENA_APPROVED_RTSP_HOSTS=["cam.example", "10.0.0.5"])
class LiveRtspApiTests(TestCase):
    def setUp(self):
        self.data_home = Path(tempfile.mkdtemp(prefix="t10-home-"))
        override = override_settings(DATA_HOME=self.data_home)
        override.enable()
        self.addCleanup(override.disable)
        credential_store._fernet = None
        self.owner = get_user_model().objects.create_user("t10-owner")
        grant(self.owner, "labeler")
        self.client = Client()
        self.client.force_login(self.owner)
        self.source = _source(self.client)

    def tearDown(self):
        shutil.rmtree(self.data_home, ignore_errors=True)

    def _set_password(self):
        response = self.client.put(
            f"/api/operation-sources/{self.source['id']}/credential",
            json.dumps({"secret": CAMERA_PASSWORD}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)

    def test_support_endpoint_lists_codecs_without_hosts(self):
        body = self.client.get("/api/live-rtsp/support").json()["live_rtsp"]
        self.assertEqual(body["video_codecs"], ["h264"])
        self.assertIn("rtsp", body["schemes"])
        self.assertNotIn("cam.example", json.dumps(body))
        self.assertGreaterEqual(body["approved_host_count"], 1)

    def test_recorded_video_source_refused_with_source_kind(self):
        recorded = _source(self.client, kind="recorded_video")
        response = self.client.post(
            f"/api/operation-sources/{recorded['id']}/live-rtsp/preview",
            json.dumps({"url": "rtsp://cam.example/stream"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["category"], "source_kind")

    def test_unapproved_host_category(self):
        response = self.client.post(
            f"/api/operation-sources/{self.source['id']}/live-rtsp/preview",
            json.dumps({"url": "rtsp://evil.example/stream"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["category"], "not_approved")
        self.assertNotIn("evil.example", response.json()["error"])

    def test_successful_preview_uses_credential_and_returns_frame(self):
        self._set_password()
        probe_meta = {"codec_name": "h264", "width": 1280, "height": 720}

        def fake_run(command, capture_output=True, timeout=None, check=False):
            self.assertIn(command[0], {"ffprobe", "ffmpeg"})
            if command[0] == "ffprobe":
                return mock.Mock(
                    returncode=0,
                    stdout=json.dumps({"streams": [probe_meta]}).encode(),
                    stderr=b"",
                )
            return mock.Mock(returncode=0, stdout=_MINI_JPEG, stderr=b"")

        with mock.patch("projects.live_rtsp.subprocess.run", side_effect=fake_run):
            response = self.client.post(
                f"/api/operation-sources/{self.source['id']}/live-rtsp/preview",
                json.dumps({"url": "rtsp://cam.example/stream1", "username": "admin"}),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["category"], "ok")
        self.assertEqual(body["stream"]["codec_name"], "h264")
        self.assertEqual(body["stream"]["width"], 1280)
        self.assertTrue(body["credential_used"])
        self.assertEqual(base64.b64decode(body["preview_jpeg_base64"])[:2], b"\xff\xd8")
        # Secret must never appear in the HTTP body.
        self.assertNotIn(CAMERA_PASSWORD, response.content.decode("utf-8", errors="replace"))

    def test_credential_failure_category(self):
        self._set_password()

        def fake_run(command, capture_output=True, timeout=None, check=False):
            return mock.Mock(returncode=1, stdout=b"", stderr=b"RTSP/1.0 401 Unauthorized\n")

        with mock.patch("projects.live_rtsp.subprocess.run", side_effect=fake_run):
            response = self.client.post(
                f"/api/operation-sources/{self.source['id']}/live-rtsp/preview",
                json.dumps({"url": "rtsp://cam.example/stream", "username": "admin"}),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["category"], "credential")

    def test_network_failure_category(self):
        def fake_run(command, capture_output=True, timeout=None, check=False):
            return mock.Mock(returncode=1, stdout=b"", stderr=b"Connection refused\n")

        with mock.patch("projects.live_rtsp.subprocess.run", side_effect=fake_run):
            response = self.client.post(
                f"/api/operation-sources/{self.source['id']}/live-rtsp/preview",
                json.dumps({"url": "rtsp://10.0.0.5/live"}),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["category"], "network")

    def test_unsupported_codec_category(self):
        def fake_run(command, capture_output=True, timeout=None, check=False):
            return mock.Mock(
                returncode=0,
                stdout=json.dumps({"streams": [{"codec_name": "hevc", "width": 1280, "height": 720}]}).encode(),
                stderr=b"",
            )

        with mock.patch("projects.live_rtsp.subprocess.run", side_effect=fake_run):
            response = self.client.post(
                f"/api/operation-sources/{self.source['id']}/live-rtsp/preview",
                json.dumps({"url": "rtsp://cam.example/stream", "include_preview": False}),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["category"], "unsupported_codec")

    def test_stranger_cannot_preview_owned_source(self):
        stranger = get_user_model().objects.create_user("t10-stranger")
        grant(stranger, "labeler")
        other = Client()
        other.force_login(stranger)
        response = other.post(
            f"/api/operation-sources/{self.source['id']}/live-rtsp/preview",
            json.dumps({"url": "rtsp://cam.example/stream"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)
