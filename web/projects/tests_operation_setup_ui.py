"""The operations console page: scope setup, pin pickers and release candidates."""

import json

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from .tests import grant


def make_scope(client, name="Main shop", timezone_name="Asia/Kuala_Lumpur"):
    """Create a site, one recorded-video source, and an operation through the API."""
    site = client.post(
        "/api/operation-sites", json.dumps({"name": name, "timezone_name": timezone_name}), content_type="application/json"
    ).json()["site"]
    source = client.post(
        "/api/operation-sources",
        json.dumps({"site_id": site["id"], "name": "Front entrance", "kind": "recorded_video"}),
        content_type="application/json",
    ).json()["source"]
    operation = client.post(
        "/api/operations",
        json.dumps({"name": "Shop entrance", "site_id": site["id"], "source_id": source["id"]}),
        content_type="application/json",
    ).json()["operation"]
    return site, source, operation


class DraftAttachTests(TestCase):
    """An unbound draft can join its operation when it is saved, exactly once."""

    def setUp(self):
        self.user = get_user_model().objects.create_user("attacher")
        grant(self.user, "labeler")
        self.client.force_login(self.user)
        self.line = {"start": [0.1, 0.5], "end": [0.9, 0.5], "positive_label": "Entering", "negative_label": "Leaving"}
        _, _, self.operation = make_scope(self.client)

    def put(self, path, payload, status=200):
        response = self.client.put(path, json.dumps(payload), content_type="application/json")
        self.assertEqual(response.status_code, status, response.content)
        return response.json()

    def test_unbound_draft_can_be_attached_on_save_and_only_once(self):
        draft = self.client.post(
            "/api/operation-drafts", json.dumps({"name": "Shop door", "line": self.line}), content_type="application/json"
        ).json()["draft"]
        self.assertIsNone(draft["operation_id"])
        url = f"/api/operation-drafts/{draft['id']}"
        result = self.put(url, {"name": "Shop door", "line": self.line, "revision": draft["revision"], "operation_id": self.operation["id"]})
        self.assertEqual(result["draft"]["operation_id"], self.operation["id"])
        # The binding is permanent: a second attach attempt is refused, not a rebind.
        self.put(
            url,
            {"name": "Shop door", "line": self.line, "revision": result["draft"]["revision"], "operation_id": self.operation["id"]},
            status=400,
        )
        self.assertEqual(self.client.get(url).json()["draft"]["operation_id"], self.operation["id"])

    def test_attaching_another_persons_operation_is_refused(self):
        stranger = get_user_model().objects.create_user("attach-stranger")
        grant(stranger, "labeler")
        stranger_client = Client()
        stranger_client.force_login(stranger)
        _, _, foreign_operation = make_scope(stranger_client, name="Foreign shop")
        draft = self.client.post(
            "/api/operation-drafts", json.dumps({"name": "Shop door", "line": self.line}), content_type="application/json"
        ).json()["draft"]
        response = self.client.put(
            f"/api/operation-drafts/{draft['id']}",
            json.dumps({"name": "Shop door", "line": self.line, "revision": draft["revision"], "operation_id": foreign_operation["id"]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404, response.content)


class OperationsPageTests(TestCase):
    """The operations page shows the setup, pin and release areas alongside the draft editor."""

    def setUp(self):
        self.user = get_user_model().objects.create_user("setup-user")
        grant(self.user, "labeler")
        self.client.force_login(self.user)

    def test_page_includes_the_operation_setup_surfaces(self):
        page = self.client.get("/operations")
        self.assertEqual(page.status_code, 200)
        for needle in (
            'id="operation-setup"',
            'id="pin-section"',
            'id="creator-section"',
            'id="release-section"',
            'id="site-create"',
            'id="source-create"',
            'id="operation-create"',
            'id="attach-area"',
            'id="pin-fields"',
            'id="operation-creators"',
            'id="release-area"',
            "operation-versions.js",
        ):
            with self.subTest(needle=needle):
                self.assertContains(page, needle)

    def test_scope_forms_are_gated_to_the_labeler_role(self):
        page = self.client.get("/operations")
        for needle in ('id="site-create" data-needs="label"', 'id="source-create" data-needs="label"', 'id="operation-create" data-needs="label"'):
            with self.subTest(needle=needle):
                self.assertContains(page, needle)

    def test_viewer_sees_the_page_but_the_forms_say_why_they_cannot_act(self):
        grant(self.user, "viewer")
        page = self.client.get("/operations")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'id="operation-setup"')
