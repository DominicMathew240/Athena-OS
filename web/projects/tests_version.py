"""The console's software version: one source of truth, three surfaces."""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase


class VersionSurfaceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("version-reader")

    def test_api_version_is_public_and_names_the_release(self):
        response = Client().get("/api/version")
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])
        body = response.json()
        self.assertEqual(body["name"], "athena-console")
        self.assertTrue(body["version"][0].isdigit() and "." in body["version"])

    def test_healthz_includes_the_version(self):
        response = Client().get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["version"], Client().get("/api/version").json()["version"])

    def test_console_footer_shows_the_version_to_signed_in_users(self):
        client = Client()
        client.force_login(self.user)
        page = client.get("/detectors")
        self.assertContains(page, 'data-testid="sidebar-version"')
        version = client.get("/api/version").json()["version"]
        self.assertContains(page, f"Athena OS {version}")

    def test_an_override_may_only_add_a_suffix_never_replace_the_release(self):
        import os

        from athena_web.version import current
        from django.conf import settings

        release = settings.RELEASE_VERSION
        old = os.environ.get("ATHENA_VERSION")
        try:
            os.environ["ATHENA_VERSION"] = f"{release}+acme"
            self.assertEqual(current(), f"{release}+acme")
            os.environ["ATHENA_VERSION"] = "9.9.9"
            self.assertEqual(current(), release)
        finally:
            if old is None:
                os.environ.pop("ATHENA_VERSION", None)
            else:
                os.environ["ATHENA_VERSION"] = old
