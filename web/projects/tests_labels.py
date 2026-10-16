"""Label-schema editor API: PATCH /api/projects/<id> with a new list of labels."""

from __future__ import annotations

import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from .models import Asset, AutoLabelJob, BoxAnnotation, Project


class LabelEditorTestCase(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user("operator")
        self.client.force_login(self.user)
        self.project = Project.objects.create(
            name="Warehouse scan",
            modality="image",
            task="Find items",
            labels=["knife", "laptop", "bottle"],
        )
        self.other = Project.objects.create(
            name="Other", modality="image", task="Find items", labels=["knife"]
        )
        self.asset = self.make_asset(self.project, "one")
        self.other_asset = self.make_asset(self.other, "two")

    @staticmethod
    def make_asset(project: Project, key: str) -> Asset:
        return Asset.objects.create(
            project=project,
            filename=f"{key}.png",
            storage_key=f"test/{project.id}/{key}.png",
            content_type="image/png",
            sha256="0" * 64,
            size_bytes=10,
            width=10,
            height=10,
        )

    @staticmethod
    def box(asset: Asset, label: str, **extra) -> BoxAnnotation:
        return BoxAnnotation.objects.create(
            asset=asset, label=label, x="0.1", y="0.1", width="0.2", height="0.2", **extra
        )

    def url(self, project: Project | None = None) -> str:
        return f"/api/projects/{(project or self.project).id}"

    def patch_labels(self, payload, project: Project | None = None, client: Client | None = None):
        body = payload if isinstance(payload, (str, bytes)) else json.dumps(payload)
        return (client or self.client).patch(
            self.url(project), data=body, content_type="application/json"
        )

    def labels(self) -> list[str]:
        self.project.refresh_from_db()
        return self.project.labels

    def box_labels(self, asset: Asset | None = None) -> list[str]:
        return sorted(
            (asset or self.asset).annotations.values_list("label", flat=True)
        )


class AddRenameReorderTests(LabelEditorTestCase):
    def test_add_a_label_keeps_existing_ones(self) -> None:
        response = self.patch_labels({"labels": ["knife", "laptop", "bottle", "scissors"]})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["project"]["labels"], ["knife", "laptop", "bottle", "scissors"])
        self.assertEqual(self.labels(), ["knife", "laptop", "bottle", "scissors"])

    def test_response_carries_project_counts_and_label_counts(self) -> None:
        self.box(self.asset, "knife")
        self.box(self.asset, "knife")
        project = self.patch_labels({"labels": ["knife", "laptop", "bottle"]}).json()["project"]
        self.assertEqual(project["image_count"], 1)
        self.assertEqual(project["label_counts"], {"knife": 2})

    def test_listing_includes_per_label_box_counts(self) -> None:
        self.box(self.asset, "knife")
        self.box(self.asset, "bottle")
        self.box(self.other_asset, "knife")
        listed = {item["name"]: item for item in self.client.get("/api/projects").json()["projects"]}
        self.assertEqual(listed["Warehouse scan"]["label_counts"], {"knife": 1, "bottle": 1})
        self.assertEqual(listed["Other"]["label_counts"], {"knife": 1})

    def test_listing_is_newest_first(self) -> None:
        from datetime import timedelta

        from django.utils import timezone

        now = timezone.now()
        Project.objects.filter(id=self.project.id).update(created_at=now - timedelta(days=2))
        Project.objects.filter(id=self.other.id).update(created_at=now - timedelta(days=1))
        newest = Project.objects.create(name="Newest", modality="image", task="t", labels=[])
        names = [item["name"] for item in self.client.get("/api/projects").json()["projects"]]
        self.assertEqual(names, [newest.name, "Other", "Warehouse scan"])

    def test_reorder_changes_only_the_order(self) -> None:
        self.box(self.asset, "laptop")
        response = self.patch_labels({"labels": ["bottle", "laptop", "knife"]})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.labels(), ["bottle", "laptop", "knife"])
        self.assertEqual(self.box_labels(), ["laptop"])

    def test_rename_updates_existing_boxes_in_the_same_request(self) -> None:
        self.box(self.asset, "knife")
        self.box(self.asset, "knife", origin="model", status="suggested", confidence=0.5)
        self.box(self.asset, "laptop")
        response = self.patch_labels(
            {"labels": [{"name": "kitchen knife", "was": "knife"}, "laptop", "bottle"]}
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.labels(), ["kitchen knife", "laptop", "bottle"])
        self.assertEqual(self.box_labels(), ["kitchen knife", "kitchen knife", "laptop"])
        self.assertEqual(response.json()["boxes_updated"], 2)
        # Review state of model proposals is untouched by a rename.
        self.assertEqual(
            self.asset.annotations.filter(status="suggested", label="kitchen knife").count(), 1
        )

    def test_rename_does_not_touch_other_projects(self) -> None:
        self.box(self.other_asset, "knife")
        self.patch_labels({"labels": [{"name": "blade", "was": "knife"}, "laptop", "bottle"]})
        self.assertEqual(self.box_labels(self.other_asset), ["knife"])

    def test_swapping_two_names_does_not_merge_their_boxes(self) -> None:
        self.box(self.asset, "knife")
        self.box(self.asset, "laptop")
        self.box(self.asset, "laptop")
        response = self.patch_labels(
            {"labels": [{"name": "laptop", "was": "knife"}, {"name": "knife", "was": "laptop"}, "bottle"]}
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.box_labels(), ["knife", "knife", "laptop"])

    def test_case_only_rename_is_allowed(self) -> None:
        self.box(self.asset, "knife")
        response = self.patch_labels({"labels": [{"name": "Knife", "was": "knife"}, "laptop", "bottle"]})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.labels()[0], "Knife")
        self.assertEqual(self.box_labels(), ["Knife"])

    def test_names_are_trimmed(self) -> None:
        self.patch_labels({"labels": ["  knife ", "laptop", "bottle", "  drill\t"]})
        self.assertEqual(self.labels(), ["knife", "laptop", "bottle", "drill"])

    def test_sending_the_same_labels_is_a_harmless_no_op(self) -> None:
        self.box(self.asset, "knife")
        response = self.patch_labels({"labels": ["knife", "laptop", "bottle"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["boxes_updated"], 0)
        self.assertEqual(self.box_labels(), ["knife"])

    def test_clearing_every_label_is_allowed_when_no_box_uses_them(self) -> None:
        self.assertEqual(self.patch_labels({"labels": []}).status_code, 200)
        self.assertEqual(self.labels(), [])


class RemoveTests(LabelEditorTestCase):
    def test_removing_an_unused_label_works(self) -> None:
        response = self.patch_labels({"labels": ["knife", "laptop"]})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.labels(), ["knife", "laptop"])

    def test_removing_a_label_that_still_has_boxes_is_refused_with_the_count(self) -> None:
        self.box(self.asset, "bottle")
        self.box(self.asset, "bottle")
        self.box(self.asset, "bottle")
        response = self.patch_labels({"labels": ["knife", "laptop"]})
        self.assertEqual(response.status_code, 409)
        body = response.json()
        self.assertIn("bottle", body["error"])
        self.assertIn("3 boxes", body["error"])
        self.assertEqual(body["in_use"], {"bottle": 3})
        self.assertEqual(self.labels(), ["knife", "laptop", "bottle"])
        self.assertEqual(self.box_labels(), ["bottle"] * 3)

    def test_singular_count_reads_naturally(self) -> None:
        self.box(self.asset, "bottle")
        body = self.patch_labels({"labels": ["knife", "laptop"]}).json()
        self.assertIn("1 box", body["error"])
        self.assertNotIn("1 boxes", body["error"])

    def test_message_names_every_blocking_label(self) -> None:
        self.box(self.asset, "bottle")
        self.box(self.asset, "laptop")
        self.box(self.asset, "laptop")
        body = self.patch_labels({"labels": ["knife"]}).json()
        self.assertEqual(body["in_use"], {"laptop": 2, "bottle": 1})
        self.assertIn("laptop", body["error"])
        self.assertIn("bottle", body["error"])

    def test_other_projects_boxes_do_not_block_removal(self) -> None:
        self.box(self.other_asset, "bottle")
        self.assertEqual(self.patch_labels({"labels": ["knife", "laptop"]}).status_code, 200)

    def test_reassign_moves_boxes_to_another_label_then_removes(self) -> None:
        self.box(self.asset, "bottle")
        self.box(self.asset, "bottle", origin="model", status="suggested", confidence=0.7)
        response = self.patch_labels(
            {"labels": ["knife", "laptop"], "reassign": {"bottle": "laptop"}}
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.labels(), ["knife", "laptop"])
        self.assertEqual(self.box_labels(), ["laptop", "laptop"])
        self.assertEqual(response.json()["boxes_updated"], 2)
        self.assertEqual(self.asset.annotations.filter(status="suggested").count(), 1)

    def test_reassign_target_can_be_a_renamed_label_using_its_new_name(self) -> None:
        self.box(self.asset, "bottle")
        response = self.patch_labels(
            {
                "labels": [{"name": "blade", "was": "knife"}, "laptop"],
                "reassign": {"bottle": "blade"},
            }
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.box_labels(), ["blade"])

    def test_reassign_target_must_be_in_the_new_list(self) -> None:
        self.box(self.asset, "bottle")
        response = self.patch_labels(
            {"labels": ["knife", "laptop"], "reassign": {"bottle": "nonexistent"}}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.box_labels(), ["bottle"])
        self.assertEqual(self.labels(), ["knife", "laptop", "bottle"])

    def test_reassign_cannot_target_a_removed_label(self) -> None:
        self.box(self.asset, "bottle")
        response = self.patch_labels(
            {"labels": ["knife"], "reassign": {"bottle": "laptop"}}
        )
        self.assertEqual(response.status_code, 400)

    def test_reassign_source_must_be_a_removed_label(self) -> None:
        response = self.patch_labels(
            {"labels": ["knife", "laptop", "bottle"], "reassign": {"bottle": "laptop"}}
        )
        self.assertEqual(response.status_code, 400)

    def test_reassign_must_be_a_mapping_of_text(self) -> None:
        for bad in (["bottle"], {"bottle": 3}, "bottle"):
            with self.subTest(bad=bad):
                response = self.patch_labels({"labels": ["knife", "laptop"], "reassign": bad})
                self.assertEqual(response.status_code, 400)

    def test_partial_reassign_still_refuses_the_label_left_without_a_target(self) -> None:
        self.box(self.asset, "bottle")
        self.box(self.asset, "laptop")
        response = self.patch_labels({"labels": ["knife"], "reassign": {"bottle": "knife"}})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["in_use"], {"laptop": 1})
        self.assertEqual(self.box_labels(), ["bottle", "laptop"])


