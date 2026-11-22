"""T18: zone and disabled-bay management.

Contracts under test:
* Users define named areas/zones on bays, label unobservable bays, distinguish
  ``disabled`` (out of service, excluded from usable capacity) from ``unobservable``
  (blocked view, which reports as ``unknown`` at runtime and is never counted in
  ``free`` or ``disabled``), and configure bay/zone capacity.
* Changing a bay's zone, observability/disabled state, or capacity preserves its
  stable UUID identity and records a new immutable draft revision or geometry
  version without mutating historical records.
* Rotation remaps preserve T18 zone, observability, disabled/unobservable flags,
  reasons, and capacity.
* The operations page exposes accessible controls for zone, observability,
  disabled reason, and capacity.
"""

from __future__ import annotations

import json
import uuid as uuid_module

from django.contrib.auth import get_user_model
from django.test import TestCase

from . import bays as bays_module
from .models import GeometryVersion, Operation, OperationSource, Site
from .tests import grant

RECTANGLE = [[0.1, 0.1], [0.4, 0.1], [0.4, 0.4], [0.1, 0.4]]


def make_bay(name="Bay 1", corners=None, bay_id=None, **extra):
    return {
        "id": bay_id or str(uuid_module.uuid4()),
        "name": name,
        "corners": corners or RECTANGLE,
        **extra,
    }


class ZoneAndDisabledBayValidationTests(TestCase):
    """Pure validation and summary rules for zones, observability, Disabled vs Unknown, and capacity."""

    def test_zone_observability_reason_and_capacity_validate_and_normalize(self) -> None:
        bay = bays_module.operation_bay(
            make_bay(
                "B12",
                zone="  North row  ",
                observability="disabled",
                disabled_reason="  Coned off for resurfacing  ",
                capacity=2,
            )
        )
        self.assertEqual(bay["zone"], "North row")
        self.assertEqual(bay["observability"], "disabled")
        self.assertTrue(bay["disabled"])
        self.assertEqual(bay["disabled_reason"], "Coned off for resurfacing")
        self.assertEqual(bay["capacity"], 2)

    def test_unobservable_is_distinct_from_disabled_and_reports_initial_unknown(self) -> None:
        disabled_bay = bays_module.operation_bay(
            make_bay("B1", observability="disabled", disabled_reason="Closed")
        )
        unobservable_bay = bays_module.operation_bay(
            make_bay("B2", observability="unobservable", disabled_reason="Pillar blocks view")
        )
        observable_bay = bays_module.operation_bay(make_bay("B3"))

        self.assertEqual(bays_module.bay_effective_observability(disabled_bay), "disabled")
        self.assertEqual(bays_module.bay_effective_observability(unobservable_bay), "unobservable")
        self.assertEqual(bays_module.bay_effective_observability(observable_bay), "observable")

        self.assertEqual(bays_module.bay_initial_runtime_state(disabled_bay), "disabled")
        self.assertEqual(bays_module.bay_initial_runtime_state(unobservable_bay), "unknown")
        self.assertEqual(bays_module.bay_initial_runtime_state(observable_bay), "unknown")

    def test_rejects_conflicting_or_invalid_zone_observability_and_capacity(self) -> None:
        cases = [
            ("both disabled and unobservable", make_bay(disabled=True, unobservable=True)),
            ("unknown as config mode", make_bay(observability="unknown")),
            ("zone too long", make_bay(zone="Z" * 81)),
            ("reason too long", make_bay(disabled_reason="R" * 161)),
            ("zero capacity", make_bay(capacity=0)),
            ("negative capacity", make_bay(capacity=-2)),
            ("boolean capacity", make_bay(capacity=True)),
            ("over max capacity", make_bay(capacity=bays_module.BAY_CAPACITY_MAX + 1)),
        ]
        for label, payload in cases:
            with self.subTest(case=label):
                with self.assertRaises(ValueError):
                    bays_module.operation_bay(payload)

    def test_summary_excludes_disabled_from_usable_capacity_and_never_counts_unknown_as_free(
        self,
    ) -> None:
        b1 = bays_module.operation_bay(make_bay("B1", zone="North", capacity=1))
        b2 = bays_module.operation_bay(make_bay("B2", zone="North", capacity=2))
        b3 = bays_module.operation_bay(
            make_bay("B3", zone="North", observability="unobservable", disabled_reason="Tree", capacity=1)
        )
        b4 = bays_module.operation_bay(
            make_bay("B4", zone="South", observability="disabled", disabled_reason="Repairs", capacity=3)
        )
        summary = bays_module.summarize_bays_and_zones(
            [b1, b2, b3, b4],
            runtime_states={
                b1["id"]: "free",
                b2["id"]: "occupied",
                # Even if a caller passed 'free' for an unobservable bay, it must remain 'unknown'.
                b3["id"]: "free",
            },
        )
        self.assertEqual(summary["total_bays"], 4)
        self.assertEqual(summary["observable_bays"], 2)
        self.assertEqual(summary["unobservable_bays"], 1)
        self.assertEqual(summary["disabled_bays"], 1)
        self.assertEqual(summary["total_capacity"], 7)
        self.assertEqual(summary["disabled_capacity"], 3)
        self.assertEqual(summary["usable_capacity"], 4)
        self.assertEqual(summary["observable_capacity"], 3)
        self.assertEqual(summary["unobservable_capacity"], 1)
        self.assertEqual(summary["free_bays"], 1)
        self.assertEqual(summary["occupied_bays"], 1)
        self.assertEqual(summary["unknown_bays"], 1)
        self.assertFalse(summary["unknown_included_in_free"])
        self.assertFalse(summary["disabled_included_in_usable_capacity"])
        zones = {z["zone"]: z for z in summary["zones"]}
        self.assertEqual(zones["North"]["usable_capacity"], 4)
        self.assertEqual(zones["North"]["unknown_bays"], 1)
        self.assertEqual(zones["North"]["free_bays"], 1)
        self.assertEqual(zones["South"]["usable_capacity"], 0)
        self.assertEqual(zones["South"]["disabled_bays"], 1)


