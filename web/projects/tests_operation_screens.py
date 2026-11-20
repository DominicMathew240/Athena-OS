"""Operation board layouts live in immutable screen versions pinned by drafts."""

import json
import re

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from .tests import grant


class OperationScreenTests(TestCase):
    """Screen-version creation, validation, immutability, scoping and draft pins."""

    def setUp(self):
        from .models import Operation, OperationSource, Site

        self.user = get_user_model().objects.create_user("screen-builder")
        self.client.force_login(self.user)
        self.site = Site.objects.create(created_by=self.user, name="Shop")
        self.source = OperationSource.objects.create(site=self.site, name="Front", kind="recorded_video")
        self.operation = Operation.objects.create(created_by=self.user, site=self.site, source=self.source, name="Door")
        self.line = {"start": [0.1, 0.5], "end": [0.9, 0.5], "name": "Front door line", "positive_label": "Entering", "negative_label": "Leaving"}

    def _url(self, operation=None):
        return f"/api/operations/{(operation or self.operation).id}/screen-versions"

    def _configuration(self, title="Bay board", show_entries=True, show_exits=True):
        return {"title": title, "show_entries": show_entries, "show_exits": show_exits}

    def _post_screen(self, configuration, operation=None, client=None):
        return (client or self.client).post(
            self._url(operation), json.dumps({"configuration": configuration}), content_type="application/json"
        )

    def _post_draft(self, extra=None, client=None):
        payload = {"operation_id": str(self.operation.id), "name": "Shop door", "line": self.line}
        payload.update(extra or {})
        return (client or self.client).post(
            "/api/operation-drafts", json.dumps(payload), content_type="application/json"
        )

    def test_versions_are_numbered_hashed_and_listed_newest_first(self):
        first = self._post_screen(self._configuration())
        self.assertEqual(first.status_code, 201, first.content)
        body = first.json()["screen_version"]
        self.assertEqual(body["version"], 1)
        self.assertEqual(body["operation_id"], str(self.operation.id))
        self.assertEqual(body["configuration"], self._configuration())
        self.assertRegex(body["sha256"], re.compile(r"^[0-9a-f]{64}$"))
        self.assertTrue(body["created_at"])

        second = self._post_screen(self._configuration(title="Night board", show_exits=False))
        self.assertEqual(second.status_code, 201, second.content)
        other = second.json()["screen_version"]
        self.assertEqual(other["version"], 2)
        self.assertNotEqual(other["sha256"], body["sha256"])

        listed = self.client.get(self._url()).json()["screen_versions"]
        self.assertEqual([item["version"] for item in listed], [2, 1])
        for item in listed:
            self.assertEqual(set(item), {"id", "operation_id", "version", "configuration", "sha256", "created_at"})
        self.assertEqual(listed[0]["configuration"]["title"], "Night board")
        self.assertFalse(listed[0]["configuration"]["show_exits"])

    def test_invalid_configurations_are_rejected_without_creating_anything(self):
        from .models import OperationScreenVersion

        invalid = [
            {"show_entries": True, "show_exits": True},  # missing title
            {**self._configuration(), "theme": "dark"},  # extra key
            {**self._configuration(), "title": "   "},  # empty after stripping
            {**self._configuration(), "title": "x" * 121},  # too long
            {**self._configuration(), "title": 7},  # non-string
            {**self._configuration(), "show_entries": "yes"},
            {**self._configuration(), "show_entries": 1},
            {**self._configuration(), "show_entries": None},
            {**self._configuration(), "show_exits": "true"},
            {**self._configuration(), "show_exits": 0},
        ]
        for configuration in invalid:
            with self.subTest(configuration=configuration):
                response = self._post_screen(configuration)
                self.assertEqual(response.status_code, 400, response.content)
        for payload in ([], {"configuration": None}, {"screen": self._configuration()}, {}):
            with self.subTest(payload=payload):
                response = self.client.post(self._url(), json.dumps(payload), content_type="application/json")
                self.assertEqual(response.status_code, 400, response.content)
        self.assertFalse(OperationScreenVersion.objects.exists())
        self.assertEqual(self.client.get(self._url()).json()["screen_versions"], [])

    def test_board_title_is_stored_and_hashed_without_surrounding_whitespace(self):
        response = self._post_screen(self._configuration(title="  Bay board  "))
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()["screen_version"]
        self.assertEqual(body["configuration"]["title"], "Bay board")
        compact = self._post_screen(self._configuration(title="Bay board"))
        self.assertEqual(compact.json()["screen_version"]["sha256"], body["sha256"])

    def test_screen_versions_are_frozen(self):
        from .models import OperationScreenVersion

        row_id = self._post_screen(self._configuration()).json()["screen_version"]["id"]
        with self.assertRaises(ValueError):
            OperationScreenVersion.objects.filter(id=row_id).update(configuration={"tampered": True})
        saved = OperationScreenVersion.objects.get(id=row_id)
        saved.configuration = {"tampered": True}
        with self.assertRaises(ValueError):
            saved.save()
        with self.assertRaises(ValueError):
            OperationScreenVersion.objects.bulk_update([saved], ["configuration"])
        self.assertEqual(OperationScreenVersion.objects.get(id=row_id).configuration, self._configuration())

    def test_routes_and_pins_are_owner_scoped(self):
        stranger = Client()
        stranger.force_login(get_user_model().objects.create_user("screen-stranger"))
        self.assertEqual(stranger.get(self._url()).status_code, 404)
        self.assertEqual(
            stranger.post(
                self._url(), json.dumps({"configuration": self._configuration()}), content_type="application/json"
            ).status_code,
            404,
        )

        from .models import Operation, OperationSource, Site

        other_user = get_user_model().objects.create_user("other-owner")
        other_site = Site.objects.create(created_by=other_user, name="Other")
        other_source = OperationSource.objects.create(site=other_site, name="Back", kind="recorded_video")
        other_operation = Operation.objects.create(
            created_by=other_user, site=other_site, source=other_source, name="Other door"
        )
        other_client = Client()
        other_client.force_login(other_user)
        foreign_id = self._post_screen(self._configuration(), operation=other_operation, client=other_client).json()[
            "screen_version"
        ]["id"]
        mine = self._post_screen(self._configuration()).json()["screen_version"]["id"]

        rejected = self._post_draft({"screen_version_id": foreign_id})
        self.assertEqual(rejected.status_code, 400, rejected.content)
        self.assertIn(b"belongs to this draft's operation", rejected.content)
        accepted = self._post_draft({"screen_version_id": mine})
        self.assertEqual(accepted.status_code, 201, accepted.content)

    def test_drafts_pin_screen_versions_and_freeze_them_into_snapshots(self):
        pinned = self._post_screen(self._configuration()).json()["screen_version"]
        draft = self._post_draft({"screen_version_id": pinned["id"]}).json()["draft"]
        url = f"/api/operation-drafts/{draft['id']}"
        self.assertEqual(draft["pins"]["screen_version_id"], pinned["id"])

        snapshot = self.client.get(url + "/versions").json()["versions"][0]["snapshot"]
        screen_pin = snapshot["component_pins"]["screen"]
        self.assertEqual(screen_pin, {"id": pinned["id"], "version": pinned["version"], "sha256": pinned["sha256"]})

        second = self._post_screen(self._configuration(title="Night board"))
        updated = self.client.put(
            url,
            json.dumps(
                {
                    "name": "Shop door",
                    "line": self.line,
                    "revision": draft["revision"],
                    "screen_version_id": second.json()["screen_version"]["id"],
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(updated.status_code, 200, updated.content)
        self.assertEqual(updated.json()["draft"]["pins"]["screen_version_id"], second.json()["screen_version"]["id"])
        versions = self.client.get(url + "/versions").json()["versions"]
        self.assertEqual(
            versions[0]["snapshot"]["component_pins"]["screen"]["id"], second.json()["screen_version"]["id"]
        )
        self.assertEqual(versions[1]["snapshot"]["component_pins"]["screen"]["id"], pinned["id"])

        cleared = self.client.put(
            url,
            json.dumps(
                {
                    "name": "Shop door",
                    "line": self.line,
                    "revision": updated.json()["draft"]["revision"],
                    "screen_version_id": None,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(cleared.status_code, 200, cleared.content)
        self.assertIsNone(cleared.json()["draft"]["pins"]["screen_version_id"])
        self.assertIsNone(
            self.client.get(url + "/versions").json()["versions"][0]["snapshot"]["component_pins"]["screen"]
        )

    def test_unpinned_drafts_expose_no_screen_pin(self):
        screen = self._post_screen(self._configuration()).json()["screen_version"]
        draft = self._post_draft().json()["draft"]
        url = f"/api/operation-drafts/{draft['id']}"
        self.assertIsNone(draft["pins"]["screen_version_id"])
        snapshot = self.client.get(url + "/versions").json()["versions"][0]["snapshot"]
        self.assertIsNone(snapshot["component_pins"]["screen"])

        bad_pin = self._post_draft({"screen_version_id": "not-a-uuid"})
        self.assertEqual(bad_pin.status_code, 400, bad_pin.content)
        missing_pin = self._post_draft({"screen_version_id": "0" * 32})
        self.assertEqual(missing_pin.status_code, 400, missing_pin.content)
        unbound = self.client.post(
            "/api/operation-drafts",
            json.dumps({"name": "No operation", "line": self.line, "screen_version_id": screen["id"]}),
            content_type="application/json",
        )
        self.assertEqual(unbound.status_code, 400, unbound.content)

    def test_posting_a_screen_version_requires_the_label_role(self):
        viewer = get_user_model().objects.create_user("screen-viewer")
        grant(viewer, "viewer")
        viewer_client = Client()
        viewer_client.force_login(viewer)
        self.assertEqual(viewer_client.get(self._url()).status_code, 404)  # not this person's operation
        denied = viewer_client.post(
            self._url(), json.dumps({"configuration": self._configuration()}), content_type="application/json"
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(self._post_screen(self._configuration()).status_code, 201)
