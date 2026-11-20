"""T16: versioned geometry and camera calibration.

What is under test, honestly: the scale reference stores what was entered
(two normalized points, a real-world distance, a unit, a provenance note) and
derives pixels-per-unit from the view version's dimensions — it is an
approximation whose limit is stated in every answer, never a homography; the
rotation remap is a pure coordinate transform that round-trips through all
four 90-degree steps and preserves the line's name, side labels and ordered
direction; every non-remappable format change (dimensions, flips) is refused
with a plain message and nothing is stored; a moved view (a newer recording
with materially different dimensions or duration) surfaces a review request
with the diff shown on both the source-views listing and the draft, never a
silent keep or drop; old view versions and their geometry stay immutable; and
the existing draft-invalidation-on-line-change behaviour survives.
"""

from __future__ import annotations

import json

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from . import calibration
from .models import (
    GeometryVersion,
    Operation,
    OperationRecording,
    OperationSource,
    OperationSourceRecording,
    Site,
    SourceScaleReference,
    SourceViewVersion,
)


class CalibrationBase(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("t16-owner")
        self.client.force_login(self.owner)
        self.site = Site.objects.create(created_by=self.owner, name="Shop")
        self.source = OperationSource.objects.create(site=self.site, name="Front", kind="recorded_video")
        self.operation = Operation.objects.create(
            created_by=self.owner, site=self.site, source=self.source, name="Door"
        )
        self.line = {
            "start": [0.2, 0.5],
            "end": [0.8, 0.5],
            "name": "Front door line",
            "positive_label": "Shop",
            "negative_label": "Street",
        }

    def post(self, path, payload, client=None, status=201):
        response = (client or self.client).post(path, json.dumps(payload), content_type="application/json")
        self.assertEqual(response.status_code, status, response.content)
        return response.json()

    def make_view(self, *, rotation=0, width=640, height=480, client=None, scale_reference=None, operation=None):
        payload = {
            "configuration": {
                "width": width,
                "height": height,
                "rotation": rotation,
                "horizontal_flip": False,
                "vertical_flip": False,
            }
        }
        if scale_reference is not None:
            payload["scale_reference"] = scale_reference
        return self.post(
            f"/api/operations/{(operation or self.operation).id}/source-views", payload, client=client
        )["source_view"]

    def make_geometry(self, view, line=None, operation=None, status=201):
        return self.post(
            f"/api/operations/{(operation or self.operation).id}/geometry-versions",
            {"source_view_version_id": view["id"], "geometry": {"line": line or self.line}},
            status=status,
        )["geometry_version"]


class ScaleReferenceTests(CalibrationBase):
    """The linear scale reference: storage, derivation, validation, limits."""

    REFERENCE = {
        "point_a": [0.1, 0.1],
        "point_b": [0.9, 0.1],
        "real_world_distance": 4.0,
        "real_world_unit": "m",
        "provenance": "Doorway width, tape measure, 2026-11-19.",
    }

    def test_scale_reference_is_stored_against_the_view_version_with_its_provenance(self):
        view = self.make_view(scale_reference=self.REFERENCE)
        row = SourceScaleReference.objects.get(source_view_version_id=view["id"])
        self.assertEqual(row.real_world_distance, 4.0)
        self.assertEqual(row.real_world_unit, "m")
        self.assertEqual(row.provenance, self.REFERENCE["provenance"])
        self.assertEqual(row.point_a, [0.1, 0.1])
        # The stored answer reports the derived value and the stated limit.
        calibration_data = view["calibration"]
        self.assertEqual(calibration_data["scale_reference"]["pixels_per_unit"], 128.0)  # 512px / 4m
        self.assertIn("approximation", calibration_data["scale_reference"]["limit"])
        self.assertIn("perpendicular", calibration_data["scale_reference"]["limit"])

    def test_pixels_per_unit_is_derived_from_the_view_dimensions_not_stored_apart(self):
        view = self.make_view(width=1280, height=720, scale_reference=self.REFERENCE)
        # 80% of 1280 = 1024 pixels over 4 metres.
        self.assertEqual(view["calibration"]["scale_reference"]["pixels_per_unit"], 256.0)
        # There is no stored pixels-per-unit column; the value only exists derived
        # (the row stores the inputs, never the result).
        row = SourceScaleReference.objects.get(source_view_version_id=view["id"])
        self.assertFalse(hasattr(row, "pixels_per_unit"))
        self.assertEqual([f.name for f in row._meta.fields].count("pixels_per_unit"), 0)

    def test_view_version_without_a_reference_reports_none_and_still_states_the_limit(self):
        view = self.make_view()
        self.assertIsNone(view["calibration"]["scale_reference"])
        self.assertIn("approximation", view["calibration"]["scale_limit"])
        self.assertEqual(view["calibration"]["pixel_aspect_ratio"], 640 / 480)
        self.assertEqual(view["calibration"]["rotation_degrees"], 0)

    def test_scale_reference_validation_refuses_malformed_input_storing_nothing(self):
        cases = [
            ("missing key", {k: v for k, v in self.REFERENCE.items() if k != "real_world_distance"}),
            ("zero distance", {**self.REFERENCE, "real_world_distance": 0}),
            ("negative distance", {**self.REFERENCE, "real_world_distance": -2}),
            ("bad unit", {**self.REFERENCE, "real_world_unit": "furlongs"}),
            ("blank provenance", {**self.REFERENCE, "provenance": "   "}),
            ("points too close", {**self.REFERENCE, "point_b": [0.1 + 1e-7, 0.1 + 1e-7]}),
            ("point out of range", {**self.REFERENCE, "point_a": [1.5, 0.1]}),
            ("point wrong type", {**self.REFERENCE, "point_a": "left"}),
            ("boolean distance", {**self.REFERENCE, "real_world_distance": True}),
            ("non-finite distance", {**self.REFERENCE, "real_world_distance": float("inf")}),
        ]
        for label, reference in cases:
            with self.subTest(case=label):
                response = self.client.post(
                    f"/api/operations/{self.operation.id}/source-views",
                    json.dumps(
                        {
                            "configuration": {
                                "width": 640,
                                "height": 480,
                                "rotation": 0,
                                "horizontal_flip": False,
                                "vertical_flip": False,
                            },
                            "scale_reference": reference,
                        }
                    ),
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 400, label)
                self.assertIn(b"alibration", response.content + b"Calibration")
        self.assertFalse(SourceScaleReference.objects.exists())

    def test_scale_reference_is_owner_scoped_and_visible_only_with_its_view(self):
        view = self.make_view(scale_reference=self.REFERENCE)
        stranger = Client()
        stranger.force_login(get_user_model().objects.create_user("t16-stranger"))
        self.assertEqual(
            stranger.post(
                f"/api/operations/{self.operation.id}/source-views",
                json.dumps({"configuration": view["configuration"]}),
                content_type="application/json",
            ).status_code,
            404,
        )
        listing = self.client.get(f"/api/operations/{self.operation.id}/source-views").json()
        self.assertEqual(listing["source_views"][0]["calibration"]["scale_reference"]["pixels_per_unit"], 128.0)

    def test_units_are_supported_and_kept_distinct(self):
        for unit in ("m", "cm", "ft"):
            with self.subTest(unit=unit):
                view = self.make_view(scale_reference={**self.REFERENCE, "real_world_unit": unit})
                self.assertEqual(view["calibration"]["scale_reference"]["real_world_unit"], unit)


class RotationRemapTests(CalibrationBase):
    """The explicit 90-degree rotation remap: pure, round-tripping, refusal-honest."""

    def test_remap_creates_a_new_geometry_version_pinned_to_the_new_view(self):
        old_view = self.make_view(rotation=0)
        old_geometry = self.make_geometry(old_view)
        new_view = self.make_view(rotation=90)
        remapped = self.post(
            f"/api/operations/{self.operation.id}/geometry-versions",
            {"remap": {"from_geometry_version_id": old_geometry["id"], "to_source_view_version_id": new_view["id"]}},
        )
        geometry = remapped["geometry_version"]
        self.assertEqual(geometry["source_view_version_id"], new_view["id"])
        self.assertEqual(geometry["version"], 2)
        # 0 -> 90 clockwise: (x, y) becomes (1 - y, x).
        self.assertEqual(geometry["geometry"]["line"]["start"], [0.5, 0.2])
        self.assertEqual(geometry["geometry"]["line"]["end"], [0.5, 0.8])
        # The line's identity survives untouched.
        self.assertEqual(geometry["geometry"]["line"]["name"], self.line["name"])
        self.assertEqual(geometry["geometry"]["line"]["positive_label"], "Shop")
        self.assertEqual(geometry["geometry"]["line"]["negative_label"], "Street")
        self.assertEqual(remapped["remap"]["rotation_step"], 90)
        self.assertIn("check both endpoints", remapped["remap"]["note"])
        # The old geometry version is untouched and still pinned to the old view.
        old = GeometryVersion.objects.get(pk=old_geometry["id"])
        self.assertEqual(old.geometry["line"], self.line)
        self.assertEqual(str(old.source_view_version_id), old_view["id"])

    def test_remap_round_trips_through_all_four_rotation_steps(self):
        view = self.make_view(rotation=0)
        geometry = self.make_geometry(view)
        current_line = self.line
        current_id = geometry["id"]
        for step in (90, 180, 270, 0):
            new_view = self.make_view(rotation=step)
            result = self.post(
                f"/api/operations/{self.operation.id}/geometry-versions",
                {"remap": {"from_geometry_version_id": current_id, "to_source_view_version_id": new_view["id"]}},
            )
            current_id = result["geometry_version"]["id"]
            current_line = result["geometry_version"]["geometry"]["line"]
        for key in ("start", "end"):
            for a, b in zip(current_line[key], self.line[key]):
                self.assertAlmostEqual(a, b, places=9)
        self.assertEqual(current_line["name"], self.line["name"])
        self.assertEqual(current_line["positive_label"], self.line["positive_label"])

    def test_remap_refuses_same_rotation_and_same_view(self):
        view = self.make_view(rotation=0)
        geometry = self.make_geometry(view)
        same_view = self.make_view(rotation=0)
        response = self.client.post(
            f"/api/operations/{self.operation.id}/geometry-versions",
            json.dumps({"remap": {"from_geometry_version_id": geometry["id"], "to_source_view_version_id": same_view["id"]}}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"same format", response.content)
        self.assertEqual(GeometryVersion.objects.count(), 1)

    def test_remap_refuses_changed_dimensions_with_a_plain_message(self):
        old_view = self.make_view(width=1920, height=1080)
        geometry = self.make_geometry(old_view)
        new_view = self.make_view(width=1280, height=720)
        response = self.client.post(
            f"/api/operations/{self.operation.id}/geometry-versions",
            json.dumps({"remap": {"from_geometry_version_id": geometry["id"], "to_source_view_version_id": new_view["id"]}}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn(b"redraw", response.content.lower())
        self.assertEqual(GeometryVersion.objects.count(), 1)

    def test_remap_refuses_changed_flips_with_a_plain_message(self):
        old_view = self.make_view(rotation=0)
        geometry = self.make_geometry(old_view)
        new_view = self.post(
            f"/api/operations/{self.operation.id}/source-views",
            {
                "configuration": {
                    "width": 640,
                    "height": 480,
                    "rotation": 90,
                    "horizontal_flip": True,
                    "vertical_flip": False,
                }
            },
        )["source_view"]
        response = self.client.post(
            f"/api/operations/{self.operation.id}/geometry-versions",
            json.dumps({"remap": {"from_geometry_version_id": geometry["id"], "to_source_view_version_id": new_view["id"]}}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn(b"flip", response.content)
        self.assertEqual(GeometryVersion.objects.count(), 1)

    def test_remap_requires_both_ids_and_own_operation_geometry(self):
        view = self.make_view()
        geometry = self.make_geometry(view)
        for bad in (
            {"from_geometry_version_id": geometry["id"]},
            {"to_source_view_version_id": view["id"]},
            {"from_geometry_version_id": "not-a-uuid", "to_source_view_version_id": view["id"]},
            {"from_geometry_version_id": geometry["id"], "to_source_view_version_id": view["id"], "extra": 1},
        ):
            with self.subTest(bad=bad):
                response = self.client.post(
                    f"/api/operations/{self.operation.id}/geometry-versions",
                    json.dumps({"remap": bad}),
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 400)
        # A foreign operation's geometry cannot be remapped here.
        stranger = get_user_model().objects.create_user("t16-foreign")
        stranger_site = Site.objects.create(created_by=stranger, name="Far")
        stranger_source = OperationSource.objects.create(site=stranger_site, name="Cam", kind="camera")
        stranger_operation = Operation.objects.create(
            created_by=stranger, site=stranger_site, source=stranger_source, name="Far door"
        )
        stranger_view = self.make_view(operation=stranger_operation, client=_client(stranger))
        stranger_client = _client(stranger)
        stranger_geometry = self.post(
            f"/api/operations/{stranger_operation.id}/geometry-versions",
            {"source_view_version_id": stranger_view["id"], "geometry": {"line": self.line}},
            client=stranger_client,
        )["geometry_version"]
        response = self.client.post(
            f"/api/operations/{self.operation.id}/geometry-versions",
            json.dumps(
                {
                    "remap": {
                        "from_geometry_version_id": stranger_geometry["id"],
                        "to_source_view_version_id": self.make_view(rotation=90)["id"],
                    }
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(GeometryVersion.objects.filter(operation=self.operation).count(), 1)

    def test_rotated_view_configurations_are_still_validated_by_the_existing_rules(self):
        response = self.client.post(
            f"/api/operations/{self.operation.id}/source-views",
            json.dumps(
                {
                    "configuration": {
                        "width": 640,
                        "height": 480,
                        "rotation": 45,
                        "horizontal_flip": False,
                        "vertical_flip": False,
                    }
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Rotation must be 0, 90, 180, or 270", response.content)


class MovedViewReviewTests(CalibrationBase):
    """The moved-view rule: material format differences ask for review, never silently act."""

    def _recording(self, *, width, height, duration, created_by=None):
        return OperationSourceRecording.objects.create(
            source=self.source,
            created_by=created_by or self.owner,
            storage_key=f"operation-source-recordings/{self.source.id}/test.mp4",
            filename="test.mp4",
            sha256="a" * 64,
            size_bytes=100,
            width=width,
            height=height,
            duration_seconds=duration,
        )

    def test_matching_recording_gets_no_review(self):
        self.make_view(width=640, height=480)
        self._recording(width=640, height=480, duration=12.0)
        listing = self.client.get(f"/api/operations/{self.operation.id}/source-views").json()
        self.assertIsNone(listing["view_review"])

    def test_material_dimension_change_flags_review_with_the_diff_shown(self):
        self.make_view(width=1920, height=1080)
        self._recording(width=1280, height=720, duration=12.0)
        listing = self.client.get(f"/api/operations/{self.operation.id}/source-views").json()
        review = listing["view_review"]
        self.assertIsNotNone(review)
        self.assertEqual(review["status"], "review")
        self.assertIn("may have moved", review["message"])
        self.assertEqual(review["changes"][0]["kind"], "dimensions")
        self.assertIn("1920", review["changes"][0]["detail"])
        self.assertIn("1280", review["changes"][0]["detail"])
        self.assertIn("saved counting line", review["next_step"])
        self.assertIn("save the line again", review["next_step"])

    def test_material_duration_change_flags_review(self):
        from django.utils import timezone as tz

        self.make_view(width=640, height=480)
        first = self._recording(width=640, height=480, duration=4.0)
        later = OperationSourceRecording.objects.create(
            source=self.source,
            created_by=self.owner,
            storage_key=f"operation-source-recordings/{self.source.id}/test2.mp4",
            filename="test2.mp4",
            sha256="b" * 64,
            size_bytes=100,
            width=640,
            height=480,
            duration_seconds=30.0,
            created_at=tz.now(),
        )
        self.assertGreater(later.created_at, first.created_at)
        listing = self.client.get(f"/api/operations/{self.operation.id}/source-views").json()
        review = listing["view_review"]
        self.assertIsNotNone(review)
        self.assertEqual(review["changes"][0]["kind"], "duration")

    def test_small_rounding_differences_do_not_flag_a_move(self):
        self.make_view(width=1920, height=1080)
        self._recording(width=1922, height=1081, duration=12.1)
        listing = self.client.get(f"/api/operations/{self.operation.id}/source-views").json()
        self.assertIsNone(listing["view_review"])

    def test_aspect_ratio_change_flags_review_even_at_the_same_diagonal(self):
        # 640x480 (4:3) vs 720x405 (16:9-ish): both cross the material threshold.
        self.make_view(width=640, height=480)
        self._recording(width=720, height=405, duration=12.0)
        listing = self.client.get(f"/api/operations/{self.operation.id}/source-views").json()
        self.assertIsNotNone(listing["view_review"])

    def test_draft_carries_the_review_request(self):
        self.make_view(width=1920, height=1080)
        self._recording(width=1280, height=720, duration=12.0)
        draft = self.post(
            "/api/operation-drafts",
            {"operation_id": str(self.operation.id), "name": "Shop door", "line": self.line},
        )["draft"]
        self.assertIsNotNone(draft["view_review"])
        self.assertIn("may have moved", draft["view_review"]["message"])
        fetched = self.client.get(f"/api/operation-drafts/{draft['id']}").json()["draft"]
        self.assertIsNotNone(fetched["view_review"])
        # An unbound draft has no operation, so no review claim is made at all.
        unbound = self.post("/api/operation-drafts", {"name": "Loose", "line": self.line})["draft"]
        self.assertIsNone(unbound["view_review"])

    def test_no_recording_or_no_view_version_means_no_review(self):
        self.make_view(width=640, height=480)
        listing = self.client.get(f"/api/operations/{self.operation.id}/source-views").json()
        self.assertIsNone(listing["view_review"])
        other = get_user_model().objects.create_user("t16-noview")
        other_site = Site.objects.create(created_by=other, name="Empty")
        other_source = OperationSource.objects.create(site=other_site, name="None", kind="recorded_video")
        other_operation = Operation.objects.create(
            created_by=other, site=other_site, source=other_source, name="No view"
        )
        listing = self.client.get(f"/api/operations/{other_operation.id}/source-views")
        self.assertEqual(listing.status_code, 404)


class ImmutabilityAndInvalidationTests(CalibrationBase):
    """Old versions stay frozen; the existing draft invalidation survives T16's additions."""

    def test_old_view_versions_and_scale_references_stay_as_entered(self):
        view = self.make_view(
            scale_reference={
                "point_a": [0.1, 0.1],
                "point_b": [0.9, 0.1],
                "real_world_distance": 4.0,
                "real_world_unit": "m",
                "provenance": "Doorway width, tape measure.",
            }
        )
        changed = self.make_view(
            rotation=90,
            scale_reference={
                "point_a": [0.1, 0.1],
                "point_b": [0.9, 0.1],
                "real_world_distance": 5.0,
                "real_world_unit": "m",
                "provenance": "Re-measured after moving the camera.",
            },
        )
        self.assertNotEqual(view["id"], changed["id"])
        old_reference = SourceScaleReference.objects.get(source_view_version_id=view["id"])
        self.assertEqual(old_reference.real_world_distance, 4.0)
        with self.assertRaises(ValueError):
            SourceViewVersion.objects.filter(pk=view["id"]).update(configuration=changed["configuration"])
        saved = SourceViewVersion.objects.get(pk=view["id"])
        saved.configuration = changed["configuration"]
        with self.assertRaises(ValueError):
            saved.save()
        # The old reference still derives its original value.
        self.assertAlmostEqual(
            calibration.pixels_per_unit(
                {
                    "point_a": old_reference.point_a,
                    "point_b": old_reference.point_b,
                    "real_world_distance": old_reference.real_world_distance,
                },
                view["configuration"]["width"],
                view["configuration"]["height"],
            ),
            128.0,
        )

    def test_rotation_change_is_a_new_view_version_and_draft_pins_stay_coherent(self):
        first_view = self.make_view(rotation=0)
        geometry = self.make_geometry(first_view)
        draft = self.post(
            "/api/operation-drafts",
            {
                "operation_id": str(self.operation.id),
                "name": "Shop door",
                "line": self.line,
                "geometry_version_id": geometry["id"],
            },
        )["draft"]
        self.assertEqual(draft["pins"]["geometry_version_id"], geometry["id"])
        # A rotation change is a NEW source-view version; the draft's pinned
        # geometry stays pinned to the OLD view version (that is what a pin
        # means) and the existing line-change machinery still invalidates it.
        second_view = self.make_view(rotation=90)
        self.assertEqual(SourceViewVersion.objects.count(), 2)
        fetched = self.client.get(f"/api/operation-drafts/{draft['id']}").json()["draft"]
        self.assertEqual(fetched["pins"]["geometry_version_id"], geometry["id"])
        changed_line = {**self.line, "start": [0.3, 0.5]}
        updated = self.client.put(
            f"/api/operation-drafts/{draft['id']}",
            json.dumps({"name": "Shop door", "line": changed_line, "revision": fetched["revision"]}),
            content_type="application/json",
        )
        self.assertEqual(updated.status_code, 200, updated.content)
        self.assertIsNone(updated.json()["draft"]["pins"]["geometry_version_id"])
        # The remapped geometry on the new view can be pinned once the line matches it.
        remapped = self.post(
            f"/api/operations/{self.operation.id}/geometry-versions",
            {"remap": {"from_geometry_version_id": geometry["id"], "to_source_view_version_id": second_view["id"]}},
        )["geometry_version"]
        pinned = self.client.put(
            f"/api/operation-drafts/{draft['id']}",
            json.dumps(
                {
                    "name": "Shop door",
                    "line": remapped["geometry"]["line"],
                    "revision": updated.json()["draft"]["revision"],
                    "geometry_version_id": remapped["id"],
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(pinned.status_code, 200, pinned.content)
        self.assertEqual(pinned.json()["draft"]["pins"]["geometry_version_id"], remapped["id"])

    def test_recording_change_still_invalidates_geometry_and_clears_marks(self):
        draft = self.post(
            "/api/operation-drafts", {"name": "Shop door", "line": self.line}
        )["draft"]
        OperationRecording.objects.create(
            draft_id=draft["id"],
            storage_key="operation-recordings/clip.mp4",
            filename="clip.mp4",
            sha256="a" * 64,
            size_bytes=100,
            width=640,
            height=480,
            duration_seconds=12.5,
        )
        url = f"/api/operation-drafts/{draft['id']}"
        events = [{"time": 1.25, "direction": "negative_to_positive"}]
        saved = self.client.put(
            url,
            json.dumps({"name": "Shop door", "line": self.line, "reference_events": events, "revision": draft["revision"]}),
            content_type="application/json",
        )
        self.assertEqual(saved.status_code, 200, saved.content)
        self.assertEqual(saved.json()["draft"]["reference_events"], events)


def _client(user):
    client = Client()
    client.force_login(user)
    return client


class PureTransformTests(TestCase):
    """The coordinate transform itself, tested as a pure function."""

    def test_round_trip_all_steps_for_many_points(self):
        points = [[0.0, 0.0], [1.0, 0.0], [0.0, 1.0], [1.0, 1.0], [0.25, 0.75], [0.6, 0.1]]
        for point in points:
            x, y = point
            for step in (90, 180, 270):
                # Four applications of the same step compose to a full turn: identity.
                current = point
                for _ in range(4):
                    current = calibration._rotate_point(current, step)
                self.assertAlmostEqual(current[0], x, places=12, msg=f"step {step}")
                self.assertAlmostEqual(current[1], y, places=12, msg=f"step {step}")
                # One application of the step, then the complementary step, cancels.
                once = calibration._rotate_point(point, step)
                back = calibration._rotate_point(once, (360 - step) % 360)
                self.assertAlmostEqual(back[0], x, places=12, msg=f"cancel {step}")
                self.assertAlmostEqual(back[1], y, places=12, msg=f"cancel {step}")

    def test_remap_line_round_trips_and_refuses_degenerate_results(self):
        line = {
            "start": [0.1, 0.4],
            "end": [0.9, 0.6],
            "name": "Line",
            "positive_label": "A",
            "negative_label": "B",
        }
        current = line
        for rotation in (90, 180, 270, 0):
            current = calibration.remap_line(current, (rotation - 90) % 360, rotation)
        self.assertAlmostEqual(current["start"][0], line["start"][0], places=9)
        self.assertAlmostEqual(current["start"][1], line["start"][1], places=9)
        self.assertAlmostEqual(current["end"][0], line["end"][0], places=9)
        self.assertAlmostEqual(current["end"][1], line["end"][1], places=9)
        with self.assertRaises(ValueError):
            calibration.remap_line(line, 0, 0)

    def test_moved_view_thresholds_are_where_they_claim(self):
        # Just under the dimension threshold (same aspect, uniform scale-up): no review.
        self.assertIsNone(
            calibration.moved_view_diff(
                recorded_width=1000,
                recorded_height=800,
                recorded_duration=10.0,
                new_width=1199,
                new_height=959,
                new_duration=10.0,
            )
        )
        # At the threshold: review (>= is material).
        review = calibration.moved_view_diff(
            recorded_width=1000,
            recorded_height=800,
            recorded_duration=10.0,
            new_width=1200,
            new_height=960,
            new_duration=10.0,
        )
        self.assertIsNotNone(review)
        # An aspect change alone is material: 4:3 to 16:9 at a similar height.
        self.assertIsNotNone(
            calibration.moved_view_diff(
                recorded_width=640,
                recorded_height=480,
                recorded_duration=10.0,
                new_width=720,
                new_height=405,
                new_duration=10.0,
            )
        )
        # A small aspect difference (under 5%) is not.
        self.assertIsNone(
            calibration.moved_view_diff(
                recorded_width=1920,
                recorded_height=1080,
                recorded_duration=10.0,
                new_width=1922,
                new_height=1081,
                new_duration=10.0,
            )
        )
        # Duration: 24.9% difference is fine, 25% is material.
        self.assertIsNone(
            calibration.moved_view_diff(
                recorded_width=1000,
                recorded_height=800,
                recorded_duration=10.0,
                new_width=1000,
                new_height=800,
                new_duration=12.49,
            )
        )
        self.assertIsNotNone(
            calibration.moved_view_diff(
                recorded_width=1000,
                recorded_height=800,
                recorded_duration=10.0,
                new_width=1000,
                new_height=800,
                new_duration=12.5,
            )
        )
