"""Industry people can mark areas on a picture (parking bays are the example) before Athena looks."""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image

from .models import Inspection
from .vision import catalog, client, regions as region_lib

STATIC = Path(__file__).resolve().parent / "static" / "projects"
TEMPLATES = Path(__file__).resolve().parent / "templates" / "projects"


def png_upload(name: str = "carpark.png") -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", (80, 60), color=(40, 80, 40)).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


class ParseMarkedAreasTests(TestCase):
    def test_empty_and_missing_are_no_marks(self) -> None:
        self.assertEqual(region_lib.parse_regions(None), [])
        self.assertEqual(region_lib.parse_regions(""), [])
        self.assertEqual(region_lib.parse_regions("[]"), [])

    def test_parking_bays_are_kept_as_fractions_with_plain_labels(self) -> None:
        marks = region_lib.parse_regions(
            json.dumps(
                [
                    {"x": 0.1, "y": 0.2, "w": 0.15, "h": 0.3, "label": "Bay 1"},
                    {"x": 0.4, "y": 0.2, "w": 0.15, "h": 0.3},
                ]
            )
        )
        self.assertEqual(len(marks), 2)
        self.assertEqual(marks[0]["label"], "Bay 1")
        self.assertEqual(marks[1]["label"], "2")
        self.assertAlmostEqual(marks[0]["x"], 0.1)
        self.assertAlmostEqual(marks[0]["w"], 0.15)

    def test_a_box_outside_the_picture_or_too_many_boxes_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            region_lib.parse_regions(json.dumps([{"x": 0.9, "y": 0.1, "w": 0.3, "h": 0.2}]))
        with self.assertRaises(ValueError):
            region_lib.parse_regions(json.dumps([{"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2}] * 25))

    def test_a_script_tag_in_the_name_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            region_lib.parse_regions(
                json.dumps([{"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2, "label": "<script>alert(1)</script>"}])
            )

    def test_an_oversized_payload_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            region_lib.parse_regions("[" + ("0" * (region_lib.MAX_RAW_BYTES + 10)))


class PromptIncludesMarkedAreasTests(TestCase):
    def test_the_model_is_told_the_person_marked_parking_bays(self) -> None:
        template = catalog.BY_KEY["parking"]
        pictures = [{"data_url": "data:image/png;base64,xx", "width": 80, "height": 60}]
        marks = [{"x": 0.1, "y": 0.2, "w": 0.15, "h": 0.3, "label": "Bay 1", "image": 1}]
        messages = client.build_messages(template, "", pictures, "photo", regions=marks)
        text = messages[1]["content"][0]["text"]
        self.assertIn("marked these areas", text.lower())
        self.assertIn("Bay 1", text)
        self.assertIn("0.10", text)
        self.assertIn("parking", text.lower())


class StoreMarkedAreasTests(TestCase):
    def setUp(self) -> None:
        user = get_user_model().objects.create_user("operator")
        self.client.force_login(user)

    def _post(self, regions: str):
        vision = {**settings.VISION, "BASE_URL": "http://127.0.0.1:9/v1", "MODEL": "qwen2.5vl:7b"}
        with override_settings(VISION=vision), patch("projects.vision_views.service.start"):
            return self.client.post(
                "/api/inspections",
                data={
                    "template": "parking",
                    "instructions": "",
                    "images": png_upload(),
                    "regions": regions,
                },
            )

    def test_create_inspection_keeps_the_drawn_bays(self) -> None:
        response = self._post(json.dumps([{"x": 0.12, "y": 0.2, "w": 0.18, "h": 0.25, "label": "Bay 1"}]))
        self.assertEqual(response.status_code, 201, response.content[:400])
        inspection = Inspection.objects.get(id=response.json()["inspection"]["id"])
        self.assertEqual(inspection.regions[0]["label"], "Bay 1")
        self.assertAlmostEqual(inspection.regions[0]["x"], 0.12)
        detail = self.client.get(f"/api/inspections/{inspection.id}")
        self.assertEqual(detail.json()["inspection"]["regions"][0]["label"], "Bay 1")

    def test_a_bad_box_saves_nothing(self) -> None:
        response = self._post(json.dumps([{"x": 2, "y": 0, "w": 0.1, "h": 0.1}]))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Inspection.objects.count(), 0)

    def test_csrf_is_required(self) -> None:
        from django.test import Client

        vision = {**settings.VISION, "BASE_URL": "http://127.0.0.1:9/v1", "MODEL": "qwen2.5vl:7b"}
        locked = Client(enforce_csrf_checks=True)
        locked.force_login(get_user_model().objects.get(username="operator"))
        with override_settings(VISION=vision), patch("projects.vision_views.service.start"):
            response = locked.post(
                "/api/inspections",
                data={"template": "parking", "images": png_upload(), "regions": "[]"},
            )
        self.assertEqual(response.status_code, 403)

    def test_a_viewer_cannot_create_an_inspection(self) -> None:
        from .tests import grant

        viewer = get_user_model().objects.create_user("watcher")
        grant(viewer, "viewer")
        client = self.client_class()
        client.force_login(viewer)
        vision = {**settings.VISION, "BASE_URL": "http://127.0.0.1:9/v1", "MODEL": "qwen2.5vl:7b"}
        with override_settings(VISION=vision), patch("projects.vision_views.service.start"):
            response = client.post(
                "/api/inspections",
                data={"template": "parking", "images": png_upload(), "regions": "[]"},
            )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Inspection.objects.count(), 0)

    def test_another_person_cannot_open_the_report(self) -> None:
        created = self._post("[]")
        self.assertEqual(created.status_code, 201)
        url = f"/api/inspections/{created.json()['inspection']['id']}"
        other = get_user_model().objects.create_user("other")
        stranger = self.client_class()
        stranger.force_login(other)
        self.assertEqual(stranger.get(url).status_code, 404)


class MarkAreasUiTests(TestCase):
    def test_new_inspection_page_has_a_place_to_draw_boxes(self) -> None:
        html = (TEMPLATES / "inspect_new.html").read_text()
        self.assertIn("mark-stage", html)
        self.assertIn("data-testid=\"mark-canvas\"", html)
        js = (STATIC / "inspect.js").read_text()
        self.assertIn("state.regions", js)
        self.assertIn("regions", js)
        self.assertIn("mark-frame", js)
