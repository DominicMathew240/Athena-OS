from __future__ import annotations

import json
import tempfile
import uuid
from datetime import timedelta
from io import BytesIO, StringIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image

from .autolabel import runner
from .autolabel.base import Proposal, normalized_box
from .autolabel.sam_clip import box_iou, mask_bounds, non_max_suppression, suppress_contained
from .models import Asset, AutoLabelJob, BoxAnnotation, Project
from .tests import SignedInMixin

import numpy as np


class FakeLabeler:
    """Deterministic stand-in for SAM + CLIP; the real stack is exercised manually."""

    name = "fake-labeler"
    calls = 0
    boxes: list[Proposal] = []

    def __init__(self, config: dict | None = None) -> None:
        pass

    def propose(self, image, labels, *, min_confidence, max_boxes):
        type(self).calls += 1
        return [box for box in type(self).boxes if box.confidence >= min_confidence][:max_boxes]


class ExplodingLabeler:
    name = "exploding"

    def propose(self, image, labels, *, min_confidence, max_boxes):
        raise RuntimeError("model weights missing")


def png_upload(name: str = "item.png") -> SimpleUploadedFile:
    buffer = BytesIO()
    Image.new("RGB", (40, 30), color=(200, 40, 40)).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


class AutoLabelBase(SignedInMixin, TestCase):
    def setUp(self) -> None:
        self.sign_in()
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        settings = override_settings(
            MEDIA_ROOT=self.media.name,
            AUTOLABEL={
                "BACKEND": "projects.tests_autolabel:FakeLabeler",
                "SAM_MODEL": "sam",
                "CLASSIFIER_MODEL": "clip",
                "DEVICE": "cpu",
                "POINTS_PER_SIDE": 4,
                "JOB_TIMEOUT_SECONDS": 60,
                "ALLOW_DOWNLOAD": False,
            },
            CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
        )
        settings.enable()
        self.addCleanup(settings.disable)
        from django.core.cache import cache

        cache.clear()
        FakeLabeler.calls = 0
        FakeLabeler.boxes = [
            Proposal(0.1, 0.1, 0.3, 0.3, "knife", 0.9),
            Proposal(0.5, 0.5, 0.2, 0.2, "laptop", 0.46),
            Proposal(0.7, 0.1, 0.2, 0.2, "knife", 0.2),
        ]
        self.project = Project.objects.create(
            name="Scanner", modality="image", task="Detection", labels=["knife", "laptop"]
        )
        self.assets = [self.upload() for _ in range(2)]

    def upload(self) -> Asset:
        response = self.client.post(
            f"/api/projects/{self.project.id}/assets", data={"images": png_upload()}
        )
        self.assertEqual(response.status_code, 201)
        return Asset.objects.get(id=response.json()["assets"][0]["id"])

    def post_json(self, url: str, payload: dict | None = None, client=None):
        return (client or self.client).post(
            url, data=json.dumps(payload or {}), content_type="application/json"
        )

    @property
    def autolabel_url(self) -> str:
        return f"/api/projects/{self.project.id}/autolabel"

    def run_next(self, labeler=None) -> AutoLabelJob:
        job = runner.claim_next_job()
        self.assertIsNotNone(job)
        return runner.run_job(job, labeler or FakeLabeler())