class ZoneAndDisabledBayDraftAndVersionTests(TestCase):
    """API actions and geometry versions preserve historical records when zones/status/capacity change."""

    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user("t18-owner")
        grant(self.user, "labeler")
        self.client.force_login(self.user)
        self.site = Site.objects.create(created_by=self.user, name="Car park")
        self.source = OperationSource.objects.create(site=self.site, name="North cam", kind="recorded_video")
        self.operation = Operation.objects.create(
            created_by=self.user, site=self.site, source=self.source, name="Parking op"
        )
        self.line = {
            "start": [0.2, 0.5],
            "end": [0.8, 0.5],
            "name": "Lane",
            "positive_label": "In",
            "negative_label": "Out",
        }

    def post(self, path, payload, status=201):
        response = self.client.post(path, json.dumps(payload), content_type="application/json")
        self.assertEqual(response.status_code, status, response.content)
        return response.json()

    def test_bay_actions_update_zone_observability_and_capacity_without_changing_historical_snapshots(
        self,
    ) -> None:
        bay = make_bay("Bay A1")
        draft = self.post("/api/operation-drafts", {"name": "North lot", "bays": [bay]})["draft"]
        bay_id = bay["id"]

        # 1. Assign zone.
        after_zone = self.post(
            f"/api/operation-drafts/{draft['id']}/bays/actions",
            {"action": "set_zone", "bay_id": bay_id, "zone": "Level 1 East"},
            status=200,
        )["draft"]
        self.assertEqual(after_zone["bays"][0]["id"], bay_id)
        self.assertEqual(after_zone["bays"][0]["zone"], "Level 1 East")
        self.assertEqual(after_zone["bay_summary"]["zones"][0]["zone"], "Level 1 East")

        # 2. Mark unobservable (distinct from disabled).
        after_unobs = self.post(
            f"/api/operation-drafts/{draft['id']}/bays/actions",
            {
                "action": "set_observability",
                "bay_id": bay_id,
                "observability": "unobservable",
                "disabled_reason": "Pillar blocks camera",
            },
            status=200,
        )["draft"]
        self.assertEqual(after_unobs["bays"][0]["observability"], "unobservable")
        self.assertTrue(after_unobs["bays"][0]["unobservable"])
        self.assertEqual(after_unobs["bay_summary"]["unobservable_bays"], 1)
        self.assertEqual(after_unobs["bay_summary"]["disabled_bays"], 0)
        self.assertEqual(after_unobs["bay_summary"]["unknown_bays"], 1)
        self.assertEqual(after_unobs["bay_summary"]["free_bays"], 0)

        # 3. Mark disabled and set capacity.
        after_dis = self.post(
            f"/api/operation-drafts/{draft['id']}/bays/actions",
            {
                "action": "set_observability",
                "bay_id": bay_id,
                "observability": "disabled",
                "disabled_reason": "Maintenance closure",
            },
            status=200,
        )["draft"]
        self.assertEqual(after_dis["bays"][0]["observability"], "disabled")
        self.assertEqual(after_dis["bay_summary"]["disabled_bays"], 1)
        self.assertEqual(after_dis["bay_summary"]["usable_capacity"], 0)

        after_cap = self.post(
            f"/api/operation-drafts/{draft['id']}/bays/actions",
            {"action": "set_capacity", "bay_id": bay_id, "capacity": 4},
            status=200,
        )["draft"]
        self.assertEqual(after_cap["bays"][0]["capacity"], 4)
        self.assertEqual(after_cap["bay_summary"]["total_capacity"], 4)
        self.assertEqual(after_cap["bay_summary"]["disabled_capacity"], 4)
        self.assertEqual(after_cap["bay_summary"]["usable_capacity"], 0)

        # Historical draft versions are untouched.
        versions = self.client.get(f"/api/operation-drafts/{draft['id']}/versions").json()["versions"]
        self.assertEqual(len(versions), 5)
        oldest_bay = versions[-1]["snapshot"]["geometry"]["bays"][0]
        self.assertEqual(oldest_bay["id"], bay_id)
        self.assertNotIn("zone", oldest_bay)
        self.assertNotIn("observability", oldest_bay)
        self.assertNotIn("capacity", oldest_bay)

    def test_geometry_versions_and_rotation_remap_preserve_zones_disabled_bays_and_capacity(self) -> None:
        view0 = self.post(
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
        )["source_view"]
        bay = make_bay(
            "Bay 1",
            zone="Loading zone",
            observability="unobservable",
            disabled_reason="Roof overhang",
            capacity=2,
        )
        v1 = self.post(
            f"/api/operations/{self.operation.id}/geometry-versions",
            {"source_view_version_id": view0["id"], "geometry": {"line": self.line, "bays": [bay]}},
        )["geometry_version"]
        self.assertEqual(v1["bay_summary"]["total_capacity"], 2)
        self.assertEqual(v1["bay_summary"]["unobservable_bays"], 1)

        # Updating capacity creates a new immutable geometry version without changing v1.
        v2 = self.post(
            f"/api/operations/{self.operation.id}/geometry-versions",
            {
                "source_view_version_id": view0["id"],
                "geometry": {"line": self.line, "bays": [{**bay, "capacity": 5, "observability": "observable"}]},
            },
        )["geometry_version"]
        self.assertEqual(v2["bay_summary"]["usable_capacity"], 5)
        v1_stored = GeometryVersion.objects.get(pk=v1["id"])
        self.assertEqual(v1_stored.geometry["bays"][0]["capacity"], 2)
        self.assertEqual(v1_stored.geometry["bays"][0]["observability"], "unobservable")

        # 90-degree rotation remap preserves zone, observability, reason, and capacity.
        view90 = self.post(
            f"/api/operations/{self.operation.id}/source-views",
            {
                "configuration": {
                    "width": 640,
                    "height": 480,
                    "rotation": 90,
                    "horizontal_flip": False,
                    "vertical_flip": False,
                }
            },
        )["source_view"]
        remapped = self.post(
            f"/api/operations/{self.operation.id}/geometry-versions",
            {"remap": {"from_geometry_version_id": v1["id"], "to_source_view_version_id": view90["id"]}},
        )["geometry_version"]
        remapped_bay = remapped["geometry"]["bays"][0]
        self.assertEqual(remapped_bay["id"], bay["id"])
        self.assertEqual(remapped_bay["zone"], "Loading zone")
        self.assertEqual(remapped_bay["observability"], "unobservable")
        self.assertEqual(remapped_bay["disabled_reason"], "Roof overhang")
        self.assertEqual(remapped_bay["capacity"], 2)

    def test_operations_page_exposes_zone_observability_and_capacity_controls(self) -> None:
        page = self.client.get("/operations")
        self.assertEqual(page.status_code, 200)
        for needle in (
            'id="bay-zone"',
            'data-testid="bay-zone"',
            'id="bay-observability"',
            'data-testid="bay-observability"',
            'id="bay-disabled-reason"',
            'id="bay-capacity"',
            "Unobservable (blocked view — reports as Unknown, never Free)",
            "Disabled (out of service — excluded from usable capacity)",
        ):
            with self.subTest(needle=needle):
                self.assertContains(page, needle)
