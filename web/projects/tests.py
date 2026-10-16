from __future__ import annotations

import hashlib
import json
import tempfile
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from PIL import Image


class SignedInMixin:
    """Every console route requires a user; sign one in before each test."""

    def sign_in(self) -> None:
        self.user = get_user_model().objects.create_user("operator")
        self.client.force_login(self.user)

    def csrf_client(self) -> Client:
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        return client


class ProjectWorkspaceTests(SignedInMixin, TestCase):
    def setUp(self) -> None:
        self.sign_in()

    def post_project(self, **overrides: object):
        payload = {
            "name": "Packaging line",
            "modality": "image",
            "task": "Object detection",
            "labels": ["damaged", "normal"],
        }
        payload.update(overrides)
        return self.client.post(
            "/api/projects",
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_dashboard_renders_project_creation_form_and_csrf_cookie(self) -> None:
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="create-project-form"')
        self.assertContains(response, 'id="project-modality"')
        self.assertIn("csrftoken", response.cookies)

    def test_dashboard_exposes_image_import_controls(self) -> None:
        response = self.client.get("/")

        self.assertContains(response, 'id="image-upload"')
        self.assertContains(response, 'accept="image/jpeg,image/png,image/webp"')
        self.assertContains(response, 'id="annotation-dialog"')
        self.assertContains(response, 'id="keyboard-box-form"')
        self.assertContains(response, 'id="annotation-zoom-in"')
        self.assertContains(response, 'id="annotation-zoom-out"')
        self.assertContains(response, 'id="annotation-zoom-reset"')
        self.assertContains(response, 'id="annotation-undo"')
        self.assertContains(response, 'id="annotation-redo"')
        self.assertContains(response, 'aria-label="Zoomable image annotation workspace"')
        self.assertContains(response, "Ctrl/Cmd+Z")
        self.assertContains(response, "Shift+arrow")

    def test_create_and_list_image_project(self) -> None:
        created = self.post_project()

        self.assertEqual(created.status_code, 201)
        project = created.json()["project"]
        self.assertEqual(project["name"], "Packaging line")
        self.assertEqual(project["modality"], "image")
        self.assertEqual(project["labels"], ["damaged", "normal"])
        self.assertTrue(project["id"])

        listed = self.client.get("/api/projects")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["projects"], [project])

    def test_create_custom_modality_project(self) -> None:
        created = self.post_project(
            modality="custom",
            custom_modality="thermal camera",
            task="Hotspot review",
        )

        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["project"]["custom_modality"], "thermal camera")

    def test_reject_unknown_modality(self) -> None:
        response = self.post_project(modality="mystery")

        self.assertEqual(response.status_code, 400)
        self.assertIn("modality", response.json()["error"].lower())

    def test_reject_blank_name_and_duplicate_labels(self) -> None:
        blank_name = self.post_project(name="   ")
        duplicate_labels = self.post_project(labels=["damaged", "DAMAGED"])

        self.assertEqual(blank_name.status_code, 400)
        self.assertEqual(duplicate_labels.status_code, 400)

    def test_reject_invalid_json(self) -> None:
        response = self.client.post(
            "/api/projects", data="{broken", content_type="application/json"
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("json", response.json()["error"].lower())

    def test_project_writes_require_csrf_token(self) -> None:
        client = self.csrf_client()
        payload = json.dumps(
            {"name": "CSRF check", "modality": "image", "task": "Detection"}
        )

        denied = client.post(
            "/api/projects", data=payload, content_type="application/json"
        )
        self.assertEqual(denied.status_code, 403)

        client.get("/")
        token = client.cookies["csrftoken"].value
        allowed = client.post(
            "/api/projects",
            data=payload,
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(allowed.status_code, 201)

        project_id = allowed.json()["project"]["id"]
        denied_delete = client.delete(f"/api/projects/{project_id}")
        self.assertEqual(denied_delete.status_code, 403)
        allowed_delete = client.delete(
            f"/api/projects/{project_id}", HTTP_X_CSRFTOKEN=token
        )
        self.assertEqual(allowed_delete.status_code, 200)

    def test_delete_project(self) -> None:
        created = self.post_project()
        project_id = created.json()["project"]["id"]

        deleted = self.client.delete(f"/api/projects/{project_id}")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json(), {"deleted": True})
        self.assertEqual(self.client.get("/api/projects").json()["projects"], [])
        self.assertEqual(self.client.delete(f"/api/projects/{project_id}").status_code, 404)


class ImageAssetTests(SignedInMixin, TestCase):
    def setUp(self) -> None:
        self.sign_in()
        self.media_directory = tempfile.TemporaryDirectory()
        self.media_settings = override_settings(MEDIA_ROOT=self.media_directory.name)
        self.media_settings.enable()
        self.addCleanup(self.media_settings.disable)
        self.addCleanup(self.media_directory.cleanup)
        response = self.client.post(
            "/api/projects",
            data=json.dumps(
                {
                    "name": "Image review",
                    "modality": "image",
                    "task": "Object detection",
                    "labels": ["damaged", "normal"],
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        self.project_id = response.json()["project"]["id"]

    @staticmethod
    def image_file(name: str = "inspection.png", image_format: str = "PNG") -> tuple[SimpleUploadedFile, bytes]:
        buffer = BytesIO()
        Image.new("RGB", (3, 2), color=(12, 90, 180)).save(buffer, format=image_format)
        content = buffer.getvalue()
        return SimpleUploadedFile(name, content, content_type="image/png"), content

    def upload_url(self) -> str:
        return f"/api/projects/{self.project_id}/assets"

    def create_asset(self) -> dict[str, object]:
        upload, _ = self.image_file()
        response = self.client.post(self.upload_url(), data={"images": upload})
        self.assertEqual(response.status_code, 201)
        return response.json()["assets"][0]

    def annotation_url(self, asset_id: str) -> str:
        return f"{self.upload_url()}/{asset_id}/annotations"

    @staticmethod
    def annotation_payload(**overrides: object) -> dict[str, object]:
        return {
            "label": "damaged",
            "x": 0.1,
            "y": 0.2,
            "width": 0.3,
            "height": 0.4,
            **overrides,
        }

    def test_upload_stores_verified_image_bytes_and_provenance(self) -> None:
        upload, content = self.image_file("C:\\private\\inspection.png")

        response = self.client.post(self.upload_url(), data={"images": upload})

        self.assertEqual(response.status_code, 201)
        asset = response.json()["assets"][0]
        self.assertEqual(asset["filename"], "inspection.png")
        self.assertEqual(asset["sha256"], hashlib.sha256(content).hexdigest())
        self.assertEqual((asset["width"], asset["height"]), (3, 2))
        self.assertEqual(asset["size_bytes"], len(content))
        stored_file = Path(
            self.media_directory.name,
            "projects",
            self.project_id,
            "assets",
            f"{asset['id']}.png",
        )
        self.assertTrue(stored_file.is_file())

        listing = self.client.get(self.upload_url())
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["assets"][0]["id"], asset["id"])

        content_response = self.client.get(asset["content_url"])
        self.assertEqual(content_response.status_code, 200)
        self.assertEqual(b"".join(content_response.streaming_content), content)
        self.assertEqual(content_response["Content-Type"], "image/png")

    def test_rejects_corrupt_and_unsupported_images_without_partial_import(self) -> None:
        valid, _ = self.image_file()
        corrupt = SimpleUploadedFile("bad.png", b"not an image", content_type="image/png")
        unsupported, _ = self.image_file("animation.gif", "GIF")

        response = self.client.post(
            self.upload_url(), data={"images": [valid, corrupt, unsupported]}
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get(self.upload_url()).json()["assets"], [])
        self.assertEqual(list(Path(self.media_directory.name).rglob("*")), [])

    def test_rejects_non_image_projects(self) -> None:
        project = self.client.post(
            "/api/projects",
            data=json.dumps({"name": "Notes", "modality": "text", "task": "Classification"}),
            content_type="application/json",
        ).json()["project"]
        upload, _ = self.image_file()

        response = self.client.post(
            f"/api/projects/{project['id']}/assets", data={"images": upload}
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())

    def test_rejects_an_image_over_the_per_file_limit(self) -> None:
        upload, _ = self.image_file()
        with patch("projects.views.MAX_IMAGE_BYTES", 1):
            response = self.client.post(self.upload_url(), data={"images": upload})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get(self.upload_url()).json()["assets"], [])

    def test_rejects_batches_over_the_total_size_limit(self) -> None:
        upload, _ = self.image_file()
        with patch("projects.views.MAX_IMAGE_BATCH_BYTES", 1):
            response = self.client.post(self.upload_url(), data={"images": upload})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get(self.upload_url()).json()["assets"], [])

    def test_rejects_batches_over_the_file_count_limit(self) -> None:
        first, _ = self.image_file("first.png")
        second, _ = self.image_file("second.png")
        with patch("projects.views.MAX_IMAGE_UPLOADS", 1):
            response = self.client.post(
                self.upload_url(), data={"images": [first, second]}
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get(self.upload_url()).json()["assets"], [])

    def test_rejects_images_over_the_pixel_limit(self) -> None:
        upload, _ = self.image_file()
        with patch("projects.views.MAX_IMAGE_PIXELS", 5):
            response = self.client.post(self.upload_url(), data={"images": upload})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get(self.upload_url()).json()["assets"], [])

    def test_upload_requires_csrf(self) -> None:
        client = self.csrf_client()
        client.get("/")
        upload, _ = self.image_file()

        response = client.post(self.upload_url(), data={"images": upload})

        self.assertEqual(response.status_code, 403)

    def test_annotation_create_update_and_delete_require_csrf(self) -> None:
        asset = self.create_asset()
        client = self.csrf_client()
        client.get("/")
        token = client.cookies["csrftoken"].value
        endpoint = self.annotation_url(asset["id"])
        payload = json.dumps(self.annotation_payload())

        denied_create = client.post(endpoint, data=payload, content_type="application/json")
        self.assertEqual(denied_create.status_code, 403)
        created = client.post(
            endpoint,
            data=payload,
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(created.status_code, 201)
        annotation_id = created.json()["annotation"]["id"]
        detail_url = f"{endpoint}/{annotation_id}"

        denied_update = client.patch(
            detail_url, data=payload, content_type="application/json"
        )
        self.assertEqual(denied_update.status_code, 403)
        updated = client.patch(
            detail_url,
            data=payload,
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(updated.status_code, 200)

        denied_delete = client.delete(detail_url)
        self.assertEqual(denied_delete.status_code, 403)
        deleted = client.delete(detail_url, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(deleted.status_code, 200)

    def test_content_endpoint_is_scoped_to_the_project(self) -> None:
        upload, _ = self.image_file()
        asset = self.client.post(self.upload_url(), data={"images": upload}).json()["assets"][0]
        other_project = self.client.post(
            "/api/projects",
            data=json.dumps({"name": "Other image project", "modality": "image", "task": "Detection"}),
            content_type="application/json",
        ).json()["project"]

        response = self.client.get(
            f"/api/projects/{other_project['id']}/assets/{asset['id']}/content"
        )

        self.assertEqual(response.status_code, 404)

    def test_project_delete_removes_imported_files(self) -> None:
        upload, _ = self.image_file()
        asset = self.client.post(self.upload_url(), data={"images": upload}).json()["assets"][0]
        stored_file = Path(
            self.media_directory.name,
            "projects",
            self.project_id,
            "assets",
            f"{asset['id']}.png",
        )

        response = self.client.delete(f"/api/projects/{self.project_id}")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(stored_file.exists())

    def test_delete_asset_removes_the_local_file(self) -> None:
        upload, _ = self.image_file()
        asset = self.client.post(self.upload_url(), data={"images": upload}).json()["assets"][0]
        stored_file = Path(
            self.media_directory.name,
            "projects",
            self.project_id,
            "assets",
            f"{asset['id']}.png",
        )
        self.assertTrue(stored_file.exists())

        response = self.client.delete(f"{self.upload_url()}/{asset['id']}")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(stored_file.exists())
        self.assertEqual(self.client.get(self.upload_url()).json()["assets"], [])

    def test_create_and_list_normalized_bounding_box(self) -> None:
        asset = self.create_asset()
        response = self.client.post(
            self.annotation_url(asset["id"]),
            data=json.dumps(self.annotation_payload()),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        annotation = response.json()["annotation"]
        self.assertEqual(annotation["label"], "damaged")
        self.assertAlmostEqual(annotation["x"], 0.1)
        self.assertAlmostEqual(annotation["y"], 0.2)
        self.assertAlmostEqual(annotation["width"], 0.3)
        self.assertAlmostEqual(annotation["height"], 0.4)

        listing = self.client.get(self.annotation_url(asset["id"]))
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["annotations"], [annotation])

    def test_update_and_delete_bounding_box(self) -> None:
        asset = self.create_asset()
        created = self.client.post(
            self.annotation_url(asset["id"]),
            data=json.dumps(self.annotation_payload()),
            content_type="application/json",
        ).json()["annotation"]
        updated = self.client.patch(
            f"{self.annotation_url(asset['id'])}/{created['id']}",
            data=json.dumps(
                self.annotation_payload(label="normal", x=0.25, width=0.5, height=0.5)
            ),
            content_type="application/json",
        )

        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["annotation"]["label"], "normal")
        self.assertAlmostEqual(updated.json()["annotation"]["x"], 0.25)
        self.assertAlmostEqual(updated.json()["annotation"]["width"], 0.5)
        self.assertAlmostEqual(updated.json()["annotation"]["height"], 0.5)

        deleted = self.client.delete(
            f"{self.annotation_url(asset['id'])}/{created['id']}"
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(self.client.get(self.annotation_url(asset["id"])).json()["annotations"], [])

    def test_rejects_unknown_labels_and_boxes_outside_the_image(self) -> None:
        asset = self.create_asset()
        invalid_payloads = [
            self.annotation_payload(label="not-configured"),
            self.annotation_payload(x=0.8, width=0.3),
            self.annotation_payload(y=-0.1),
            self.annotation_payload(width=0),
            self.annotation_payload(width=True),
            self.annotation_payload(height=float("nan")),
            {**self.annotation_payload(), "unexpected": "field"},
        ]

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                response = self.client.post(
                    self.annotation_url(asset["id"]),
                    data=json.dumps(payload),
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get(self.annotation_url(asset["id"])).json()["annotations"], [])

    def test_annotation_cannot_be_attached_to_an_asset_from_another_project(self) -> None:
        asset = self.create_asset()
        other_project = self.client.post(
            "/api/projects",
            data=json.dumps({"name": "Other image project", "modality": "image", "task": "Detection"}),
            content_type="application/json",
        ).json()["project"]

        response = self.client.post(
            f"/api/projects/{other_project['id']}/assets/{asset['id']}/annotations",
            data=json.dumps(self.annotation_payload()),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 404)