class AutoLabelApiTests(AutoLabelBase):
    def test_status_reports_backend_and_worker_heartbeat(self) -> None:
        body = self.client.get("/api/autolabel").json()
        self.assertTrue(body["enabled"])
        self.assertFalse(body["worker_online"])
        runner.record_heartbeat()
        self.assertTrue(self.client.get("/api/autolabel").json()["worker_online"])

    def test_status_distinguishes_a_worker_still_loading_models(self) -> None:
        runner.record_heartbeat(runner.WORKER_LOADING)
        body = self.client.get("/api/autolabel").json()
        self.assertTrue(body["worker_online"])
        self.assertTrue(body["worker_loading"])
        runner.record_heartbeat()
        self.assertFalse(self.client.get("/api/autolabel").json()["worker_loading"])

    def test_disabled_backend_refuses_new_jobs(self) -> None:
        with override_settings(AUTOLABEL={**self.settings_snapshot(), "BACKEND": "disabled"}):
            self.assertFalse(self.client.get("/api/autolabel").json()["enabled"])
            self.assertEqual(self.post_json(self.autolabel_url).status_code, 503)

    def settings_snapshot(self) -> dict:
        from django.conf import settings

        return dict(settings.AUTOLABEL)

    def test_enqueue_returns_queued_job_with_defaults(self) -> None:
        response = self.post_json(self.autolabel_url)
        self.assertEqual(response.status_code, 202)
        job = response.json()["job"]
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["scope"], "project")
        self.assertEqual(job["min_confidence"], 0.45)
        self.assertEqual(AutoLabelJob.objects.get().created_by, self.user)

    def test_second_active_job_for_same_project_is_a_conflict(self) -> None:
        self.assertEqual(self.post_json(self.autolabel_url).status_code, 202)
        again = self.post_json(self.autolabel_url)
        self.assertEqual(again.status_code, 409)
        self.assertIn("already", again.json()["error"])

    def test_requires_labels_and_images(self) -> None:
        self.project.labels = []
        self.project.save()
        self.assertEqual(self.post_json(self.autolabel_url).status_code, 400)
        self.project.labels = ["knife"]
        self.project.save()
        self.project.assets.all().delete()
        self.assertEqual(self.post_json(self.autolabel_url).status_code, 400)

    def test_rejects_bad_parameters_and_unknown_fields(self) -> None:
        for payload in (
            {"min_confidence": 1.5},
            {"min_confidence": "high"},
            {"min_confidence": True},
            {"max_boxes": 0},
            {"max_boxes": 1000},
            {"only_unlabeled": "yes"},
            {"asset_id": "not-a-uuid"},
            {"status": "accepted"},
        ):
            with self.subTest(payload=payload):
                self.assertEqual(self.post_json(self.autolabel_url, payload).status_code, 400)
        self.assertEqual(AutoLabelJob.objects.count(), 0)

    def test_asset_from_another_project_is_not_found(self) -> None:
        other = Project.objects.create(name="Other", modality="image", task="t", labels=["a"])
        foreign = Asset.objects.create(
            project=other, filename="x.png", storage_key="x", content_type="image/png",
            sha256="0" * 64, size_bytes=1, width=1, height=1,
        )
        response = self.post_json(self.autolabel_url, {"asset_id": str(foreign.id)})
        self.assertEqual(response.status_code, 404)

    def test_non_image_project_is_not_found(self) -> None:
        audio = Project.objects.create(name="Mics", modality="audio", task="t", labels=["a"])
        self.assertEqual(self.post_json(f"/api/projects/{audio.id}/autolabel").status_code, 404)

    def test_cancel_queued_job_frees_the_project(self) -> None:
        job_id = self.post_json(self.autolabel_url).json()["job"]["id"]
        cancelled = self.post_json(f"{self.autolabel_url}/{job_id}/cancel")
        self.assertEqual(cancelled.json()["job"]["status"], "cancelled")
        self.assertEqual(self.post_json(self.autolabel_url).status_code, 202)

    def test_cancel_unknown_job_is_not_found(self) -> None:
        self.assertEqual(
            self.post_json(f"{self.autolabel_url}/{uuid.uuid4()}/cancel").status_code, 404
        )

    def test_listing_shows_jobs_and_pending_count(self) -> None:
        self.post_json(self.autolabel_url)
        self.run_next()
        body = self.client.get(self.autolabel_url).json()
        self.assertEqual(body["jobs"][0]["status"], "succeeded")
        self.assertEqual(body["pending_boxes"], 4)  # 2 boxes >= 0.35 on each of 2 images

    def test_writes_require_csrf(self) -> None:
        client = self.csrf_client()
        client.get("/")
        self.assertEqual(self.post_json(self.autolabel_url, client=client).status_code, 403)


