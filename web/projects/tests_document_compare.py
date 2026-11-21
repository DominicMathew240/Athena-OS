"""HTTP contract for the OS-DOC-001 table comparison.

GET ``/api/projects/<uuid>/datasets/compare?reference=<dataset-uuid>&candidate=<dataset-uuid>``
returns ``{"comparison": <document_eval.compare_versions block>}`` and stores nothing.
Invalid input is ``400 {"error": "..."}``. Only a document-fields project can be compared,
and both versions must belong to that project. Versions are listed by the existing
datasets endpoint; this module does not add another list.
"""

from __future__ import annotations

import json
import uuid

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from .datasets import generic as generic_service
from .datasets import splits
from .models import Asset, AssetAnnotation, DatasetEntry, DatasetVersion, ExportEvent, Project, ProjectRole
from .tests import grant
from .tests_document import DocumentTestBase, csv_bytes, document_rows
from .training import document_eval

LEAK_MARKER = "LEAK-MARKER-77"
NOT_IN_PROJECT = "Both dataset versions must belong to this project."


class DocumentCompareApiTests(DocumentTestBase):
    """The frozen compare route: one read, no new rows, no cross-project cells."""

    def compare_path(self, reference, candidate, project=None) -> str:
        project = project or self.document_project
        return (
            f"/api/projects/{project.id}/datasets/compare"
            f"?reference={reference}&candidate={candidate}"
        )

    def make_version_on(self, project, records, header=None) -> DatasetVersion:
        """Freeze one CSV table on ``project`` (the document project's own helper stays put)."""
        content = csv_bytes(records, header=header)
        for asset in Asset.objects.filter(project=project):
            asset.delete()
        upload = SimpleUploadedFile("documents.csv", content, content_type="text/csv")
        response = self.client.post(f"/api/projects/{project.id}/assets", {"images": upload})
        self.assertEqual(response.status_code, 201, response.content)
        asset = response.json()["assets"][0]
        response = self.client.post(
            f"/api/projects/{project.id}/assets/{asset['id']}/annotations",
            data=json.dumps({"label": project.labels[0]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        analysis = generic_service.analyse(project, splits.SplitConfig.default())
        self.assertTrue(analysis.ready, analysis.blockers)
        return generic_service.create_version(analysis, self.user)

    def saved_rows(self) -> tuple[int, ...]:
        return (
            Project.objects.count(),
            Asset.objects.count(),
            AssetAnnotation.objects.count(),
            DatasetVersion.objects.count(),
            DatasetEntry.objects.count(),
            ExportEvent.objects.count(),
            ProjectRole.objects.count(),
        )

    def test_two_csv_versions_compare_through_the_frozen_response(self) -> None:
        reference_rows = document_rows(4)
        candidate_rows = document_rows(4)
        reference_rows[0]["issued"] = candidate_rows[0]["issued"] = "03/04/05"
        candidate_rows[1]["supplier"] = "Totally Different Ltd"
        reference = self.make_version(reference_rows)
        candidate = self.make_version(candidate_rows)

        listed = {row["id"] for row in self.client.get(self.api("/datasets")).json()["datasets"]}
        self.assertEqual(listed, {str(reference.id), str(candidate.id)})

        response = self.client.get(self.compare_path(reference.id, candidate.id))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response["Cache-Control"], "no-store")
        body = response.json()
        self.assertEqual(set(body), {"comparison"})
        block = document_eval.compare_versions(reference, candidate, self.document_project)
        self.assertEqual(body["comparison"], block)
        self.assertEqual(body["comparison"]["mismatches"][0]["candidate"], "Totally Different Ltd")
        self.assertEqual(body["comparison"]["mismatches"][0]["reference"], reference_rows[1]["supplier"])
        self.assertGreater(body["comparison"]["totals"]["unclear"], 0)
        self.assertIn("not a promise about future documents", body["comparison"]["plain"])
        self.assertNotIn("comparison", body["comparison"])

    def test_the_capped_mismatch_list_and_extra_rows_are_the_library_block(self) -> None:
        reference_rows = document_rows(60)
        candidate_rows = [{**record, "supplier": "Wrong"} for record in reference_rows]
        reference = self.make_version(reference_rows)
        candidate = self.make_version(candidate_rows)
        response = self.client.get(self.compare_path(reference.id, candidate.id))
        self.assertEqual(response.status_code, 200, response.content)
        block = document_eval.compare_versions(reference, candidate, self.document_project)
        self.assertEqual(response.json()["comparison"], block)
        self.assertEqual(block["mismatch_count"], 60)
        self.assertTrue(block["mismatches_truncated"])
        self.assertEqual(len(block["mismatches"]), document_eval.MAX_MISMATCHES)

        shorter = self.make_version(document_rows(5))
        response = self.client.get(self.compare_path(reference.id, shorter.id))
        self.assertEqual(response.status_code, 200, response.content)
        block = document_eval.compare_versions(reference, shorter, self.document_project)
        self.assertEqual(response.json()["comparison"], block)
        self.assertIn(document_eval.EXTRA_ROWS_NOTE, block["notes"])

    def test_invalid_query_and_incomparable_versions_are_json_400(self) -> None:
        base = f"/api/projects/{self.document_project.id}/datasets/compare"
        version = self.make_version(document_rows(3))
        missing = (
            base,
            f"{base}?reference={version.id}",
            f"{base}?candidate={version.id}",
            f"{base}?reference=%20%20&candidate=%20",
        )
        for path in missing:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    response.json(),
                    {"error": "Provide both reference and candidate dataset version ids."},
                )

        bad = (
            f"{base}?reference=not-a-uuid&candidate=also-bad",
            f"{base}?reference={version.id}&candidate=not-a-uuid",
        )
        for path in bad:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    response.json(),
                    {"error": "reference and candidate must be dataset version ids."},
                )

        same = self.client.get(self.compare_path(version.id, version.id))
        self.assertEqual(same.status_code, 400)
        self.assertEqual(same.json(), {"error": "Choose two different dataset versions to compare."})

        renamed = [
            {"a": record["invoice_number"], "b": record["amount"], "c": record["issued"], "d": record["supplier"]}
            for record in document_rows(3)
        ]
        other = self.make_version(renamed, header=["a", "b", "c", "d"])
        headers = self.client.get(self.compare_path(version.id, other.id))
        self.assertEqual(headers.status_code, 400)
        self.assertEqual(set(headers.json()), {"error"})
        self.assertIn("headers", headers.json()["error"])
        self.assertNotIn("comparison", headers.json())

    def test_a_dataset_from_another_project_is_refused_without_its_cells(self) -> None:
        local_rows = document_rows(3)
        local_rows[0]["supplier"] = "LOCAL-ONLY-ACME"
        local = self.make_version(local_rows)
        foreign = Project.objects.create(
            name="Foreign Ledger Project",
            modality="text",
            task="Check fields in document tables",
            recipe="document-fields",
            labels=list(self.fields),
        )
        leaked = document_rows(3)
        leaked[0]["supplier"] = LEAK_MARKER
        foreign_version = self.make_version_on(foreign, leaked)

        unknown = self.client.get(self.compare_path(uuid.uuid4(), uuid.uuid4()))
        mixed = self.client.get(self.compare_path(local.id, foreign_version.id))
        both_foreign = self.client.get(self.compare_path(foreign_version.id, uuid.uuid4()))
        through_foreign = self.client.get(self.compare_path(local.id, foreign_version.id, project=foreign))
        for response in (unknown, mixed, both_foreign, through_foreign):
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json(), {"error": NOT_IN_PROJECT})
            text = response.content.decode()
            self.assertNotIn(LEAK_MARKER, text)
            self.assertNotIn("LOCAL-ONLY-ACME", text)
            self.assertNotIn("Foreign Ledger Project", text)
        self.assertEqual(unknown.json(), mixed.json())
        self.assertFalse(ExportEvent.objects.exists())

    def test_other_recipes_are_refused(self) -> None:
        text_project = Project.objects.create(
            name="Classify notes",
            modality="text",
            task="Tell texts apart",
            recipe="text-classification",
            labels=["billing", "support"],
        )
        for project in (self.project, text_project):
            with self.subTest(recipe=project.recipe):
                response = self.client.get(self.compare_path(uuid.uuid4(), uuid.uuid4(), project=project))
                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    response.json(),
                    {"error": "Version comparison is only available for a document-fields project."},
                )

    def test_an_unknown_project_is_not_found(self) -> None:
        response = self.client.get(
            f"/api/projects/{uuid.uuid4()}/datasets/compare?reference={uuid.uuid4()}&candidate={uuid.uuid4()}"
        )
        self.assertEqual(response.status_code, 404)

    def test_a_viewer_may_read_a_comparison_and_nothing_is_saved(self) -> None:
        reference = self.make_version(document_rows(4))
        candidate = self.make_version(document_rows(4))
        path = self.compare_path(reference.id, candidate.id)
        expected = document_eval.compare_versions(reference, candidate, self.document_project)

        viewer = get_user_model().objects.create_user("compare-viewer")
        grant(viewer, "viewer")
        viewer_client = Client()
        viewer_client.force_login(viewer)

        scoped = get_user_model().objects.create_user("compare-scoped")
        grant(scoped, "approver")
        ProjectRole.objects.create(project=self.document_project, user=scoped, role="viewer")
        scoped_client = Client()
        scoped_client.force_login(scoped)

        before = self.saved_rows()
        for client in (viewer_client, scoped_client, self.client):
            response = client.get(path)
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(response.json()["comparison"], expected)
        denied = Client().get(path)
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(denied.json(), {"error": "Sign in to continue."})
        self.assertNotIn("comparison", denied.json())
        self.assertEqual(self.saved_rows(), before)

        refused = self.client.get(self.compare_path(uuid.uuid4(), uuid.uuid4()))
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(self.saved_rows(), before)

    def test_writes_are_rejected(self) -> None:
        path = self.compare_path(uuid.uuid4(), uuid.uuid4())
        for method in ("post", "put", "patch", "delete"):
            with self.subTest(method=method):
                kwargs = {"data": "{}", "content_type": "application/json"} if method in ("post", "put", "patch") else {}
                self.assertEqual(getattr(self.client, method)(path, **kwargs).status_code, 405)
