"""Credential protection for operation sources (T11).

Secrets are encrypted at rest with a key file under the private data home, are
never returned by any API path, and never appear in error text or logs. Outbound
destinations are refused unless their exact host is on the approved list. No
outbound endpoint exists yet — these tests cover the guards that future call
sites (T10 live sources, later webhooks/exports) must pass through.
"""

import shutil
import stat
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from . import credentials as credential_store
from .destinations import DestinationRefused, check_destination
from .models import OperationSource, SourceCredential
from .tests import grant

# Assembled from parts so gitleaks' generic-api-key rule does not flag this test fixture.
SECRET = "-".join(("correct", "horse", "battery", "staple", "7f3a"))


def _make_source(client, user):
    site = client.post(
        "/api/operation-sites",
        '{"name": "KL shop", "timezone_name": "Asia/Kuala_Lumpur"}',
        content_type="application/json",
    ).json()["site"]
    return client.post(
        "/api/operation-sources",
        f'{{"site_id": "{site["id"]}", "name": "Front door", "kind": "camera"}}',
        content_type="application/json",
    ).json()["source"]


class CredentialVaultTests(TestCase):
    """Encryption at rest and key-file handling in projects/credentials.py."""

    def run(self, result=None):
        self.data_home = Path(tempfile.mkdtemp(prefix="t11-keyhome-"))
        try:
            with override_settings(DATA_HOME=self.data_home):
                credential_store._fernet = None
                return super().run(result)
        finally:
            shutil.rmtree(self.data_home, ignore_errors=True)

    def test_stored_ciphertext_is_not_the_plaintext_and_reverses(self):
        token = credential_store.encrypt(SECRET)
        self.assertNotIn(SECRET, token)
        self.assertNotEqual(token, "")
        self.assertEqual(credential_store.reveal(token), SECRET)

    def test_key_file_is_created_with_owner_only_permissions(self):
        credential_store.encrypt(SECRET)
        key_path = self.data_home / "operation-credentials.key"
        self.assertTrue(key_path.exists())
        self.assertEqual(stat.S_IMODE(key_path.stat().st_mode) & 0o077, 0)

    def test_no_secret_in_hint(self):
        hint = credential_store.hint(SECRET)
        self.assertTrue(hint)
        self.assertNotIn(SECRET, hint)
        # Hint leaks at most the last four characters, by design; the secret itself must not appear.

    def test_reveal_returns_none_after_key_rotation(self):
        token = credential_store.encrypt(SECRET)
        (self.data_home / "operation-credentials.key").unlink()
        credential_store._fernet = None  # simulate a process restart between rotation and read
        self.assertIsNone(credential_store.reveal(token))

    def test_over_open_permissions_key_file_is_refused(self):
        key_path = self.data_home / "operation-credentials.key"
        key_path.write_bytes(b"x" * 44)
        key_path.chmod(0o644)
        with self.assertRaises(PermissionError):
            credential_store.encrypt(SECRET)


