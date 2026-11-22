"""T17: polygon bays with stable names and IDs.

What is under test, honestly: a bay is a named four-corner polygon in the
same normalized 0–1 coordinate discipline as the counting line, with a
stable UUID chosen once at creation — so renaming a bay keeps its identity
and its history (old geometry versions keep the name they were frozen with;
identity is resolved by ID, never by string matching); the four corners are
validated with proper segment-crossing math, so a crossed "bowtie" is
refused however convex its corner positions look, out-of-bounds and
non-quad input is refused, and degenerate near-zero-area quads are refused
with plain messages shaped like the T14 degenerate-line refusals; the draft
carries a bay list that rides the same coherence machinery as the line (a
changed bay list invalidates a kept geometry pin, and a pin requires the
matching bays); the T16 rotation remap rotates bay corners exactly as it
rotates line endpoints while IDs and names survive, and non-remappable
format changes are still refused; duplicate mints a fresh stable ID with a
"… (copy)" name; and the operations page carries the T15 keyboard-editing
idioms for bays (corner selection, arrow nudging, aria-live text summary,
undo/redo, confirm-guarded destructive actions).
"""

from __future__ import annotations

import json
import uuid as uuid_module

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from . import bays as bays_module
from .models import GeometryVersion, Operation, OperationDraft, OperationRecording, OperationSource, Site
from .tests import grant

RECTANGLE = [[0.1, 0.1], [0.4, 0.1], [0.4, 0.4], [0.1, 0.4]]


def make_bay(name="Bay 1", corners=None, bay_id=None):
    return {"id": bay_id or str(uuid_module.uuid4()), "name": name, "corners": corners or RECTANGLE}


