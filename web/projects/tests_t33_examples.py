"""T33: event-linked learning examples and verified negatives.

Acceptance: examples retain recording/time/site provenance and authorized
evidence; selections dedupe; explicitly verified negatives are supported.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import OperationExecution, OperationLearningExample
from .operations import examples as learning_examples
from .tests import grant
from .tests_t19_durable import DurableExecutionBase


class LearningExamplesUnitTests(TestCase):
    def test_content_identity_stable_and_distinct(self):
        a = learning_examples.content_identity(
            operation_id="11111111-1111-1111-1111-111111111111",
            recording_sha256="a" * 64,
            time_pts=1.25,
            polarity="positive",
            selection_kind="miss",
            event_key="miss|bay-1",
        )
        b = learning_examples.content_identity(
            operation_id="11111111-1111-1111-1111-111111111111",
            recording_sha256="a" * 64,
            time_pts=1.2500001,  # same microsecond quantum
            polarity="positive",
            selection_kind="miss",
            event_key="miss|bay-1",
        )
        c = learning_examples.content_identity(
            operation_id="11111111-1111-1111-1111-111111111111",
            recording_sha256="a" * 64,
            time_pts=1.25,
            polarity="negative",
            selection_kind="extra",
            event_key="extra|bay-1",
        )
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertEqual(len(a), 64)

    def test_candidates_skip_non_recognition(self):
        diagnosis = {
            "recommend_training": False,
            "comparison": {
                "items": [
                    {"kind": "miss", "category": "geometry", "time": 1.0, "reason": "line"},
                    {"kind": "miss", "category": "recognition", "time": 2.0, "reason": "missed"},
                    {"kind": "extra", "category": "recognition", "time": 3.0, "reason": "fp"},
                ]
            },
            "parking": {
                "items": [
                    {"kind": "false_free", "category": "source_view", "time": 4.0, "bay_id": "b1"},
                    {"kind": "false_free", "category": "recognition", "time": 5.0, "bay_id": "b2"},
                ]
            },
        }
        cands = learning_examples.candidates_from_diagnosis(diagnosis)
        self.assertEqual(len(cands), 3)
        kinds = {(c["selection_kind"], c["polarity"], c["time_pts"]) for c in cands}
        self.assertIn(("miss", "positive", 2.0), kinds)
        self.assertIn(("extra", "negative", 3.0), kinds)
        self.assertIn(("extra", "negative", 5.0), kinds)


class LearningExamplesApiTests(DurableExecutionBase):
    def setUp(self):
        super().setUp()
        # DurableExecutionBase already creates owner/site/source/operation/recording/bundle.
        self.execution = OperationExecution.objects.create(
            bundle=self.bundle,
            recording=self.recording,
            created_by=self.user,
            bundle_sha256=self.bundle.snapshot_sha256,
            recording_sha256=self.recording.sha256,
            pins={},
            status=OperationExecution.SUCCEEDED,
            result={
                "diagnosis": {
                    "schema": "t32.execution_diagnosis.v1",
                    "recommend_training": True,
                    "primary_category": "recognition",
                    "comparison": {
                        "primary_category": "recognition",
                        "recommend_training": True,
                        "items": [
                            {
                                "kind": "miss",
                                "category": "recognition",
                                "time": 0.4,
                                "reason": "no_nearby_detection",
                                "direction": "in",
                            },
                            {
                                "kind": "extra",
                                "category": "recognition",
                                "time": 0.8,
                                "reason": "unmatched_prediction",
                            },
                            {
                                "kind": "miss",
                                "category": "geometry",
                                "time": 0.2,
                                "reason": "line_misaligned",
                            },
                        ],
                    },
                }
            },
        )

    def test_capture_from_execution_dedupes_and_keeps_provenance(self):
        url = f"/api/operation-executions/{self.execution.id}/learning-examples/capture"
        r1 = self.client.post(url, data=b"{}", content_type="application/json")
        self.assertEqual(r1.status_code, 201, r1.content)
        body = r1.json()
        self.assertEqual(body["counts"]["created"], 2)  # geometry skipped
        self.assertEqual(body["counts"]["existing"], 0)

        r2 = self.client.post(url, data=b"{}", content_type="application/json")
        self.assertEqual(r2.status_code, 200, r2.content)
        body2 = r2.json()
        self.assertEqual(body2["counts"]["created"], 0)
        self.assertEqual(body2["counts"]["existing"], 2)

        rows = list(OperationLearningExample.objects.filter(operation=self.operation).order_by("time_pts"))
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(row.site_id, self.site.id)
            self.assertEqual(row.recording_sha256, self.recording.sha256)
            self.assertEqual(row.bundle_sha256, self.bundle.snapshot_sha256)
            self.assertEqual(row.execution_id, self.execution.id)
            self.assertIn("diagnosis_item", row.evidence)
            self.assertNotIn("password", str(row.evidence).lower())
        polarities = {r.polarity for r in rows}
        self.assertEqual(polarities, {"positive", "negative"})

    def test_verified_negative_and_list_filter(self):
        payload = {
            "time_pts": 0.55,
            "verified_negative": True,
            "recording_id": str(self.recording.id),
            "event_identity": {"bay_id": "bay-empty-1"},
            "note": "Empty bay confirmed on review",
            "evidence": {"frame_ref": "pts:0.55", "secret_token": "should-strip"},
        }
        r = self.client.post(
            f"/api/operations/{self.operation.id}/learning-examples",
            data=payload,
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 201, r.content)
        example = r.json()["example"]
        self.assertEqual(example["polarity"], "negative")
        self.assertEqual(example["selection_kind"], "verified_negative")
        self.assertEqual(example["status"], "verified")
        self.assertEqual(example["site_id"], str(self.site.id))
        self.assertEqual(example["recording_sha256"], self.recording.sha256)
        self.assertNotIn("secret_token", example["evidence"])

        # Dedupe same verified negative
        r2 = self.client.post(
            f"/api/operations/{self.operation.id}/learning-examples",
            data=payload,
            content_type="application/json",
        )
        self.assertEqual(r2.status_code, 200, r2.content)
        self.assertFalse(r2.json()["created"])
        self.assertEqual(OperationLearningExample.objects.filter(operation=self.operation).count(), 1)

        listed = self.client.get(
            f"/api/operations/{self.operation.id}/learning-examples?status=verified&polarity=negative"
        )
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["count"], 1)

    def test_status_verify_requires_trainer_and_owner_scope(self):
        row, _ = learning_examples.add_example(
            operation=self.operation,
            user=self.user,
            time_pts=0.1,
            polarity="positive",
            selection_kind="miss",
            recording=self.recording,
            event_identity={"kind": "miss"},
        )
        # Superuser fixture already has approver; train role is covered by grant.
        grant(self.user, "trainer")
        r = self.client.post(
            f"/api/operation-learning-examples/{row.id}/status",
            data={"status": "verified"},
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["example"]["status"], "verified")

        other = get_user_model().objects.create_user("t33-other", is_superuser=True)
        grant(other, "approver")
        self.client.force_login(other)
        blocked = self.client.get(f"/api/operations/{self.operation.id}/learning-examples")
        self.assertEqual(blocked.status_code, 404)

    def test_capture_refuses_non_succeeded(self):
        self.execution.status = OperationExecution.RUNNING
        self.execution.result = {}
        self.execution.save(update_fields=["status", "result"])
        r = self.client.post(
            f"/api/operation-executions/{self.execution.id}/learning-examples/capture",
            data=b"{}",
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 400)