class AutoLabelRunnerTests(AutoLabelBase):
    def test_job_creates_reviewable_suggestions_with_provenance(self) -> None:
        self.post_json(self.autolabel_url, {"min_confidence": 0.45})
        job = self.run_next()

        self.assertEqual(job.status, "succeeded")
        self.assertEqual((job.total_assets, job.processed_assets, job.boxes_proposed), (2, 2, 4))
        boxes = BoxAnnotation.objects.filter(asset=self.assets[0])
        self.assertEqual(boxes.count(), 2)
        for box in boxes:
            self.assertEqual(box.origin, "model")
            self.assertEqual(box.status, "suggested")
            self.assertEqual(box.model_name, "fake-labeler")
            self.assertEqual(box.job_id, job.id)
            self.assertIsNone(box.reviewed_by)
        self.assertEqual(set(boxes.values_list("label", flat=True)), {"knife", "laptop"})

    def test_only_unlabeled_skips_images_with_accepted_boxes(self) -> None:
        BoxAnnotation.objects.create(asset=self.assets[0], label="knife", x=0.1, y=0.1, width=0.2, height=0.2)
        self.post_json(self.autolabel_url)
        job = self.run_next()
        self.assertEqual(job.total_assets, 1)
        self.assertFalse(
            BoxAnnotation.objects.filter(asset=self.assets[0], origin="model").exists()
        )
        self.assertTrue(BoxAnnotation.objects.filter(asset=self.assets[1], origin="model").exists())

    def test_rerun_replaces_old_suggestions_but_keeps_accepted_work(self) -> None:
        self.post_json(self.autolabel_url)
        self.run_next()
        kept = BoxAnnotation.objects.filter(asset=self.assets[0]).first()
        kept.status = "accepted"
        kept.save()
        manual = BoxAnnotation.objects.create(
            asset=self.assets[0], label="laptop", x=0.6, y=0.6, width=0.1, height=0.1
        )

        self.post_json(self.autolabel_url, {"asset_id": str(self.assets[0].id)})
        self.run_next()

        ids = set(BoxAnnotation.objects.filter(asset=self.assets[0]).values_list("id", flat=True))
        self.assertIn(kept.id, ids)
        self.assertIn(manual.id, ids)
        suggested = BoxAnnotation.objects.filter(asset=self.assets[0], status="suggested")
        self.assertEqual(suggested.count(), 2)  # replaced, not duplicated

    def test_labels_outside_the_project_are_dropped(self) -> None:
        FakeLabeler.boxes = [Proposal(0.1, 0.1, 0.2, 0.2, "bomb", 0.99)]
        self.post_json(self.autolabel_url)
        job = self.run_next()
        self.assertEqual(job.boxes_proposed, 0)

    def test_out_of_range_boxes_are_clamped_not_rejected_by_the_database(self) -> None:
        FakeLabeler.boxes = [Proposal(0.9, 0.9, 0.5, 0.5, "knife", 0.9)]
        self.post_json(self.autolabel_url, {"asset_id": str(self.assets[0].id)})
        self.run_next()
        box = BoxAnnotation.objects.get(asset=self.assets[0])
        self.assertLessEqual(float(box.x + box.width), 1.0)
        self.assertLessEqual(float(box.y + box.height), 1.0)

    def test_failure_is_recorded_on_the_job_and_unblocks_the_project(self) -> None:
        self.post_json(self.autolabel_url)
        job = self.run_next(ExplodingLabeler())
        self.assertEqual(job.status, "failed")
        self.assertIn("model weights missing", job.error)
        self.assertEqual(self.post_json(self.autolabel_url).status_code, 202)

    def test_project_without_labels_fails_with_a_plain_message(self) -> None:
        self.post_json(self.autolabel_url)
        Project.objects.filter(pk=self.project.pk).update(labels=[])
        job = self.run_next()
        self.assertEqual(job.status, "failed")
        self.assertIn("label", job.error)

    def test_cancel_request_stops_a_running_job_between_images(self) -> None:
        self.post_json(self.autolabel_url)

        class CancelAfterFirst(FakeLabeler):
            def propose(inner, image, labels, **kwargs):
                AutoLabelJob.objects.update(cancel_requested=True)
                return super().propose(image, labels, **kwargs)

        job = self.run_next(CancelAfterFirst())
        self.assertEqual(job.status, "cancelled")
        self.assertEqual(job.processed_assets, 1)

    def test_claim_next_job_is_single_use(self) -> None:
        self.post_json(self.autolabel_url)
        first = runner.claim_next_job()
        self.assertEqual(first.status, "running")
        self.assertIsNone(runner.claim_next_job())

    def test_stale_running_jobs_are_failed(self) -> None:
        self.post_json(self.autolabel_url)
        job = runner.claim_next_job()
        AutoLabelJob.objects.filter(pk=job.pk).update(started_at=timezone.now() - timedelta(hours=2))
        self.assertEqual(runner.fail_stale_jobs(), 1)
        self.assertEqual(AutoLabelJob.objects.get(pk=job.pk).status, "failed")

    def test_deleting_an_image_mid_job_does_not_crash_it(self) -> None:
        self.post_json(self.autolabel_url)

        class DeleteSecond(FakeLabeler):
            def propose(inner, image, labels, **kwargs):
                Asset.objects.filter(pk=self_assets[1].pk).delete()
                return super().propose(image, labels, **kwargs)

        self_assets = self.assets
        job = self.run_next(DeleteSecond())
        self.assertEqual(job.status, "succeeded")

    def test_unreadable_image_is_skipped_not_fatal(self) -> None:
        from django.core.files.storage import default_storage

        self.post_json(self.autolabel_url)
        default_storage.delete(self.assets[0].storage_key)
        job = self.run_next(FakeLabeler())
        self.assertEqual(job.status, "succeeded")
        self.assertEqual(job.processed_assets, 2)
        self.assertIn("Skipped 1 unreadable image", job.error)
        self.assertEqual(
            BoxAnnotation.objects.filter(asset=self.assets[1], status=BoxAnnotation.STATUS_SUGGESTED).count(), 2
        )
        self.assertFalse(BoxAnnotation.objects.filter(asset=self.assets[0]).exists())

    def test_worker_command_processes_queue_once(self) -> None:
        self.post_json(self.autolabel_url)
        out = StringIO()
        call_command("autolabel_worker", "--once", stdout=out)
        self.assertIn("succeeded", out.getvalue())
        self.assertEqual(AutoLabelJob.objects.get().status, "succeeded")
        self.assertTrue(runner.worker_online())