class BayValidationTests(TestCase):
    """The pure validation rules: quads, bounds, self-intersection, identity."""

    def test_a_simple_quad_passes_and_is_normalized_to_floats(self):
        result = bays_module.operation_bay(make_bay(corners=[[0, 0], [1, 0], [1, 1], [0, 1]]))
        self.assertEqual(result["corners"], [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]])
        self.assertEqual(result["name"], "Bay 1")

    def test_a_bowtie_is_refused_by_segment_crossing_not_convexity(self):
        # The four corners sit on a perfect rectangle — a convexity heuristic
        # would pass this — but the corner ORDER crosses the outline.
        bowtie = [[0.1, 0.1], [0.9, 0.9], [0.9, 0.1], [0.1, 0.9]]
        with self.assertRaises(ValueError) as raised:
            bays_module.bay_corners(bowtie)
        self.assertIn("cross each other", str(raised.exception))
        # ...and the same four corners in perimeter order are a legal quad.
        bays_module.bay_corners([[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]])

    def test_a_thin_bowtie_is_also_refused(self):
        # A thin but genuinely crossed outline still crosses, however slight.
        with self.assertRaises(ValueError):
            bays_module.bay_corners([[0.1, 0.1], [0.4, 0.45], [0.4, 0.1], [0.1, 0.45]])

    def test_concave_quads_are_allowed(self):
        # A dart/arrow shape is simple (non self-intersecting) even though it
        # is concave; only crossing outlines are refused.
        bays_module.bay_corners([[0.1, 0.1], [0.9, 0.1], [0.2, 0.2], [0.1, 0.9]])

    def test_non_quad_input_is_refused(self):
        for corners in ([[0.1, 0.1]] * 3, [[0.1, 0.1]] * 5, "four corners", []):
            with self.subTest(corners=corners):
                with self.assertRaises(ValueError) as raised:
                    bays_module.bay_corners(corners)
                self.assertIn("four corners", str(raised.exception))

    def test_out_of_bounds_and_malformed_corners_are_refused(self):
        cases = [
            ("x above 1", [[1.5, 0.1], [0.4, 0.1], [0.4, 0.4], [0.1, 0.4]]),
            ("y below 0", [[-0.1, 0.1], [0.4, 0.1], [0.4, 0.4], [0.1, 0.4]]),
            ("boolean coordinate", [[True, 0.1], [0.4, 0.1], [0.4, 0.4], [0.1, 0.4]]),
            ("non-finite", [["x", 0.1], [0.4, 0.1], [0.4, 0.4], [0.1, 0.4]]),
            ("wrong arity", [[0.1, 0.1, 0.2], [0.4, 0.1], [0.4, 0.4], [0.1, 0.4]]),
        ]
        for label, corners in cases:
            with self.subTest(case=label):
                with self.assertRaises(ValueError) as raised:
                    bays_module.bay_corners(corners)
                self.assertIn("between 0 and 1", str(raised.exception))

    def test_degenerate_zero_area_quads_are_refused_with_the_plain_message(self):
        with self.assertRaises(ValueError) as raised:
            bays_module.bay_corners([[0.5, 0.5], [0.5, 0.5], [0.5, 0.500001], [0.5, 0.5]])
        self.assertIn("too close together", str(raised.exception))
        # A straight line of four corners has no area either.
        with self.assertRaises(ValueError):
            bays_module.bay_corners([[0.1, 0.5], [0.3, 0.5], [0.6, 0.5], [0.9, 0.5]])

    def test_bay_identity_needs_a_stable_uuid(self):
        for bad in (None, "bay-1", "", 42, "not-a-uuid"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    bays_module.operation_bay({"id": bad, "name": "Bay 1", "corners": RECTANGLE})

    def test_bay_names_are_validated_like_line_names(self):
        for bad in (None, "", "   ", "x" * 81):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError) as raised:
                    bays_module.operation_bay({"id": str(uuid_module.uuid4()), "name": bad, "corners": RECTANGLE})
                self.assertIn("80 characters", str(raised.exception))

    def test_a_bay_list_needs_unique_ids_and_respects_the_limit(self):
        first = make_bay()
        with self.assertRaises(ValueError) as raised:
            bays_module.operation_bays([first, {**first, "name": "Also bay 1"}])
        self.assertIn("own ID", str(raised.exception))
        many = [make_bay(f"Bay {i}") for i in range(bays_module.BAY_COUNT_LIMIT + 1)]
        with self.assertRaises(ValueError):
            bays_module.operation_bays(many)

    def test_duplicate_names_are_allowed_identity_is_the_id(self):
        # Two bays may share a name — the ID is the identity.
        result = bays_module.operation_bays([make_bay("Bay 1"), make_bay("Bay 1")])
        self.assertEqual(len(result), 2)
        self.assertNotEqual(result[0]["id"], result[1]["id"])

    def test_duplicate_bay_name_helper_makes_a_plainly_unique_copy_name(self):
        self.assertEqual(bays_module.duplicate_bay_name("Bay 1", ["Bay 2"]), "Bay 1 (copy)")
        self.assertEqual(bays_module.duplicate_bay_name("Bay 1", ["Bay 1 (copy)"]), "Bay 1 (copy) 2")
        self.assertEqual(
            bays_module.duplicate_bay_name("Bay 1", ["Bay 1 (copy)", "Bay 1 (copy) 2"]),
            "Bay 1 (copy) 3",
        )

    def test_duplicate_bay_name_stays_within_80_characters_including_a_collision_suffix(self):
        name = "N" * 80
        copy = bays_module.duplicate_bay_name(name, [name])
        self.assertEqual(len(copy), 80)
        self.assertTrue(copy.endswith("(copy)"))
        numbered = bays_module.duplicate_bay_name(name, [name, copy])
        self.assertEqual(len(numbered), 80)
        self.assertIn(" (copy)", numbered)
        self.assertNotEqual(numbered, copy)


