"""Environment and dependency preflight for operations (T08)."""

from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from . import operation_preflight
from .tests import grant


@override_settings(VISION={"BASE_URL": "", "MODEL": "qwen2.5vl:7b", "LABEL": "", "API_KEY": "", "TIMEOUT_SECONDS": 30, "MAX_TOKENS": 1000})
class PreflightApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("preflight-reader")
        grant(self.user, "viewer")
        self.client.force_login(self.user)
        # The worker heartbeats live in the cache; start every test from a quiet cache.
        from django.core.cache import cache
        cache.clear()

    def get(self):
        response = self.client.get("/api/operation-preflight")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn("no-store", response["Cache-Control"])
        return response.json()

    def test_every_check_names_its_owner_and_recovery_when_blocked(self):
        body = self.get()
        self.assertEqual(
            [c["id"] for c in body["checks"]],
            ["video_checking", "storage", "teaching", "checkscreen", "vision"],
        )
        for check in body["checks"]:
            with self.subTest(check=check["id"]):
                self.assertIn("title", check)
                self.assertIn("detail", check)
                if not check["ok"]:
                    self.assertTrue(check["owner"].strip(), "a blocked check must name an owner")
                    self.assertTrue(check["recovery"].strip(), "a blocked check must say how to recover")
                    self.assertIn(check, body["blockers"])
        self.assertEqual(body["ready"], all(c["ok"] for c in body["checks"]))
        self.assertEqual(body["blockers"], [c for c in body["checks"] if not c["ok"]])

    def test_missing_ffprobe_is_a_blocker_with_recovery_text(self):
        with mock.patch.object(operation_preflight.shutil, "which", return_value=None):
            body = self.get()
        check = next(c for c in body["checks"] if c["id"] == "video_checking")
        self.assertFalse(check["ok"])
        self.assertIn("administrator", check["owner"])
        self.assertIn("ffprobe", check["recovery"])

    def test_unwritable_storage_is_a_blocker(self):
        with mock.patch.object(operation_preflight, "_storage_check") as storage:
            storage.return_value = operation_preflight._check(
                "storage", "Private storage", False,
                "Recordings cannot be kept: the server's storage folder is not writable.",
                "administrator",
                "Ask an administrator to make the Athena data folder writable (or configure private bucket storage), then reload this page.",
            )
            body = self.get()
        check = next(c for c in body["checks"] if c["id"] == "storage")
        self.assertFalse(check["ok"])
        self.assertIn("writable", check["recovery"])

    def test_offline_workers_are_blockers_with_their_start_commands(self):
        body = self.get()
        teaching = next(c for c in body["checks"] if c["id"] == "teaching")
        checkscreen = next(c for c in body["checks"] if c["id"] == "checkscreen")
        self.assertIn("train_worker", teaching["recovery"])
        self.assertIn("inference_worker", checkscreen["recovery"])

    def test_a_reachable_model_is_not_a_blocker_and_an_unreachable_one_is(self):
        from django.core.cache import cache

        conn = mock.Mock(configured=True, label="Test model", host="models.example")
        with mock.patch.object(operation_preflight.service, "active_connection", return_value=conn), \
             mock.patch.object(operation_preflight.client, "ping", return_value=True):
            body = self.get()
        self.assertTrue(next(c for c in body["checks"] if c["id"] == "vision")["ok"])
        cache.clear()  # the endpoint caches; the changed environment must be re-read
        with mock.patch.object(operation_preflight.service, "active_connection", return_value=conn), \
             mock.patch.object(operation_preflight.client, "ping", return_value=False):
            body = self.get()
        check = next(c for c in body["checks"] if c["id"] == "vision")
        self.assertFalse(check["ok"])
        self.assertIn("AI model page", check["recovery"])

    def test_results_are_cached_between_calls(self):
        with mock.patch.object(operation_preflight, "_build_checks", wraps=operation_preflight._build_checks) as build:
            self.get()
            self.get()
            self.assertEqual(build.call_count, 1)


class PreflightPageTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("preflight-page-reader")
        grant(self.user, "viewer")
        self.client.force_login(self.user)

    def test_operations_page_contains_the_preflight_surface(self):
        page = self.client.get("/operations")
        self.assertEqual(page.status_code, 200)
        for needle in (
            'id="preflight-section"',
            'id="preflight-list"',
            'id="preflight-refresh"',
            "Is this server ready?",
            "operation-preflight.js",
        ):
            with self.subTest(needle=needle):
                self.assertContains(page, needle)
