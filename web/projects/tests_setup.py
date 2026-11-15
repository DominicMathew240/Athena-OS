"""First-run onboarding journey when Athena OS is freshly installed with no users."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import AIConnection


class FirstRunSetupTests(TestCase):
    def test_fresh_install_redirects_login_and_protected_routes_to_setup(self) -> None:
        self.assertFalse(get_user_model().objects.exists())
        self.assertRedirects(self.client.get("/login/"), "/setup/", fetch_redirect_response=False)
        self.assertRedirects(self.client.get("/detectors"), "/setup/", fetch_redirect_response=False)

        page = self.client.get("/setup/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'data-testid="setup-form"')
        self.assertContains(page, 'data-testid="setup-email-input"')
        self.assertContains(page, 'data-testid="setup-password-input"')
        self.assertContains(page, 'data-testid="setup-ai-local"')

    def test_setup_rejects_mismatched_passwords_and_invalid_email(self) -> None:
        response = self.client.post("/setup/", {
            "email": "not-an-email",
            "password": "StrongPassword!123",
            "password_confirm": "DifferentPassword!123",
            "ai_mode": "local_qwen",
        })
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Enter a valid email address.", status_code=400)
        self.assertContains(response, "The two passwords do not match.", status_code=400)
        self.assertFalse(get_user_model().objects.exists())

    def test_setup_creates_admin_saves_local_qwen_and_opens_dashboard(self) -> None:
        response = self.client.post("/setup/", {
            "email": "owner@athena.local",
            "username": "",
            "password": "StrongPassword!123",
            "password_confirm": "StrongPassword!123",
            "ai_mode": "local_qwen",
        })
        self.assertRedirects(response, "/")

        user = get_user_model().objects.get(email="owner@athena.local")
        self.assertTrue(user.is_superuser)
        self.assertEqual(user.username, "owner")

        conn = AIConnection.objects.get(user=user)
        self.assertTrue(conn.use_own)
        self.assertEqual(conn.base_url, "http://127.0.0.1:11434/v1")
        self.assertEqual(conn.model, "qwen2.5vl:7b")

        dashboard = self.client.get("/")
        self.assertEqual(dashboard.status_code, 200)
        self.assertContains(dashboard, "What do you want to check today?")

    def test_setup_is_locked_once_a_user_exists(self) -> None:
        get_user_model().objects.create_user("existing", "existing@athena.local", "StrongPassword!123")
        self.assertRedirects(self.client.get("/setup/"), "/login/", fetch_redirect_response=False)
        self.assertRedirects(self.client.post("/setup/", {"email": "attacker@evil.invalid"}), "/login/", fetch_redirect_response=False)
        self.assertEqual(self.client.get("/login/").status_code, 200)