class ValidationTests(LabelEditorTestCase):
    def assert_rejected(self, payload, *, fragment: str = "", status: int = 400) -> None:
        response = self.patch_labels(payload)
        self.assertEqual(response.status_code, status, response.content)
        self.assertTrue(response.json()["error"])
        if fragment:
            self.assertIn(fragment.lower(), response.json()["error"].lower())
        self.assertEqual(self.labels(), ["knife", "laptop", "bottle"])

    def test_empty_and_whitespace_names(self) -> None:
        self.assert_rejected({"labels": ["knife", ""]}, fragment="empty")
        self.assert_rejected({"labels": ["knife", "   "]}, fragment="empty")

    def test_duplicates_are_rejected_ignoring_case_and_padding(self) -> None:
        self.assert_rejected({"labels": ["knife", "KNIFE"]}, fragment="twice")
        self.assert_rejected({"labels": ["knife", " knife "]}, fragment="twice")

    def test_rename_into_an_existing_name_is_a_duplicate(self) -> None:
        self.assert_rejected(
            {"labels": [{"name": "Laptop", "was": "knife"}, "laptop", "bottle"]}, fragment="twice"
        )

    def test_name_length_cap(self) -> None:
        self.assert_rejected({"labels": ["x" * 65]}, fragment="64")
        self.assertEqual(self.patch_labels({"labels": ["x" * 64]}).status_code, 200)

    def test_label_count_cap(self) -> None:
        names = [f"label {number}" for number in range(51)]
        self.assert_rejected({"labels": names}, fragment="50")
        self.assertEqual(self.patch_labels({"labels": names[:50]}).status_code, 200)
        self.assertEqual(len(self.labels()), 50)

    def test_names_must_be_printable_text(self) -> None:
        self.assert_rejected({"labels": ["bad\x00name"]})
        self.assert_rejected({"labels": ["two\nlines"]})

    def test_wrong_types(self) -> None:
        self.assert_rejected({"labels": "knife"})
        self.assert_rejected({"labels": [3]})
        self.assert_rejected({"labels": [None]})
        self.assert_rejected({"labels": [["knife"]]})
        self.assert_rejected({"labels": [{"name": 3, "was": "knife"}]})
        self.assert_rejected({"labels": [{"name": "x", "was": 3}]})
        self.assert_rejected({"labels": [{"name": "x", "extra": 1}]})
        self.assert_rejected({"labels": [{"was": "knife"}]})

    def test_labels_are_required_and_other_fields_unsupported(self) -> None:
        self.assert_rejected({})
        self.assert_rejected({"labels": ["knife"], "name": "New name"}, fragment="name")
        self.assert_rejected({"labels": ["knife"], "modality": "audio"})

    def test_referring_to_an_unknown_or_repeated_old_label(self) -> None:
        self.assert_rejected({"labels": [{"name": "x", "was": "ghost"}]}, fragment="reload")
        self.assert_rejected(
            {"labels": [{"name": "a", "was": "knife"}, {"name": "b", "was": "knife"}]}
        )
        self.assert_rejected({"labels": ["knife", {"name": "again", "was": "knife"}]})

    def test_body_must_be_json_object(self) -> None:
        self.assert_rejected("{broken", fragment="json")
        self.assert_rejected(["knife"])
        response = self.client.patch(self.url(), data="labels=x", content_type="text/plain")
        self.assertEqual(response.status_code, 415)

    def test_oversized_body(self) -> None:
        response = self.patch_labels(json.dumps({"labels": ["a" * 64] * 30000}))
        self.assertEqual(response.status_code, 413)

    def test_errors_are_plain_language_and_never_expose_internals(self) -> None:
        for payload in ({"labels": [""]}, {"labels": "x"}, {}):
            error = self.patch_labels(payload).json()["error"]
            self.assertNotIn("Traceback", error)
            self.assertNotIn("labels:", error)
            self.assertNotIn("JSON", error.replace("valid JSON", ""))


