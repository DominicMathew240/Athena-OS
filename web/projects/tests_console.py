from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings

WEB_DIR = Path(__file__).resolve().parent.parent
FAST_HASHER = ["django.contrib.auth.hashers.MD5PasswordHasher"]
LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
PASSWORD = "a-long-passphrase-for-tests"


@override_settings(PASSWORD_HASHERS=FAST_HASHER, CACHES=LOCMEM)
class SignInTests(TestCase):
    def setUp(self) -> None:
        cache.clear()
        self.user = get_user_model().objects.create_user("annotator", password=PASSWORD)

    def test_console_redirects_anonymous_visitors_to_sign_in(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/login/")

    def test_api_returns_json_401_instead_of_a_login_redirect(self) -> None:
        for method, url in (
            ("get", "/api/projects"),
            ("post", "/api/projects"),
            ("get", "/api/autolabel"),
            ("get", "/api/projects/00000000-0000-0000-0000-000000000000/assets"),
        ):
            with self.subTest(url=url, method=method):
                response = getattr(self.client, method)(url)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json(), {"error": "Sign in to continue."})
                self.assertEqual(response["Cache-Control"], "no-store")

    def test_health_check_and_login_page_are_public(self) -> None:
        health = self.client.get("/healthz")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["status"], "ok")
        self.assertEqual(self.client.get("/login/").status_code, 200)

    def test_sign_in_opens_the_console(self) -> None:
        response = self.client.post("/login/", {"username": "annotator", "password": PASSWORD})
        self.assertRedirects(response, "/", fetch_redirect_response=False)
        console = self.client.get("/")
        self.assertContains(console, "annotator")
        self.assertContains(console, 'action="/logout/"')
        self.assertEqual(self.client.get("/api/projects").status_code, 200)

    def test_wrong_password_shows_one_generic_message(self) -> None:
        response = self.client.post("/login/", {"username": "annotator", "password": "nope"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please enter a correct username and password", status_code=200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_repeated_failures_lock_the_account_even_with_the_right_password(self) -> None:
        with override_settings(LOGIN_MAX_FAILURES=3):
            for _ in range(3):
                self.client.post("/login/", {"username": "annotator", "password": "wrong"})
            locked = self.client.post("/login/", {"username": "annotator", "password": PASSWORD})
            self.assertEqual(locked.status_code, 429)
            self.assertContains(locked, "Too many failed sign-in attempts", status_code=429)
            self.assertNotIn("_auth_user_id", self.client.session)

    def test_lockout_follows_the_address_when_usernames_rotate(self) -> None:
        with override_settings(LOGIN_MAX_FAILURES=3):
            for index in range(3):
                self.client.post("/login/", {"username": f"guess{index}", "password": "x"})
            response = self.client.post("/login/", {"username": "annotator", "password": PASSWORD})
            self.assertEqual(response.status_code, 429)

    def test_successful_sign_in_clears_the_username_counter(self) -> None:
        with override_settings(LOGIN_MAX_FAILURES=3):
            self.client.post("/login/", {"username": "annotator", "password": "wrong"})
            self.client.post("/login/", {"username": "annotator", "password": PASSWORD})
            self.client.post("/logout/")
            self.client.post("/login/", {"username": "annotator", "password": "wrong"})
            again = self.client.post("/login/", {"username": "annotator", "password": PASSWORD})
            self.assertEqual(again.status_code, 302)

    def test_next_parameter_cannot_redirect_off_site(self) -> None:
        response = self.client.post(
            "/login/?next=https://evil.example/", {"username": "annotator", "password": PASSWORD}
        )
        self.assertEqual(response["Location"], "/")

    def test_sign_out_requires_post_and_ends_the_session(self) -> None:
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/logout/").status_code, 405)
        self.assertEqual(self.client.post("/logout/").status_code, 302)
        self.assertEqual(self.client.get("/api/projects").status_code, 401)

    def test_login_form_enforces_csrf(self) -> None:
        client = Client(enforce_csrf_checks=True)
        response = client.post("/login/", {"username": "annotator", "password": PASSWORD})
        self.assertEqual(response.status_code, 403)


class ConsolePageTests(TestCase):
    def setUp(self) -> None:
        self.client.force_login(get_user_model().objects.create_user("operator"))

    def test_page_sends_a_strict_content_security_policy(self) -> None:
        policy = self.client.get("/")["Content-Security-Policy"]
        self.assertIn("default-src 'none'", policy)
        self.assertIn("script-src 'self'", policy)
        self.assertNotIn("unsafe-inline", policy)
        self.assertIn("frame-ancestors 'none'", policy)

    def test_markup_has_no_inline_script_style_or_handlers(self) -> None:
        for url in ("/", "/login/"):
            html = self.client.get(url).content.decode()
            with self.subTest(url=url):
                self.assertNotRegex(html, r"<script(?![^>]*\bsrc=)")
                self.assertNotIn("<style", html)
                self.assertNotRegex(html, r"\sstyle=")
                self.assertNotRegex(html, r"\son[a-z]+=")

    def test_page_no_longer_claims_to_be_an_unauthenticated_local_build(self) -> None:
        html = self.client.get("/").content.decode()
        self.assertNotIn("No login", html)
        self.assertNotIn("Early local build", html)
        self.assertIn('name="csrf-token"', html)
        self.assertRegex(html, r'content="[A-Za-z0-9]{32,}"')

    def test_csrf_cookie_is_not_readable_by_scripts(self) -> None:
        response = self.client.get("/")
        self.assertTrue(response.cookies["csrftoken"]["httponly"])

    def test_autolabel_controls_are_present(self) -> None:
        html = self.client.get("/").content.decode()
        for element_id in (
            "autolabel-form", "autolabel-run", "autolabel-accept", "autolabel-reject",
            "annotation-autolabel", "annotation-accept-all",
        ):
            self.assertIn(f'id="{element_id}"', html)

    def test_get_started_checklist_and_label_editor_are_present(self) -> None:
        html = self.client.get("/").content.decode()
        for element_id in (
            "checklist", "checklist-progress", "labels-panel", "label-list", "label-add-form",
            "label-new", "label-save", "label-discard", "save-state", "annotation-save-state",
        ):
            self.assertIn(f'id="{element_id}"', html)

    def test_page_has_skip_link_main_landmark_and_live_save_state(self) -> None:
        html = self.client.get("/").content.decode()
        self.assertIn('href="#main"', html)
        self.assertRegex(html, r'<main[^>]*id="main"')
        self.assertRegex(html, r'id="save-state" role="status" aria-live="polite"')
        self.assertRegex(html, r'id="label-error" role="alert"')

    def test_visible_text_avoids_technical_jargon(self) -> None:
        import re

        html = self.client.get("/").content.decode()
        text = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S)
        text = re.sub(r"<[^>]+>", " ", text)
        for term in (
            r"modalit", r"bounding", r"\bSAM\b", r"\bCLIP\b", r"\basset", r"\bjob\b",
            r"confidence", r"SHA-?256", r"ground truth", r"\bplugin", r"\bworker\b",
            # Added with the training-set panel; COCO and YOLO are allowed as the two format names.
            r"manifest", r"checksum", r"\bhash", r"\bsplit", r"\bseed\b", r"\bdataset", r"\bepoch",
            # Added with the "Teach the computer" panel: how well a model did, in words a person can act on.
            r"precision", r"recall", r"\bIoU\b", r"\bAP50\b", r"\bmAP", r"backend", r"threshold", r"inference",
            r"artifact", r"checkpoint", r"\bbatch\b", r"learning rate", r"\bGPU\b", r"\bstaged?\b",
        ):
            with self.subTest(term=term):
                case_sensitive = term in (r"\bSAM\b", r"\bCLIP\b", r"\bIoU\b", r"\bAP50\b", r"\bGPU\b", r"\bmAP")
                self.assertNotRegex(text, re.compile(term, 0 if case_sensitive else re.I))

    def test_teach_panel_and_decision_dialog_are_in_the_page(self) -> None:
        html = self.client.get("/").content.decode()
        for element_id in ("train-panel", "tr-form", "tr-model-list", "decision-dialog"):
            self.assertIn(f'id="{element_id}"', html)

    def test_unavailable_steps_are_labelled_not_wired_up(self) -> None:
        html = self.client.get("/").content.decode()
        self.assertIn("Coming soon", html)
        self.assertIn("no labelling tools yet", html)
        js = (WEB_DIR / "projects" / "static" / "projects" / "console.js").read_text()
        self.assertIn("Not available yet", js)
        self.assertIn("Needs your review", js)

    def test_script_has_no_inline_event_handlers_or_eval(self) -> None:
        js = (WEB_DIR / "projects" / "static" / "projects" / "console.js").read_text()
        self.assertNotRegex(js, r"\beval\(|new Function\(|\.innerHTML\s*=|\.setAttribute\(\s*[\"']on")


class DeploymentSettingsTests(TestCase):
    """Run Django in a clean subprocess so each case sees real production settings."""

    SECRET = "ci-only-ci-only-ci-only-ci-only-ci-only-ci-only-ci-only"

    def manage(self, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        base = {"PATH": os.environ.get("PATH", ""), "HOME": tempfile.gettempdir()}
        with tempfile.TemporaryDirectory() as data:
            full = {**base, "ATHENA_DATA_HOME": data, "ATHENA_STATIC_ROOT": data + "-static", **env}
            return subprocess.run(
                [sys.executable, "manage.py", *args],
                cwd=WEB_DIR, env=full, capture_output=True, text=True, timeout=120,
            )

    def production(self, **overrides: str) -> dict[str, str]:
        env = {
            "DJANGO_SECRET_KEY": self.SECRET,
            "ATHENA_ALLOWED_HOSTS": "console.example.com",
            "ATHENA_BEHIND_PROXY": "1",
        }
        env.update(overrides)
        return env

    def test_deploy_check_is_clean_with_production_settings(self) -> None:
        result = self.manage("check", "--deploy", "--fail-level", "WARNING", **self.production())
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("no issues", result.stdout)

    def test_refuses_to_start_without_a_secret_key(self) -> None:
        env = self.production()
        del env["DJANGO_SECRET_KEY"]
        result = self.manage("check", **env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DJANGO_SECRET_KEY", result.stderr)

    def test_refuses_weak_or_insecure_secret_keys(self) -> None:
        for key in ("short", "django-insecure-" + "a" * 60):
            with self.subTest(key=key[:12]):
                result = self.manage("check", **self.production(DJANGO_SECRET_KEY=key))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("DJANGO_SECRET_KEY", result.stderr)

    def test_refuses_missing_or_wildcard_allowed_hosts(self) -> None:
        for hosts in ("", "*", "console.example.com,*"):
            with self.subTest(hosts=hosts):
                result = self.manage("check", **self.production(ATHENA_ALLOWED_HOSTS=hosts))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("ATHENA_ALLOWED_HOSTS", result.stderr)

    def test_debug_stays_off_unless_explicitly_enabled(self) -> None:
        result = self.manage(
            "shell", "-c", "from django.conf import settings as s; print(s.DEBUG, s.SESSION_COOKIE_SECURE, s.SECURE_SSL_REDIRECT)",
            **self.production(),
        )
        self.assertEqual(result.stdout.strip().splitlines()[-1], "False True True", result.stderr)

    def test_migrations_are_in_sync_with_models(self) -> None:
        result = self.manage("makemigrations", "--check", "--dry-run", DJANGO_DEBUG="1")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class HealthCheckTests(TestCase):
    def test_reports_unavailable_when_the_database_is_down(self) -> None:
        from unittest.mock import patch

        with patch("projects.auth_views.connection.cursor", side_effect=RuntimeError("down")):
            response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["database"], False)
        self.assertNotIn("down", response.content.decode())