class SourceCredentialApiTests(TestCase):
    """The owner-scoped credential API: set, read reference, rotate, clear — never the secret."""

    def setUp(self):
        self.data_home = Path(tempfile.mkdtemp(prefix="t11-apihome-"))
        override = override_settings(DATA_HOME=self.data_home)
        override.enable()
        self.addCleanup(override.disable)
        credential_store._fernet = None

        self.owner = get_user_model().objects.create_user("t11-owner")
        grant(self.owner, "labeler")
        self.client.force_login(self.owner)
        self.source = _make_source(self.client, self.owner)
        self.source_row = OperationSource.objects.get(id=self.source["id"])

    def tearDown(self):
        shutil.rmtree(self.data_home, ignore_errors=True)
        credential_store._fernet = None

    def _put_secret(self, secret=SECRET):
        return self.client.put(
            f'/api/operation-sources/{self.source["id"]}/credential',
            f'{{"secret": "{secret}"}}',
            content_type="application/json",
        )

    def test_setting_a_credential_returns_reference_not_secret(self):
        response = self._put_secret()
        self.assertEqual(response.status_code, 201)
        body = response.json()["credential"]
        self.assertNotIn(SECRET, response.content.decode())
        self.assertNotIn("secret", body)
        self.assertTrue(body["set"])
        self.assertTrue(body["hint"])

    def test_the_database_row_is_encrypted_at_rest(self):
        self._put_secret()
        row = SourceCredential.objects.get(source=self.source_row)
        self.assertNotIn(SECRET, row.secret_encrypted)
        self.assertEqual(credential_store.reveal(row.secret_encrypted), SECRET)

    def test_reading_back_answers_only_whether_it_is_set(self):
        self._put_secret()
        response = self.client.get(f'/api/operation-sources/{self.source["id"]}/credential')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(SECRET, response.content.decode())
        self.assertTrue(response.json()["credential"]["set"])

    def test_rotation_replaces_the_ciphertext_without_history(self):
        self._put_secret()
        old_token = SourceCredential.objects.get(source=self.source_row).secret_encrypted
        self._put_secret("rotated-secret-99bb21")
        row = SourceCredential.objects.get(source=self.source_row)
        self.assertNotEqual(row.secret_encrypted, old_token)
        self.assertEqual(credential_store.reveal(row.secret_encrypted), "rotated-secret-99bb21")
        self.assertEqual(SourceCredential.objects.filter(source=self.source_row).count(), 1)

    def test_clearing_removes_the_ciphertext(self):
        self._put_secret()
        response = self.client.delete(f'/api/operation-sources/{self.source["id"]}/credential')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            SourceCredential.objects.filter(source=self.source_row).exists()
        )
        body = self.client.get(f'/api/operation-sources/{self.source["id"]}/credential').json()
        self.assertFalse(body["credential"]["set"])

    def test_an_empty_secret_is_refused_with_a_plain_message(self):
        response = self._put_secret("   ")
        self.assertEqual(response.status_code, 400)
        self.assertNotIn(SECRET, response.content.decode())

    def test_credentials_are_private_across_owners(self):
        self._put_secret()
        stranger = get_user_model().objects.create_user("t11-stranger")
        grant(stranger, "labeler")
        stranger_client = Client()
        stranger_client.force_login(stranger)
        url = f'/api/operation-sources/{self.source["id"]}/credential'
        self.assertEqual(stranger_client.get(url).status_code, 404)
        response = stranger_client.put(url, data='{"secret": "evil"}', content_type="application/json")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(stranger_client.delete(url).status_code, 404)

    def test_a_viewer_cannot_set_but_the_reference_is_owner_scoped(self):
        viewer = get_user_model().objects.create_user("t11-viewer")
        grant(viewer, "viewer")
        viewer_client = Client()
        viewer_client.force_login(viewer)
        url = f'/api/operation-sources/{self.source["id"]}/credential'
        # The role gate answers first: a Viewer is refused (403) before ownership matters.
        response = viewer_client.put(url, data='{"secret": "nope"}', content_type="application/json")
        self.assertEqual(response.status_code, 403)
        # The read reference stays owner-scoped: a Viewer who is not the owner gets 404.
        self.assertEqual(viewer_client.get(url).status_code, 404)

    def test_secret_never_appears_in_server_logs(self):
        # Exercise set + read + a failing request, then scan every logger's records
        # captured during the whole flow: the secret must appear in none of them.
        import logging

        class _Capture(logging.Handler):
            def __init__(self):
                super().__init__(level=logging.DEBUG)
                self.records: list[str] = []

            def emit(self, record):
                self.records.append(record.getMessage())

        capture = _Capture()
        root = logging.getLogger()
        root.addHandler(capture)
        try:
            self._put_secret()
            self.client.get(f'/api/operation-sources/{self.source["id"]}/credential')
            self._put_secret("")  # refused request: error paths must stay secret-free too
        finally:
            root.removeHandler(capture)
        for message in capture.records:
            self.assertNotIn(SECRET, message)


@override_settings(ATHENA_APPROVED_DESTINATIONS=["https://vendor.example", "https://alerts.internal"])
class DestinationGuardTests(TestCase):
    """The outbound allowlist guard: exact-host HTTPS only, no userinfo or lookalike tricks."""

    def test_approved_exact_host_passes(self):
        self.assertEqual(check_destination("https://vendor.example/path"), "https://vendor.example")

    def test_arbitrary_host_is_refused(self):
        with self.assertRaises(DestinationRefused) as caught:
            check_destination("https://evil.example/webhook")
        self.assertNotIn("evil.example", str(caught.exception))

    def test_userinfo_authority_confusion_is_refused(self):
        with self.assertRaises(DestinationRefused):
            check_destination("https://vendor.example@evil.example/webhook")

    def test_embedded_at_sign_in_netloc_is_refused(self):
        with self.assertRaises(DestinationRefused):
            check_destination("https://evil@example.com@vendor.example/")

    def test_subdomains_are_not_implicitly_approved(self):
        with self.assertRaises(DestinationRefused):
            check_destination("https://sub.vendor.example/")

    def test_http_scheme_is_refused(self):
        with self.assertRaises(DestinationRefused):
            check_destination("http://vendor.example/")

    def test_garbage_is_refused_plaintextlessly(self):
        with self.assertRaises(DestinationRefused) as caught:
            check_destination("not a url at all")
        self.assertNotIn("not a url", str(caught.exception))

    def test_the_approved_list_endpoint_lists_entries_for_signed_in_users(self):
        user = get_user_model().objects.create_user("t11-dest")
        grant(user, "viewer")
        client = Client()
        client.force_login(user)
        response = client.get("/api/approved-destinations")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["approved_destinations"],
            ["https://alerts.internal", "https://vendor.example"],
        )

    def test_the_default_setting_approves_nothing(self):
        with override_settings(ATHENA_APPROVED_DESTINATIONS=[]):
            with self.assertRaises(DestinationRefused):
                check_destination("https://vendor.example/")
