"""Immutable source-view, geometry, recipe and policy versions for scoped operations."""

import json

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from .models import GeometryVersion, Operation, OperationSource, PolicyVersion, RecipeVersion, Site, SourceViewVersion


class OperationComponentVersionTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("version-owner")
        self.client.force_login(self.owner)
        self.site = Site.objects.create(created_by=self.owner, name="Shop")
        self.source = OperationSource.objects.create(site=self.site, name="Front", kind="recorded_video")
        self.operation = Operation.objects.create(
            created_by=self.owner, site=self.site, source=self.source, name="Door"
        )

    def post(self, path, payload, client=None):
        return (client or self.client).post(path, json.dumps(payload), content_type="application/json")

    def test_source_view_versions_are_numbered_immutable_and_owner_scoped(self):
        url = f"/api/operations/{self.operation.id}/source-views"
        configuration = {
            "width": 1920,
            "height": 1080,
            "rotation": 0,
            "horizontal_flip": False,
            "vertical_flip": False,
        }
        created = self.post(url, {"configuration": configuration})
        self.assertEqual(created.status_code, 201, created.content)
        first = created.json()["source_view"]
        self.assertEqual(first["version"], 1)
        self.assertEqual(len(first["sha256"]), 64)
        self.assertEqual(first["configuration"], configuration)

        changed = {**configuration, "rotation": 90}
        second = self.post(url, {"configuration": changed}).json()["source_view"]
        self.assertEqual(second["version"], 2)
        self.assertNotEqual(first["sha256"], second["sha256"])
        self.assertEqual(SourceViewVersion.objects.get(pk=first["id"]).configuration, configuration)
        with self.assertRaises(ValueError):
            SourceViewVersion.objects.filter(pk=first["id"]).update(configuration=changed)
        saved_view = SourceViewVersion.objects.get(pk=first["id"])
        saved_view.configuration = changed
        with self.assertRaises(ValueError):
            saved_view.save()

        stranger = Client()
        stranger.force_login(get_user_model().objects.create_user("version-stranger"))
        self.assertEqual(
            stranger.post(
                url, data=json.dumps({"configuration": configuration}), content_type="application/json"
            ).status_code,
            404,
        )

    def test_source_view_configuration_rejects_malformed_or_unknown_fields(self):
        url = f"/api/operations/{self.operation.id}/source-views"
        base = {"width": 1920, "height": 1080, "rotation": 0, "horizontal_flip": False, "vertical_flip": False}
        cases = [
            {**base, "rotation": 45},
            {**base, "width": True},
            {**base, "password": "secret"},
            {**base, "width": 0},
        ]
        for configuration in cases:
            with self.subTest(configuration=configuration):
                self.assertEqual(self.post(url, {"configuration": configuration}).status_code, 400)
        self.assertFalse(SourceViewVersion.objects.exists())

    def test_geometry_version_is_bound_to_a_source_view_and_operation(self):
        source_view = self.post(
            f"/api/operations/{self.operation.id}/source-views",
            {
                "configuration": {
                    "width": 640,
                    "height": 480,
                    "rotation": 0,
                    "horizontal_flip": False,
                    "vertical_flip": False,
                }
            },
        ).json()["source_view"]
        line = {"start": [0.1, 0.5], "end": [0.9, 0.5], "positive_label": "In", "negative_label": "Out"}
        url = f"/api/operations/{self.operation.id}/geometry-versions"
        response = self.post(url, {"source_view_version_id": source_view["id"], "geometry": {"line": line}})
        self.assertEqual(response.status_code, 201, response.content)
        geometry = response.json()["geometry_version"]
        saved = GeometryVersion.objects.get(pk=geometry["id"])
        self.assertEqual(str(saved.source_view_version_id), source_view["id"])
        self.assertEqual(geometry["source_view_version_id"], source_view["id"])
        self.assertEqual(geometry["version"], 1)
        saved.geometry["line"]["positive_label"] = "Changed"
        with self.assertRaises(ValueError):
            saved.save()

    def test_geometry_rejects_foreign_or_incompatible_source_view(self):
        foreign = get_user_model().objects.create_user("foreign-version-owner")
        foreign_site = Site.objects.create(created_by=foreign, name="Foreign")
        foreign_source = OperationSource.objects.create(site=foreign_site, name="Camera", kind="camera")
        foreign_operation = Operation.objects.create(
            created_by=foreign, site=foreign_site, source=foreign_source, name="Foreign door"
        )
        foreign_client = Client()
        foreign_client.force_login(foreign)
        foreign_view = self.post(
            f"/api/operations/{foreign_operation.id}/source-views",
            {
                "configuration": {
                    "width": 640,
                    "height": 480,
                    "rotation": 0,
                    "horizontal_flip": False,
                    "vertical_flip": False,
                }
            },
            foreign_client,
        ).json()["source_view"]
        line = {"start": [0, 0], "end": [1, 1], "positive_label": "In", "negative_label": "Out"}
        url = f"/api/operations/{self.operation.id}/geometry-versions"
        self.assertEqual(
            self.post(url, {"source_view_version_id": foreign_view["id"], "geometry": {"line": line}}).status_code, 404
        )

        own_view = self.post(
            f"/api/operations/{self.operation.id}/source-views",
            {
                "configuration": {
                    "width": 640,
                    "height": 480,
                    "rotation": 0,
                    "horizontal_flip": False,
                    "vertical_flip": False,
                }
            },
        ).json()["source_view"]
        self.assertEqual(
            self.post(url, {"source_view_version_id": own_view["id"], "geometry": {"polygon": []}}).status_code, 400
        )

    def test_recipe_versions_are_numbered_immutable_and_owner_scoped(self):
        url = f"/api/operations/{self.operation.id}/recipe-versions"
        configuration = {
            "dead_band": 0.01,
            "confirmations": 2,
            "max_gap_seconds": 1.0,
            "comparison_tolerance_seconds": 0.5,
        }
        created = self.post(url, {"recipe_key": "people_counting", "configuration": configuration})
        self.assertEqual(created.status_code, 201, created.content)
        first = created.json()["recipe_version"]
        self.assertEqual(first["version"], 1)
        self.assertEqual(first["recipe_key"], "people_counting")
        self.assertEqual(len(first["sha256"]), 64)

        changed = {**configuration, "confirmations": 3}
        second = self.post(url, {"recipe_key": "people_counting", "configuration": changed}).json()["recipe_version"]
        self.assertEqual(second["version"], 2)
        self.assertNotEqual(first["sha256"], second["sha256"])
        self.assertEqual(RecipeVersion.objects.get(pk=first["id"]).configuration, configuration)
        with self.assertRaises(ValueError):
            RecipeVersion.objects.filter(pk=first["id"]).update(configuration=changed)
        saved = RecipeVersion.objects.get(pk=first["id"])
        saved.configuration = changed
        with self.assertRaises(ValueError):
            saved.save()
        self.assertEqual([row["version"] for row in self.client.get(url).json()["recipe_versions"]], [2, 1])

        stranger = Client()
        stranger.force_login(get_user_model().objects.create_user("recipe-stranger"))
        self.assertEqual(
            stranger.post(
                url,
                data=json.dumps({"recipe_key": "people_counting", "configuration": configuration}),
                content_type="application/json",
            ).status_code,
            404,
        )
        self.assertEqual(stranger.get(url).status_code, 404)

    def test_recipe_configuration_rejects_unsupported_key_and_malformed_fields(self):
        url = f"/api/operations/{self.operation.id}/recipe-versions"
        base = {"dead_band": 0.01, "confirmations": 2, "max_gap_seconds": 1.0, "comparison_tolerance_seconds": 0.5}
        cases = [
            {"recipe_key": "parking_bays", "configuration": base},
            {"recipe_key": "people_counting", "configuration": {**base, "confirmations": 0}},
            {"recipe_key": "people_counting", "configuration": {**base, "dead_band": 1.5}},
            {"recipe_key": "people_counting", "configuration": {**base, "max_gap_seconds": -1}},
            {"recipe_key": "people_counting", "configuration": {**base, "extra": True}},
        ]
        for case in cases:
            with self.subTest(case=case):
                self.assertEqual(self.post(url, case).status_code, 400)
        self.assertFalse(RecipeVersion.objects.exists())

    def test_policy_versions_are_numbered_immutable_and_owner_scoped(self):
        url = f"/api/operations/{self.operation.id}/policy-versions"
        configuration = {"evidence_retention_days": 90, "max_coverage_gap_seconds": 5.0}
        created = self.post(url, {"configuration": configuration})
        self.assertEqual(created.status_code, 201, created.content)
        first = created.json()["policy_version"]
        self.assertEqual(first["version"], 1)
        self.assertEqual(len(first["sha256"]), 64)

        changed = {**configuration, "evidence_retention_days": 180}
        second = self.post(url, {"configuration": changed}).json()["policy_version"]
        self.assertEqual(second["version"], 2)
        self.assertNotEqual(first["sha256"], second["sha256"])
        self.assertEqual(PolicyVersion.objects.get(pk=first["id"]).configuration, configuration)
        with self.assertRaises(ValueError):
            PolicyVersion.objects.filter(pk=first["id"]).update(configuration=changed)
        saved = PolicyVersion.objects.get(pk=first["id"])
        saved.configuration = changed
        with self.assertRaises(ValueError):
            saved.save()

        stranger = Client()
        stranger.force_login(get_user_model().objects.create_user("policy-stranger"))
        self.assertEqual(
            stranger.post(
                url, data=json.dumps({"configuration": configuration}), content_type="application/json"
            ).status_code,
            404,
        )

    def test_policy_configuration_rejects_out_of_range_fields(self):
        url = f"/api/operations/{self.operation.id}/policy-versions"
        base = {"evidence_retention_days": 90, "max_coverage_gap_seconds": 5.0}
        for configuration in (
            {**base, "evidence_retention_days": 0},
            {**base, "evidence_retention_days": 3660},
            {**base, "max_coverage_gap_seconds": 0},
            {**base, "retention_weeks": 4},
        ):
            with self.subTest(configuration=configuration):
                self.assertEqual(self.post(url, {"configuration": configuration}).status_code, 400)
        self.assertFalse(PolicyVersion.objects.exists())