class ProposalReviewTests(AutoLabelBase):
    def setUp(self) -> None:
        super().setUp()
        self.post_json(self.autolabel_url)
        self.run_next()
        self.review_url = f"/api/projects/{self.project.id}/annotations/review"

    def annotations_url(self, asset: Asset) -> str:
        return f"/api/projects/{self.project.id}/assets/{asset.id}/annotations"

    def test_annotation_payload_exposes_provenance(self) -> None:
        item = self.client.get(self.annotations_url(self.assets[0])).json()["annotations"][0]
        self.assertEqual(item["origin"], "model")
        self.assertEqual(item["status"], "suggested")
        self.assertEqual(item["model_name"], "fake-labeler")
        self.assertIsInstance(item["confidence"], float)

    def test_asset_listing_counts_accepted_and_pending_boxes(self) -> None:
        BoxAnnotation.objects.create(asset=self.assets[0], label="knife", x=0.1, y=0.6, width=0.1, height=0.1)
        listed = {a["id"]: a for a in self.client.get(f"/api/projects/{self.project.id}/assets").json()["assets"]}
        first = listed[str(self.assets[0].id)]
        self.assertEqual((first["accepted_boxes"], first["pending_boxes"]), (1, 2))
        self.assertEqual(listed[str(self.assets[1].id)]["accepted_boxes"], 0)

    def test_project_listing_summarizes_images_and_review_state(self) -> None:
        BoxAnnotation.objects.create(asset=self.assets[0], label="knife", x=0.1, y=0.6, width=0.1, height=0.1)
        listed = self.client.get("/api/projects").json()["projects"][0]
        self.assertEqual(
            (listed["image_count"], listed["accepted_boxes"], listed["pending_boxes"]), (2, 1, 4)
        )
        fresh = self.post_json(
            "/api/projects", {"name": "New", "modality": "image", "task": "t", "labels": ["a"]}
        ).json()["project"]
        self.assertEqual((fresh["image_count"], fresh["accepted_boxes"], fresh["pending_boxes"]), (0, 0, 0))

    def test_project_listing_counts_pictures_with_boxes_waiting_for_review(self) -> None:
        # Two pictures, four suggested boxes between them: the box count is 4, the picture count is 2.
        listed = self.client.get("/api/projects").json()["projects"][0]
        self.assertEqual((listed["pending_boxes"], listed["pending_pictures"]), (4, 2))
        BoxAnnotation.objects.filter(asset=self.assets[0], status=BoxAnnotation.STATUS_SUGGESTED).delete()
        listed = self.client.get("/api/projects").json()["projects"][0]
        self.assertEqual(listed["pending_pictures"], 1)
        fresh = self.post_json(
            "/api/projects", {"name": "New", "modality": "image", "task": "t", "labels": ["a"]}
        ).json()["project"]
        self.assertEqual(fresh["pending_pictures"], 0)

    def test_accept_above_threshold_marks_reviewer(self) -> None:
        response = self.post_json(self.review_url, {"action": "accept", "min_confidence": 0.5})
        body = response.json()
        self.assertEqual((body["updated"], body["pending_boxes"]), (2, 2))  # the original two keys keep their meaning
        self.assertEqual(body["left_for_review"], {"total": 2, "without_score": 0, "from_operator_feedback": 0})
        accepted = BoxAnnotation.objects.filter(status="accepted")
        self.assertEqual(accepted.count(), 2)
        for box in accepted:
            self.assertEqual(box.reviewed_by, self.user)
            self.assertIsNotNone(box.reviewed_at)
            self.assertGreaterEqual(box.confidence, 0.5)

    def test_accept_can_be_limited_to_one_image(self) -> None:
        response = self.post_json(
            self.review_url, {"action": "accept", "asset_id": str(self.assets[0].id)}
        )
        self.assertEqual(response.json()["updated"], 2)
        self.assertFalse(
            BoxAnnotation.objects.filter(asset=self.assets[1], status="accepted").exists()
        )

    def test_reject_deletes_only_proposals(self) -> None:
        manual = BoxAnnotation.objects.create(
            asset=self.assets[0], label="knife", x=0.1, y=0.6, width=0.1, height=0.1
        )
        self.post_json(self.review_url, {"action": "reject"})
        self.assertEqual(list(BoxAnnotation.objects.all()), [manual])

    def test_review_validates_input(self) -> None:
        for payload in (
            {"action": "approve"},
            {"action": "accept", "min_confidence": 2},
            {"action": "accept", "ids": "all"},
            {"action": "accept", "ids": ["nope"]},
            {"action": "accept", "extra": 1},
        ):
            with self.subTest(payload=payload):
                self.assertEqual(self.post_json(self.review_url, payload).status_code, 400)
        self.assertEqual(BoxAnnotation.objects.filter(status="suggested").count(), 4)

    def test_review_never_touches_other_projects(self) -> None:
        other = Project.objects.create(name="Other", modality="image", task="t", labels=["knife"])
        response = self.post_json(f"/api/projects/{other.id}/annotations/review", {"action": "reject"})
        self.assertEqual(response.json()["updated"], 0)
        self.assertEqual(BoxAnnotation.objects.count(), 4)

    def test_editing_a_proposal_counts_as_reviewing_it(self) -> None:
        box = BoxAnnotation.objects.filter(asset=self.assets[0], label="knife").get()
        url = f"{self.annotations_url(self.assets[0])}/{box.id}"
        response = self.client.patch(
            url,
            data=json.dumps({"label": "laptop", "x": 0.2, "y": 0.2, "width": 0.3, "height": 0.3}),
            content_type="application/json",
        )
        self.assertEqual(response.json()["annotation"]["status"], "accepted")
        box.refresh_from_db()
        self.assertEqual((box.reviewed_by, box.origin), (self.user, "model"))

    def test_patch_can_restore_a_proposal_for_undo(self) -> None:
        box = BoxAnnotation.objects.filter(asset=self.assets[0]).first()
        url = f"{self.annotations_url(self.assets[0])}/{box.id}"
        payload = {
            "label": box.label, "x": float(box.x), "y": float(box.y),
            "width": float(box.width), "height": float(box.height),
        }
        self.client.patch(url, data=json.dumps({**payload, "status": "accepted"}), content_type="application/json")
        restored = self.client.patch(
            url, data=json.dumps({**payload, "status": "suggested"}), content_type="application/json"
        )
        self.assertEqual(restored.json()["annotation"]["status"], "suggested")
        box.refresh_from_db()
        self.assertIsNone(box.reviewed_by)

    def test_clients_cannot_forge_model_provenance_or_manual_proposals(self) -> None:
        payload = {"label": "knife", "x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2}
        for extra in ({"status": "suggested"}, {"origin": "model"}, {"confidence": 0.99}):
            response = self.client.post(
                self.annotations_url(self.assets[1]),
                data=json.dumps({**payload, **extra}),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 400, extra)
        manual = BoxAnnotation.objects.create(asset=self.assets[1], label="knife", x=0.5, y=0.5, width=0.1, height=0.1)
        patched = self.client.patch(
            f"{self.annotations_url(self.assets[1])}/{manual.id}",
            data=json.dumps({**payload, "status": "suggested"}),
            content_type="application/json",
        )
        self.assertEqual(patched.status_code, 400)

    def test_database_rejects_manual_boxes_marked_as_suggestions(self) -> None:
        from django.db import IntegrityError, transaction

        with self.assertRaises(IntegrityError), transaction.atomic():
            BoxAnnotation.objects.create(
                asset=self.assets[0], label="knife", x=0.1, y=0.1, width=0.1, height=0.1,
                origin="manual", status="suggested",
            )


class PureFunctionTests(TestCase):
    def test_box_iou_and_nms_keep_the_higher_scoring_overlap(self) -> None:
        a, b, c = (0, 0, 10, 10), (1, 1, 10, 10), (20, 20, 30, 30)
        self.assertAlmostEqual(box_iou(a, a), 1.0)
        self.assertEqual(box_iou(a, c), 0.0)
        self.assertEqual(sorted(non_max_suppression([a, b, c], [0.5, 0.9, 0.4], 0.6)), [1, 2])

    def test_part_boxes_inside_a_bigger_box_of_the_same_label_are_dropped(self) -> None:
        whole, part, other_label, elsewhere = (0, 0, 100, 100), (10, 10, 40, 40), (10, 10, 40, 40), (200, 0, 240, 40)
        kept = suppress_contained(
            [whole, part, other_label, elsewhere], [0, 0, 1, 0], [0.9, 0.95, 0.8, 0.7]
        )
        self.assertEqual(sorted(kept), [0, 2, 3])

    def test_mask_bounds(self) -> None:
        mask = np.zeros((10, 12), dtype=bool)
        mask[2:5, 3:9] = True
        self.assertEqual(mask_bounds(mask), (3, 2, 9, 5))
        self.assertIsNone(mask_bounds(np.zeros((4, 4), dtype=bool)))

    def test_normalized_box_clamps_into_the_image(self) -> None:
        box = normalized_box(Proposal(0.95, -0.2, 0.5, 2.0, "x", 0.5))
        self.assertEqual(float(box["x"]), 0.95)
        self.assertEqual(float(box["y"]), 0.0)
        self.assertLessEqual(float(box["x"] + box["width"]), 1.0)
        self.assertLessEqual(float(box["y"] + box["height"]), 1.0)
        self.assertIsNone(normalized_box(Proposal(1.0, 0.1, 0.2, 0.2, "x", 0.5)))