class BayDraftAPITests(TestCase):
    """Bays on the draft API: storage, validation, identity across renames, actions."""

    def setUp(self):
        self.user = get_user_model().objects.create_user("t17-owner")
        grant(self.user, "labeler")
        self.client.force_login(self.user)

    def post(self, path, payload, status=201):
        response = self.client.post(path, json.dumps(payload), content_type="application/json")
        self.assertEqual(response.status_code, status, response.content)
        return response.json()

    def put(self, path, payload, status=200):
        response = self.client.put(path, json.dumps(payload), content_type="application/json")
        self.assertEqual(response.status_code, status, response.content)
        return response.json()

    def test_a_draft_carries_its_named_bays_normalized(self):
        draft = self.post("/api/operation-drafts", {"name": "Shop door", "bays": [make_bay("Bay 1")]})["draft"]
        self.assertEqual(len(draft["bays"]), 1)
        self.assertEqual(draft["bays"][0]["name"], "Bay 1")
        self.assertEqual(draft["bays"][0]["corners"], RECTANGLE)
        # The stable ID round-trips exactly.
        bay_id = draft["bays"][0]["id"]
        uuid_module.UUID(bay_id)

    def test_renaming_a_bay_keeps_its_id_and_history(self):
        bay = make_bay("Bay 1")
        draft = self.post("/api/operation-drafts", {"name": "Shop door", "bays": [bay]})["draft"]
        renamed = {**bay, "name": "Customer bay"}
        updated = self.put(
            f"/api/operation-drafts/{draft['id']}",
            {"name": "Shop door", "bays": [renamed], "revision": draft["revision"]},
        )["draft"]
        self.assertEqual(updated["bays"][0]["id"], bay["id"], "renaming must keep the stable ID")
        self.assertEqual(updated["bays"][0]["name"], "Customer bay")
        # History: the older revision's snapshot keeps the old name, while the
        # bay's identity is resolvable by ID across both.
        versions = self.client.get(f"/api/operation-drafts/{draft['id']}/versions").json()["versions"]
        self.assertEqual(versions[1]["snapshot"]["geometry"]["bays"][0]["name"], "Bay 1")
        self.assertEqual(versions[0]["snapshot"]["geometry"]["bays"][0]["name"], "Customer bay")
        self.assertEqual(
            versions[0]["snapshot"]["geometry"]["bays"][0]["id"],
            versions[1]["snapshot"]["geometry"]["bays"][0]["id"],
        )

    def test_invalid_bays_are_refused_and_nothing_is_stored(self):
        same_bay = make_bay()
        cases = [
            ("bowtie", [make_bay(corners=[[0.1, 0.1], [0.9, 0.9], [0.9, 0.1], [0.1, 0.9]])]),
            ("out of bounds", [make_bay(corners=[[1.5, 0.1], [0.4, 0.1], [0.4, 0.4], [0.1, 0.4]])]),
            ("three corners", [make_bay(corners=[[0.1, 0.1], [0.4, 0.1], [0.4, 0.4]])]),
            ("no stable id", [{"name": "Bay 1", "corners": RECTANGLE}]),
            ("duplicate ids", [same_bay, dict(same_bay)]),
        ]
        for label, payload_bays in cases:
            with self.subTest(case=label):
                response = self.client.post(
                    "/api/operation-drafts",
                    json.dumps({"name": "Shop door", "bays": payload_bays}),
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 400, label)
        self.assertFalse(OperationDraft.objects.exclude(bays=[]).exists())

    def test_duplicate_action_mints_a_new_id_with_a_copy_name(self):
        bay = make_bay("Bay 1")
        draft = self.post("/api/operation-drafts", {"name": "Shop door", "bays": [bay]})["draft"]
        result = self.post(
            f"/api/operation-drafts/{draft['id']}/bays/actions",
            {"action": "duplicate", "bay_id": bay["id"]},
            status=200,
        )
        updated = result["draft"]
        self.assertEqual(len(updated["bays"]), 2)
        original, copy = updated["bays"]
        self.assertEqual(original["id"], bay["id"])
        self.assertNotEqual(copy["id"], bay["id"], "the duplicate is a NEW bay with its own stable ID")
        self.assertEqual(copy["name"], "Bay 1 (copy)")
        self.assertEqual(copy["corners"], bay["corners"])
        # The action recorded a new revision and snapshot.
        versions = self.client.get(f"/api/operation-drafts/{draft['id']}/versions").json()["versions"]
        self.assertEqual([v["revision"] for v in versions], [draft["revision"] + 1, draft["revision"]])

    def test_duplicate_action_keeps_an_80_character_copy_name_within_the_limit(self):
        name = "N" * 80
        bay = make_bay(name)
        draft = self.post("/api/operation-drafts", {"name": "Shop door", "bays": [bay]})["draft"]
        first = self.post(
            f"/api/operation-drafts/{draft['id']}/bays/actions",
            {"action": "duplicate", "bay_id": bay["id"]},
            status=200,
        )
        copy = first["draft"]["bays"][1]
        self.assertNotEqual(copy["id"], bay["id"])
        self.assertLessEqual(len(copy["name"]), 80)
        self.assertTrue(copy["name"].endswith("(copy)"))
        second = self.post(
            f"/api/operation-drafts/{draft['id']}/bays/actions",
            {"action": "duplicate", "bay_id": bay["id"]},
            status=200,
        )
        known = {bay["id"], copy["id"]}
        numbered = next(item for item in second["draft"]["bays"] if item["id"] not in known)
        self.assertNotEqual(numbered["id"], copy["id"])
        self.assertLessEqual(len(numbered["name"]), 80)
        self.assertIn(" (copy)", numbered["name"])
        self.assertNotEqual(numbered["name"], copy["name"])

    def test_rename_action_changes_only_the_name_not_the_id(self):
        bay = make_bay("Bay 1")
        draft = self.post("/api/operation-drafts", {"name": "Shop door", "bays": [bay]})["draft"]
        result = self.post(
            f"/api/operation-drafts/{draft['id']}/bays/actions",
            {"action": "rename", "bay_id": bay["id"], "name": "Disabled bay"},
            status=200,
        )
        renamed = result["draft"]["bays"][0]
        self.assertEqual(renamed["id"], bay["id"])
        self.assertEqual(renamed["name"], "Disabled bay")
        self.assertIn("history", result["message"])

    def test_bay_actions_validate_their_input_plainly(self):
        draft = self.post("/api/operation-drafts", {"name": "Shop door", "bays": [make_bay()]})["draft"]
        bay_id = draft["bays"][0]["id"]
        for label, payload, status in (
            ("unknown action", {"action": "rotate", "bay_id": bay_id}, 400),
            ("missing bay", {"action": "duplicate"}, 400),
            ("foreign bay", {"action": "duplicate", "bay_id": str(uuid_module.uuid4())}, 400),
            ("blank rename", {"action": "rename", "bay_id": bay_id, "name": "   "}, 400),
            ("extra field", {"action": "duplicate", "bay_id": bay_id, "corners": RECTANGLE}, 400),
        ):
            with self.subTest(case=label):
                self.post(f"/api/operation-drafts/{draft['id']}/bays/actions", payload, status=status)
        # A viewer without the label role cannot act on bays at all.
        viewer = get_user_model().objects.create_user("t17-viewer")
        grant(viewer, "viewer")
        viewer_client = Client()
        viewer_client.force_login(viewer)
        response = viewer_client.post(
            f"/api/operation-drafts/{draft['id']}/bays/actions",
            json.dumps({"action": "duplicate", "bay_id": bay_id}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_bay_actions_are_404_for_another_owner(self):
        bay = make_bay("Bay 1")
        draft = self.post("/api/operation-drafts", {"name": "Shop door", "bays": [bay]})["draft"]
        stranger = get_user_model().objects.create_user("t17-other-owner")
        grant(stranger, "labeler")
        other = Client()
        other.force_login(stranger)
        response = other.post(
            f"/api/operation-drafts/{draft['id']}/bays/actions",
            json.dumps({"action": "duplicate", "bay_id": bay["id"]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)
        stored = OperationDraft.objects.get(pk=draft["id"])
        self.assertEqual(len(stored.bays), 1)
        self.assertEqual(stored.bays[0]["id"], bay["id"])

    def test_changed_bays_invalidate_a_kept_geometry_pin(self):
        site = Site.objects.create(created_by=self.user, name="Shop")
        source = OperationSource.objects.create(site=site, name="Front", kind="recorded_video")
        operation = Operation.objects.create(created_by=self.user, site=site, source=source, name="Door")
        view = self.post(
            f"/api/operations/{operation.id}/source-views",
            {
                "configuration": {
                    "width": 640,
                    "height": 480,
                    "rotation": 0,
                    "horizontal_flip": False,
                    "vertical_flip": False,
                }
            },
        )["source_view"]
        line = {"start": [0.2, 0.5], "end": [0.8, 0.5], "name": "Door", "positive_label": "In", "negative_label": "Out"}
        bay = make_bay("Bay 1")
        geometry = self.post(
            f"/api/operations/{operation.id}/geometry-versions",
            {"source_view_version_id": view["id"], "geometry": {"line": line, "bays": [bay]}},
        )["geometry_version"]
        draft = self.post(
            "/api/operation-drafts",
            {
                "operation_id": str(operation.id),
                "name": "Shop door",
                "line": line,
                "bays": [bay],
                "geometry_version_id": geometry["id"],
            },
        )["draft"]
        self.assertEqual(draft["pins"]["geometry_version_id"], geometry["id"])
        # Moving a corner invalidates the kept pin, exactly as a changed line does.
        moved = {**bay, "corners": [[0.2, 0.2], [0.5, 0.2], [0.5, 0.5], [0.2, 0.5]]}
        updated = self.put(
            f"/api/operation-drafts/{draft['id']}",
            {"name": "Shop door", "line": line, "bays": [moved], "revision": draft["revision"]},
        )["draft"]
        self.assertIsNone(updated["pins"]["geometry_version_id"])
        # ...and the geometry POST path validates bays with the same rules.
        self.post(
            f"/api/operations/{operation.id}/geometry-versions",
            {
                "source_view_version_id": view["id"],
                "geometry": {"line": line, "bays": [make_bay(corners=[[0, 0], [1, 1], [1, 0], [0, 1]])]},
            },
            status=400,
        )

    def test_supplying_the_old_geometry_pin_with_changed_bays_is_refused(self):
        site = Site.objects.create(created_by=self.user, name="Shop")
        source = OperationSource.objects.create(site=site, name="Front", kind="recorded_video")
        operation = Operation.objects.create(created_by=self.user, site=site, source=source, name="Door")
        view = self.post(
            f"/api/operations/{operation.id}/source-views",
            {
                "configuration": {
                    "width": 640,
                    "height": 480,
                    "rotation": 0,
                    "horizontal_flip": False,
                    "vertical_flip": False,
                }
            },
        )["source_view"]
        line = {"start": [0.2, 0.5], "end": [0.8, 0.5], "name": "Door", "positive_label": "In", "negative_label": "Out"}
        bay = make_bay("Bay 1")
        geometry = self.post(
            f"/api/operations/{operation.id}/geometry-versions",
            {"source_view_version_id": view["id"], "geometry": {"line": line, "bays": [bay]}},
        )["geometry_version"]
        moved = {**bay, "corners": [[0.2, 0.2], [0.5, 0.2], [0.5, 0.5], [0.2, 0.5]]}
        created = self.client.post(
            "/api/operation-drafts",
            json.dumps(
                {
                    "operation_id": str(operation.id),
                    "name": "Shop door",
                    "line": line,
                    "bays": [moved],
                    "geometry_version_id": geometry["id"],
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(created.status_code, 400, created.content)
        self.assertIn(b"matching bays", created.content)
        draft = self.post(
            "/api/operation-drafts",
            {
                "operation_id": str(operation.id),
                "name": "Shop door",
                "line": line,
                "bays": [bay],
                "geometry_version_id": geometry["id"],
            },
        )["draft"]
        updated = self.client.put(
            f"/api/operation-drafts/{draft['id']}",
            json.dumps(
                {
                    "name": "Shop door",
                    "line": line,
                    "bays": [moved],
                    "revision": draft["revision"],
                    "geometry_version_id": geometry["id"],
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(updated.status_code, 400, updated.content)
        self.assertIn(b"matching bays", updated.content)
        stored = self.client.get(f"/api/operation-drafts/{draft['id']}").json()["draft"]
        self.assertEqual(stored["pins"]["geometry_version_id"], geometry["id"])
        self.assertEqual(stored["bays"][0]["corners"], bay["corners"])

    def test_removing_the_recording_clears_bays_too(self):
        draft_row = OperationDraft.objects.create(created_by=self.user, name="Shop door", bays=[make_bay()])
        OperationRecording.objects.create(
            draft=draft_row,
            storage_key="operation-recordings/test.mp4",
            filename="clip.mp4",
            sha256="a" * 64,
            size_bytes=10,
            width=64,
            height=48,
            duration_seconds=2.0,
        )
        response = self.client.delete(
            f"/api/operation-drafts/{draft_row.id}/recording",
            json.dumps({"revision": draft_row.revision}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["draft"]["bays"], [])


class BayRemapTests(TestCase):
    """The T16 rotation remap carries bays along; refusals stay honest."""

    def setUp(self):
        self.owner = get_user_model().objects.create_user("t17-remap-owner")
        grant(self.owner, "labeler")
        self.client.force_login(self.owner)
        self.site = Site.objects.create(created_by=self.owner, name="Shop")
        self.source = OperationSource.objects.create(site=self.site, name="Front", kind="recorded_video")
        self.operation = Operation.objects.create(
            created_by=self.owner, site=self.site, source=self.source, name="Door"
        )
        self.line = {
            "start": [0.2, 0.5],
            "end": [0.8, 0.5],
            "name": "Door",
            "positive_label": "In",
            "negative_label": "Out",
        }
        self.bay = make_bay("Bay 1")

    def post(self, path, payload, status=201):
        response = self.client.post(path, json.dumps(payload), content_type="application/json")
        self.assertEqual(response.status_code, status, response.content)
        return response.json()

    def make_view(self, *, rotation=0, width=640, height=480):
        configuration = {
            "width": width,
            "height": height,
            "rotation": rotation,
            "horizontal_flip": False,
            "vertical_flip": False,
        }
        return self.post(f"/api/operations/{self.operation.id}/source-views", {"configuration": configuration})[
            "source_view"
        ]

    def test_remap_rotates_bay_corners_and_keeps_identity(self):
        old_view = self.make_view(rotation=0)
        geometry = self.post(
            f"/api/operations/{self.operation.id}/geometry-versions",
            {"source_view_version_id": old_view["id"], "geometry": {"line": self.line, "bays": [self.bay]}},
        )["geometry_version"]
        new_view = self.make_view(rotation=90)
        remapped = self.post(
            f"/api/operations/{self.operation.id}/geometry-versions",
            {"remap": {"from_geometry_version_id": geometry["id"], "to_source_view_version_id": new_view["id"]}},
        )
        bay = remapped["geometry_version"]["geometry"]["bays"][0]
        # 0 -> 90 clockwise: (x, y) becomes (1 - y, x), applied to all four corners.
        self.assertEqual(bay["corners"], [[0.9, 0.1], [0.9, 0.4], [0.6, 0.4], [0.6, 0.1]])
        self.assertEqual(bay["id"], self.bay["id"], "a remapped bay keeps its stable ID")
        self.assertEqual(bay["name"], "Bay 1")
        # The old geometry version is untouched.
        old = GeometryVersion.objects.get(pk=geometry["id"])
        self.assertEqual(old.geometry["bays"][0]["corners"], RECTANGLE)

    def test_bay_remap_round_trips_through_all_four_rotation_steps(self):
        view = self.make_view(rotation=0)
        geometry = self.post(
            f"/api/operations/{self.operation.id}/geometry-versions",
            {"source_view_version_id": view["id"], "geometry": {"line": self.line, "bays": [self.bay]}},
        )["geometry_version"]
        current_id = geometry["id"]
        current_bay = geometry["geometry"]["bays"][0]
        for step in (90, 180, 270, 0):
            new_view = self.make_view(rotation=step)
            result = self.post(
                f"/api/operations/{self.operation.id}/geometry-versions",
                {"remap": {"from_geometry_version_id": current_id, "to_source_view_version_id": new_view["id"]}},
            )
            current_id = result["geometry_version"]["id"]
            current_bay = result["geometry_version"]["geometry"]["bays"][0]
        for corner, original in zip(current_bay["corners"], RECTANGLE):
            for a, b in zip(corner, original):
                self.assertAlmostEqual(a, b, places=9)
        self.assertEqual(current_bay["id"], self.bay["id"])

    def test_remap_still_refuses_non_rotation_changes_for_bay_geometry(self):
        view = self.make_view(width=1920, height=1080)
        geometry = self.post(
            f"/api/operations/{self.operation.id}/geometry-versions",
            {"source_view_version_id": view["id"], "geometry": {"line": self.line, "bays": [self.bay]}},
        )["geometry_version"]
        new_view = self.make_view(width=1280, height=720)
        response = self.client.post(
            f"/api/operations/{self.operation.id}/geometry-versions",
            json.dumps(
                {"remap": {"from_geometry_version_id": geometry["id"], "to_source_view_version_id": new_view["id"]}}
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn(b"redraw", response.content.lower())
        self.assertEqual(GeometryVersion.objects.count(), 1)


class BayGeometryVersionTests(TestCase):
    """Bays inside frozen geometry versions: immutable, hashed, identity-preserving."""

    def setUp(self):
        self.owner = get_user_model().objects.create_user("t17-version-owner")
        grant(self.owner, "labeler")
        self.client.force_login(self.owner)
        self.site = Site.objects.create(created_by=self.owner, name="Shop")
        self.source = OperationSource.objects.create(site=self.site, name="Front", kind="recorded_video")
        self.operation = Operation.objects.create(created_by=self.owner, site=self.site, source=self.source, name="Door")
        self.view = self.client.post(
            f"/api/operations/{self.operation.id}/source-views",
            json.dumps(
                {
                    "configuration": {
                        "width": 640,
                        "height": 480,
                        "rotation": 0,
                        "horizontal_flip": False,
                        "vertical_flip": False,
                    }
                }
            ),
            content_type="application/json",
        ).json()["source_view"]
        self.line = {
            "start": [0.2, 0.5],
            "end": [0.8, 0.5],
            "name": "Door",
            "positive_label": "In",
            "negative_label": "Out",
        }

    def post(self, path, payload, status=201):
        response = self.client.post(path, json.dumps(payload), content_type="application/json")
        self.assertEqual(response.status_code, status, response.content)
        return response.json()

    def test_geometry_version_stores_bays_and_a_rename_is_a_new_version(self):
        first = self.post(
            f"/api/operations/{self.operation.id}/geometry-versions",
            {"source_view_version_id": self.view["id"], "geometry": {"line": self.line, "bays": [make_bay("Bay 1")]}},
        )["geometry_version"]
        bay = first["geometry"]["bays"][0]
        second = self.post(
            f"/api/operations/{self.operation.id}/geometry-versions",
            {
                "source_view_version_id": self.view["id"],
                "geometry": {"line": self.line, "bays": [{**bay, "name": "Renamed bay"}]},
            },
        )["geometry_version"]
        # The rename is a NEW immutable version with the SAME bay ID inside.
        self.assertNotEqual(second["id"], first["id"])
        self.assertEqual(second["geometry"]["bays"][0]["id"], bay["id"])
        self.assertEqual(second["geometry"]["bays"][0]["name"], "Renamed bay")
        # The first version keeps the name it was frozen with.
        old = GeometryVersion.objects.get(pk=first["id"])
        self.assertEqual(old.geometry["bays"][0]["name"], "Bay 1")

    def test_geometry_version_integrity_check_covers_bays(self):
        from django.db import connection

        from .operation_views import _component_sha256

        bay = make_bay("Bay 1")
        created = self.post(
            f"/api/operations/{self.operation.id}/geometry-versions",
            {"source_view_version_id": self.view["id"], "geometry": {"line": self.line, "bays": [bay]}},
        )["geometry_version"]
        row = GeometryVersion.objects.get(pk=created["id"])
        payload = {"source_view_version_id": str(row.source_view_version_id), "geometry": row.geometry}
        self.assertEqual(created["sha256"], row.geometry_sha256)
        self.assertEqual(_component_sha256(payload), created["sha256"])
        renamed = self.post(
            f"/api/operations/{self.operation.id}/geometry-versions",
            {
                "source_view_version_id": self.view["id"],
                "geometry": {"line": self.line, "bays": [{**bay, "name": "Renamed bay"}]},
            },
        )["geometry_version"]
        self.assertNotEqual(renamed["sha256"], created["sha256"])
        row.geometry["bays"][0]["name"] = "Tampered"
        with self.assertRaises(ValueError):
            row.save()
        self.assertEqual(GeometryVersion.objects.get(pk=created["id"]).geometry["bays"][0]["name"], "Bay 1")
        tampered = json.loads(json.dumps(GeometryVersion.objects.get(pk=created["id"]).geometry))
        tampered["bays"][0]["name"] = "Tampered"
        with connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {GeometryVersion._meta.db_table} SET geometry = %s WHERE id = %s",
                [json.dumps(tampered), row.pk.hex],
            )
        listing = self.client.get(f"/api/operations/{self.operation.id}/geometry-versions")
        self.assertEqual(listing.status_code, 500, listing.content)
        self.assertIn(b"integrity check", listing.content.lower())

    def test_geometry_without_bays_is_still_valid_and_reports_an_empty_list(self):
        created = self.post(
            f"/api/operations/{self.operation.id}/geometry-versions",
            {"source_view_version_id": self.view["id"], "geometry": {"line": self.line}},
        )["geometry_version"]
        self.assertEqual(created["geometry"]["bays"], [])


class BayPageSurfaceTests(TestCase):
    """The operations page carries the bays surface with the T15 accessibility idioms."""

    def setUp(self):
        self.user = get_user_model().objects.create_user("t17-page-user")
        grant(self.user, "labeler")
        self.client.force_login(self.user)

    def test_page_includes_the_bay_editing_surfaces(self):
        page = self.client.get("/operations")
        self.assertEqual(page.status_code, 200)
        for needle in (
            'id="bay-section"',
            'id="bay-name"',
            'id="bay-canvas"',
            'id="bay-add"',
            'data-testid="bay-add"',
            'data-testid="bay-remove"',
            'data-testid="bay-duplicate"',
            'data-testid="bay-rename"',
            'id="bay-corner-0-x"',
            'id="bay-corner-3-y"',
            'id="bay-select"',
            'id="bay-select-corner-0"',
            'id="bay-move-left"',
            'id="bay-move-down"',
            'id="bay-undo"',
            'data-testid="bay-undo"',
            'data-testid="bay-redo"',
            'id="bay-summary"',
            'data-testid="bay-summary"',
            'data-testid="bay-save"',
            "operation-bays.js",
        ):
            with self.subTest(needle=needle):
                self.assertContains(page, needle)

    def test_bay_surfaces_follow_the_t15_accessibility_idioms(self):
        page = self.client.get("/operations")
        # The text summary is a live region, like the line summary.
        self.assertContains(page, 'id="bay-summary" class="line-summary-text" role="status" aria-live="polite"')
        # The chosen-corner buttons expose their state as text, never colour alone.
        self.assertContains(page, 'id="bay-select-corner-0" type="button" aria-pressed="false"')
        self.assertContains(page, 'role="group" aria-labelledby="bay-nudge-point-label"')
        self.assertContains(page, 'role="group" aria-labelledby="bay-nudge-move-label"')
        # The copy states the fine and coarse keyboard steps and that dragging is
        # never required.
        self.assertContains(page, "fine step of 0.001")
        self.assertContains(page, "coarse step of 0.01")
        self.assertContains(page, "Dragging a corner is never required")
        # The editing canvas is labelled and focusable.
        self.assertContains(
            page, 'id="bay-canvas" class="line-canvas" width="640" height="360" tabindex="0" role="img"'
        )
        # The undo/redo copy is honest about the saved-state boundary.
        self.assertContains(page, "undo never reaches past the last saved state")
        # Identity and validation rules are stated in the copy.
        self.assertContains(page, "renaming a bay never loses its history")
        self.assertContains(page, "must not overlap itself")

    def test_bay_controls_are_labeler_gated(self):
        page = self.client.get("/operations")
        for needle in (
            'id="bay-add" type="button" data-needs="label"',
            'id="bay-remove" type="button" data-needs="label"',
            'id="bay-duplicate" type="button" data-needs="label"',
            'id="bay-save" type="button" data-needs="label"',
            'id="bay-clear" type="button" data-needs="label"',
        ):
            with self.subTest(needle=needle):
                self.assertContains(page, needle)
