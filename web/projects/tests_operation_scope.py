"""Site/source scoping for private operations."""
import json
from typing import Any

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from .models import Inspection


class OperationScopeApiTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("scope-owner")
        self.client.force_login(self.owner)

    def post_json(self, path: str, payload: dict[str, Any], client: Client | None = None) -> Any:
        return (client or self.client).post(path, json.dumps(payload), content_type="application/json")

    def create_scope(self, client: Client | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        client = client or self.client
        site_response = self.post_json(
            "/api/operation-sites",
            {"name": "Kuala Lumpur shop", "timezone_name": "Asia/Kuala_Lumpur"},
            client,
        )
        self.assertEqual(site_response.status_code, 201, site_response.content)
        site = site_response.json()["site"]
        source_response = self.post_json(
            "/api/operation-sources",
            {"site_id": site["id"], "name": "Front entrance", "kind": "recorded_video"},
            client,
        )
        self.assertEqual(source_response.status_code, 201, source_response.content)
        return site, source_response.json()["source"]

    def test_operation_is_bound_to_one_owned_site_and_source_and_stays_private(self):
        site, source = self.create_scope()
        response = self.post_json(
            "/api/operations",
            {"name": "Shop entrance", "site_id": site["id"], "source_id": source["id"]},
        )
        self.assertEqual(response.status_code, 201, response.content)
        operation = response.json()["operation"]
        self.assertEqual(operation["site_id"], site["id"])
        self.assertEqual(operation["source_id"], source["id"])
        self.assertEqual(operation["visibility"], "private")
        self.assertEqual(self.client.get("/api/operations").json()["operations"], [operation])
        detail_url = f"/api/operations/{operation['id']}"
        self.assertEqual(self.client.get(detail_url).json()["operation"], operation)

        stranger = get_user_model().objects.create_user("scope-stranger")
        other = Client()
        other.force_login(stranger)
        self.assertEqual(other.get("/api/operation-sites").json()["sites"], [])
        self.assertEqual(other.get("/api/operation-sources").json()["sources"], [])
        self.assertEqual(other.get("/api/operations").json()["operations"], [])
        self.assertEqual(other.get(detail_url).status_code, 404)

        superuser = get_user_model().objects.create_superuser(
            "scope-admin", email="scope-admin@example.com", password="test-password"
        )
        admin_client = Client()
        admin_client.force_login(superuser)
        self.assertEqual(admin_client.get("/api/operations").json()["operations"], [])
        self.assertEqual(admin_client.get(detail_url).status_code, 404)

    def test_foreign_or_mismatched_site_source_pairs_are_rejected(self):
        first_site, first_source = self.create_scope()
        second_site_response = self.post_json(
            "/api/operation-sites", {"name": "Second site", "timezone_name": "UTC"}
        )
        self.assertEqual(second_site_response.status_code, 201, second_site_response.content)
        second_site = second_site_response.json()["site"]
        foreign_user = get_user_model().objects.create_user("scope-foreign")
        foreign_client = Client()
        foreign_client.force_login(foreign_user)
        foreign_site, foreign_source = self.create_scope(foreign_client)

        mismatch = self.post_json(
            "/api/operations",
            {"name": "Wrong site", "site_id": second_site["id"], "source_id": first_source["id"]},
        )
        self.assertEqual(mismatch.status_code, 400)
        hidden_site = self.post_json(
            "/api/operation-sources",
            {"site_id": foreign_site["id"], "name": "Foreign feed", "kind": "camera"},
        )
        self.assertEqual(hidden_site.status_code, 404)
        hidden_source = self.post_json(
            "/api/operations",
            {"name": "Foreign feed", "site_id": foreign_site["id"], "source_id": foreign_source["id"]},
        )
        self.assertEqual(hidden_source.status_code, 404)
        self.assertEqual(self.client.get("/api/operations").json()["operations"], [])

    def test_scope_inputs_are_validated_and_cannot_enable_sharing_or_store_endpoints(self):
        invalid_timezone = self.post_json(
            "/api/operation-sites", {"name": "Bad timezone", "timezone_name": "Mars/Olympus"}
        )
        self.assertEqual(invalid_timezone.status_code, 400)
        self.assertEqual(self.client.post(
            "/api/operation-sites",
            data=json.dumps({"name": "Bad site", "timezone_name": "UTC", "public": True}),
            content_type="application/json",
        ).status_code, 400)
        site, _ = self.create_scope()
        bad_source = self.post_json(
            "/api/operation-sources",
            {"site_id": site["id"], "name": "Public URL", "kind": "camera", "url": "http://127.0.0.1/"},
        )
        self.assertEqual(bad_source.status_code, 400)
        bad_operation = self.post_json(
            "/api/operations",
            {"name": "Shared", "site_id": site["id"], "source_id": "not-a-uuid", "visibility": "public"},
        )
        self.assertEqual(bad_operation.status_code, 400)

    def test_existing_inspections_remain_private(self):
        inspection = Inspection.objects.create(
            created_by=self.owner, template="corrosion", title="Private inspection", status="failed"
        )
        other = Client()
        other.force_login(get_user_model().objects.create_user("inspection-stranger"))
        self.assertEqual(other.get(f"/api/inspections/{inspection.id}").status_code, 404)

    def test_non_owner_cannot_read_or_write_another_users_draft_or_recording(self):
        from unittest.mock import patch

        from .models import OperationDraft, OperationRecording

        draft = OperationDraft.objects.create(created_by=self.owner, name="Private door")
        OperationRecording.objects.create(
            draft=draft, storage_key="operation-recordings/private/clip.mp4", filename="clip.mp4",
            sha256="a" * 64, size_bytes=100, width=640, height=480, duration_seconds=12.5,
        )
        stranger = Client()
        stranger.force_login(get_user_model().objects.create_user("draft-stranger"))
        draft_url = f"/api/operation-drafts/{draft.id}"
        recording_url = f"{draft_url}/recording"
        self.assertEqual(stranger.get(draft_url).status_code, 404)
        self.assertEqual(stranger.put(
            draft_url, json.dumps({"name": "Stolen", "line": None, "revision": 1}),
            content_type="application/json",
        ).status_code, 404)
        self.assertEqual(stranger.delete(draft_url).status_code, 404)
        self.assertEqual(stranger.get(recording_url).status_code, 405)  # metadata is only returned with the owner-filtered draft
        self.assertEqual(stranger.post(recording_url, {"revision": "1"}).status_code, 404)
        self.assertEqual(stranger.delete(
            recording_url, json.dumps({"revision": 1}), content_type="application/json"
        ).status_code, 404)
        with patch("projects.recording_views.default_storage.open") as storage_open:
            response = stranger.get(f"{draft_url}/recording/content")
        self.assertEqual(response.status_code, 404)
        storage_open.assert_not_called()

    def test_metric_export_and_publication_paths_are_unavailable(self):
        site, source = self.create_scope()
        operation_response = self.post_json(
            "/api/operations", {"name": "Unreleased", "site_id": site["id"], "source_id": source["id"]}
        )
        self.assertEqual(operation_response.status_code, 201, operation_response.content)
        operation_id = operation_response.json()["operation"]["id"]
        for suffix in ("metrics", "exports", "publish"):
            with self.subTest(suffix=suffix):
                url = f"/api/operations/{operation_id}/{suffix}"
                self.assertEqual(self.client.get(url).status_code, 404)
                self.assertEqual(self.client.post(url, data="{}", content_type="application/json").status_code, 404)

    def test_draft_is_bound_to_one_owned_operation_and_cannot_be_reassigned(self):
        from .models import OperationRecording

        site, source = self.create_scope()
        operation_response = self.post_json(
            "/api/operations", {"name": "Door A", "site_id": site["id"], "source_id": source["id"]}
        )
        self.assertEqual(operation_response.status_code, 201, operation_response.content)
        operation_id = operation_response.json()["operation"]["id"]
        draft_response = self.post_json(
            "/api/operation-drafts", {"name": "Door A draft", "line": None, "operation_id": operation_id}
        )
        self.assertEqual(draft_response.status_code, 201, draft_response.content)
        draft = draft_response.json()["draft"]
        self.assertEqual(draft["operation_id"], operation_id)
        OperationRecording.objects.create(
            draft_id=draft["id"], storage_key="operation-recordings/private/door-a.mp4", filename="door-a.mp4",
            sha256="b" * 64, size_bytes=100, width=640, height=480, duration_seconds=12.5,
        )
        self.assertEqual(self.client.get("/api/operation-drafts").json()["drafts"][0]["operation_id"], operation_id)

        other = Client()
        other.force_login(get_user_model().objects.create_user("scope-other-operation"))
        other_site, other_source = self.create_scope(other)
        other_operation = self.post_json(
            "/api/operations",
            {"name": "Other door", "site_id": other_site["id"], "source_id": other_source["id"]},
            other,
        )
        self.assertEqual(other_operation.status_code, 201, other_operation.content)
        draft_url = f"/api/operation-drafts/{draft['id']}"
        self.assertEqual(other.get(draft_url).status_code, 404)
        self.assertEqual(other.get(f"{draft_url}/recording/content").status_code, 404)
        rejected = self.post_json(
            "/api/operation-drafts",
            {"name": "Wrong owner", "line": None, "operation_id": other_operation.json()["operation"]["id"]},
        )
        self.assertEqual(rejected.status_code, 404)
        updated = self.client.put(
            draft_url,
            json.dumps({"name": "Move draft", "line": None, "revision": draft["revision"], "operation_id": other_operation.json()["operation"]["id"]}),
            content_type="application/json",
        )
        self.assertEqual(updated.status_code, 400)
        self.assertEqual(self.client.get(draft_url).json()["draft"]["operation_id"], operation_id)

    def test_viewer_cannot_create_scoped_records_and_csrf_is_enforced(self):
        from .tests import grant

        viewer = get_user_model().objects.create_user("scope-viewer")
        grant(viewer, "viewer")
        viewer_client = Client()
        viewer_client.force_login(viewer)
        denied = self.post_json("/api/operation-sites", {"name": "Nope", "timezone_name": "UTC"}, viewer_client)
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(self.client.get("/api/operation-sites").json()["sites"], [])

        locked = Client(enforce_csrf_checks=True)
        locked.force_login(self.owner)
        response = self.post_json("/api/operation-sites", {"name": "CSRF", "timezone_name": "UTC"}, locked)
        self.assertEqual(response.status_code, 403)