class SafetyTests(LabelEditorTestCase):
    def test_database_failure_rolls_back_renamed_boxes(self) -> None:
        self.box(self.asset, "knife")
        with patch.object(Project, "save", side_effect=RuntimeError("disk full")):
            with self.assertRaises(RuntimeError):
                self.patch_labels({"labels": [{"name": "blade", "was": "knife"}, "laptop", "bottle"]})
        self.assertEqual(self.box_labels(), ["knife"])
        self.assertEqual(self.labels(), ["knife", "laptop", "bottle"])

    def _active_job(self) -> AutoLabelJob:
        return AutoLabelJob.objects.create(project=self.project, status=AutoLabelJob.RUNNING)

    def test_rename_and_remove_wait_while_auto_suggest_is_running(self) -> None:
        self._active_job()
        renamed = self.patch_labels({"labels": [{"name": "blade", "was": "knife"}, "laptop", "bottle"]})
        removed = self.patch_labels({"labels": ["knife", "laptop"]})
        for response in (renamed, removed):
            self.assertEqual(response.status_code, 409)
            self.assertIn("auto", response.json()["error"].lower())
        self.assertEqual(self.labels(), ["knife", "laptop", "bottle"])

    def test_adding_and_reordering_are_fine_while_a_job_runs(self) -> None:
        self._active_job()
        self.assertEqual(self.patch_labels({"labels": ["bottle", "knife", "laptop", "new"]}).status_code, 200)

    def test_finished_jobs_do_not_block_edits(self) -> None:
        AutoLabelJob.objects.create(project=self.project, status=AutoLabelJob.SUCCEEDED)
        renamed = self.patch_labels({"labels": [{"name": "blade", "was": "knife"}, "laptop", "bottle"]})
        self.assertEqual(renamed.status_code, 200)

    def test_unknown_project_is_404(self) -> None:
        response = self.client.patch(
            "/api/projects/00000000-0000-0000-0000-000000000000",
            data=json.dumps({"labels": []}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)

    def test_requires_sign_in_and_csrf(self) -> None:
        anonymous = Client()
        denied = anonymous.patch(
            self.url(), data=json.dumps({"labels": []}), content_type="application/json"
        )
        self.assertEqual(denied.status_code, 401)

        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.user)
        no_token = strict.patch(
            self.url(), data=json.dumps({"labels": ["knife"]}), content_type="application/json"
        )
        self.assertEqual(no_token.status_code, 403)
        strict.get("/")
        token = strict.cookies["csrftoken"].value
        allowed = strict.patch(
            self.url(),
            data=json.dumps({"labels": ["knife"]}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(allowed.status_code, 200, allowed.content)

    def test_other_methods_still_behave(self) -> None:
        self.assertEqual(self.client.get(self.url()).status_code, 405)
        self.assertEqual(self.client.put(self.url()).status_code, 405)
        self.assertEqual(self.client.delete(self.url()).status_code, 200)
        self.assertFalse(Project.objects.filter(id=self.project.id).exists())
