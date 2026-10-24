"""Auto-suggest must never touch a picture that came from operator feedback.

Those pictures carry the operator's confirmed / missed boxes as Needs-review suggestions. A labeler has not
looked at them yet, so a suggestion run that "refreshes" model suggestions would silently replace them.
"""

from __future__ import annotations

from unittest import mock

from django.test import override_settings

from . import feedback_import
from .autolabel import runner
from .autolabel.base import Proposal
from .models import Asset, AutoLabelJob, BoxAnnotation, FeedbackImport
from .tests_autolabel import FakeLabeler
from .tests_feedback_import import FeedbackBase

REASON = "kept for review: this picture came from operator feedback"
BOX_FIELDS = (
    "id", "asset_id", "label", "x", "y", "width", "height", "origin", "status", "confidence", "model_name",
    "job_id", "created_by_id", "reviewed_by_id", "reviewed_at", "created_at",
)


class FeedbackAutoLabelBase(FeedbackBase):
    def setUp(self) -> None:
        super().setUp()
        settings = override_settings(
            AUTOLABEL={
                "BACKEND": "projects.tests_autolabel:FakeLabeler",
                "SAM_MODEL": "sam",
                "CLASSIFIER_MODEL": "clip",
                "DEVICE": "cpu",
                "POINTS_PER_SIDE": 4,
                "JOB_TIMEOUT_SECONDS": 60,
                "ALLOW_DOWNLOAD": False,
            }
        )
        settings.enable()
        self.addCleanup(settings.disable)
        FakeLabeler.calls = 0
        FakeLabeler.boxes = [
            Proposal(0.1, 0.1, 0.3, 0.3, "damaged", 0.9),
            Proposal(0.5, 0.5, 0.2, 0.2, "normal", 0.8),
        ]
        # Two feedback pictures (one with a confirmed box, one with a drawn missed box) and two normal ones.
        first, second = self.observe(), self.observe()
        self.tp(first, 0)
        self.missed(second, "normal")
        self.assertEqual(self.do_import().status_code, 200)
        self.feedback_assets = [self.imported_asset(first), self.imported_asset(second)]
        self.normal_assets = [self.make_asset(), self.make_asset()]
        self.assertEqual(FeedbackImport.objects.count(), 2)

    @property
    def autolabel_url(self) -> str:
        return self.api("/autolabel")

    def snapshot(self, assets) -> list[tuple]:
        rows = BoxAnnotation.objects.filter(asset__in=assets).order_by("id").values_list(*BOX_FIELDS)
        return [tuple(row) for row in rows]

    def start(self, **payload):
        response = self.post(self.autolabel_url, payload)
        self.assertEqual(response.status_code, 202, response.content)
        return response.json()

    def run_next(self, labeler=None) -> AutoLabelJob:
        job = runner.claim_next_job()
        self.assertIsNotNone(job)
        return runner.run_job(job, labeler or FakeLabeler())

    def suggestions_by(self, asset, model_name="fake-labeler"):
        return BoxAnnotation.objects.filter(asset=asset, model_name=model_name)


class FeedbackPicturesAreLeftAloneTests(FeedbackAutoLabelBase):
    def test_all_pictures_job_leaves_feedback_boxes_byte_identical_and_processes_the_rest(self) -> None:
        before = self.snapshot(self.feedback_assets)
        self.assertEqual(len(before), 2)
        self.assertTrue(all(row[BOX_FIELDS.index("model_name")].startswith("operator feedback") for row in before))

        self.start(only_unlabeled=False)
        job = self.run_next()

        self.assertEqual(job.status, "succeeded")
        self.assertEqual(self.snapshot(self.feedback_assets), before)
        for asset in self.normal_assets:
            self.assertEqual(self.suggestions_by(asset).count(), 2)
        everything = Asset.objects.filter(project=self.project).count()
        self.assertEqual(job.total_assets, everything - 2)
        self.assertEqual(job.processed_assets, everything - 2)
        self.assertFalse(Asset.objects.filter(pk__in=[a.pk for a in self.feedback_assets], annotations__job=job).exists())
        self.assertIn(REASON, job.error)
        self.assertIn("2 pictures left out", job.error)

    def test_only_unlabeled_job_still_skips_feedback_pictures_that_have_no_accepted_box(self) -> None:
        for asset in self.feedback_assets:  # Needs-review only: exactly what 'only unlabeled' would have picked
            self.assertFalse(asset.annotations.filter(status="accepted").exists())
        before = self.snapshot(self.feedback_assets)

        self.start(only_unlabeled=True)
        job = self.run_next()

        self.assertEqual(self.snapshot(self.feedback_assets), before)
        for asset in self.normal_assets:
            self.assertEqual(self.suggestions_by(asset).count(), 2)
        self.assertEqual(job.total_assets, len(self.normal_assets))
        self.assertIn(REASON, job.error)

    def test_a_feedback_picture_a_labeler_already_accepted_boxes_on_is_still_left_alone(self) -> None:
        box = self.feedback_assets[0].annotations.get()
        BoxAnnotation.objects.filter(pk=box.pk).update(status="accepted")
        BoxAnnotation.objects.create(asset=self.feedback_assets[1], label="normal", x=0.2, y=0.2, width=0.1, height=0.1)
        before = self.snapshot(self.feedback_assets)
        self.start(only_unlabeled=False)
        self.run_next()
        self.assertEqual(self.snapshot(self.feedback_assets), before)

    def test_explicit_picture_that_came_from_feedback_is_skipped_with_the_reason(self) -> None:
        asset = self.feedback_assets[0]
        before = self.snapshot(self.feedback_assets)

        body = self.start(asset_id=str(asset.id))
        self.assertEqual(body["job"]["kept_for_review"], 1)
        self.assertEqual(body["job"]["kept_for_review_reason"], REASON)
        job = self.run_next()

        self.assertEqual(job.status, "succeeded")
        self.assertEqual((job.total_assets, job.processed_assets, job.boxes_proposed), (0, 0, 0))
        self.assertEqual(FakeLabeler.calls, 0)
        self.assertIn(REASON, job.error)
        self.assertIn("1 picture left out", job.error)
        self.assertEqual(self.snapshot(self.feedback_assets), before)

    def test_explicit_normal_picture_is_still_processed(self) -> None:
        body = self.start(asset_id=str(self.normal_assets[0].id))
        self.assertEqual(body["job"]["kept_for_review"], 0)
        job = self.run_next()
        self.assertEqual((job.total_assets, job.boxes_proposed), (1, 2))
        self.assertNotIn(REASON, job.error)

    def test_a_project_without_feedback_pictures_has_no_reason_and_no_count(self) -> None:
        other = self.make_project_without_feedback()
        body = self.post(self.api("/autolabel", other), {"only_unlabeled": False})
        self.assertEqual(body.status_code, 202, body.content)
        job = body.json()["job"]
        self.assertEqual((job["kept_for_review"], job["kept_for_review_reason"]), (0, ""))

    def make_project_without_feedback(self):
        from .models import Project

        other = Project.objects.create(name="Plain", modality="image", task="Detect", labels=["damaged"])
        self.make_asset(project=other)
        return other

    def test_api_job_keeps_existing_keys_and_adds_the_count(self) -> None:
        body = self.start(only_unlabeled=False)
        job = body["job"]
        for key in (
            "id", "status", "scope", "asset_id", "only_unlabeled", "min_confidence", "max_boxes", "model_name",
            "total_assets", "processed_assets", "boxes_proposed", "cancel_requested", "error", "created_at",
            "started_at", "finished_at",
        ):
            self.assertIn(key, job)
        self.assertEqual(job["kept_for_review"], 2)
        self.assertEqual(job["kept_for_review_reason"], REASON)
        self.run_next()
        listed = self.client.get(self.autolabel_url).json()["jobs"][0]
        self.assertEqual(listed["kept_for_review"], 2)
        self.assertIn(REASON, listed["error"])

    def test_unreadable_pictures_and_kept_pictures_are_both_reported(self) -> None:
        self.normal_assets[0].storage_key = "projects/missing/nothing.png"
        self.normal_assets[0].save()
        self.start(only_unlabeled=False)
        job = self.run_next()
        self.assertIn(REASON, job.error)
        self.assertIn("unreadable", job.error)

    def test_the_marker_matches_the_one_the_importer_writes(self) -> None:
        self.assertEqual(runner.FEEDBACK_BOX_SOURCE, feedback_import.BOX_SOURCE_NAME)
        self.assertEqual(runner.KEPT_REASON, REASON)


class DefenceInDepthTests(FeedbackAutoLabelBase):
    def test_a_picture_linked_after_the_job_started_is_skipped_inside_the_loop(self) -> None:
        before = self.snapshot(self.feedback_assets)
        self.start(only_unlabeled=False)
        everything = list(Asset.objects.filter(project=self.project).order_by("created_at", "id"))
        with mock.patch.object(runner, "_target_assets", return_value=everything):  # selection missed them
            job = self.run_next()
        self.assertEqual(self.snapshot(self.feedback_assets), before)
        self.assertIn(REASON, job.error)
        self.assertIn("2 pictures left out", job.error)
        self.assertEqual(job.processed_assets, len(everything))

    def test_the_delete_itself_spares_operator_feedback_boxes_even_if_every_other_check_fails(self) -> None:
        before = self.snapshot(self.feedback_assets)
        self.start(only_unlabeled=False)
        everything = list(Asset.objects.filter(project=self.project).order_by("created_at", "id"))
        with mock.patch.object(runner, "_target_assets", return_value=everything), mock.patch.object(
            runner, "_linked_to_feedback", return_value=False
        ):
            self.run_next()
        kept = [row for row in self.snapshot(self.feedback_assets) if row[0] in {r[0] for r in before}]
        self.assertEqual(kept, before)  # nothing the operator confirmed or drew was deleted

    def test_boxes_listed_in_a_link_are_spared_even_if_they_were_renamed(self) -> None:
        asset = self.feedback_assets[0]
        box = asset.annotations.get()
        BoxAnnotation.objects.filter(pk=box.pk).update(model_name="fake-labeler")  # looks like an ordinary suggestion
        before = self.snapshot([asset])
        self.start(only_unlabeled=False)
        everything = list(Asset.objects.filter(project=self.project).order_by("created_at", "id"))
        with mock.patch.object(runner, "_target_assets", return_value=everything), mock.patch.object(
            runner, "_linked_to_feedback", return_value=False
        ):
            self.run_next()
        self.assertEqual([row for row in self.snapshot([asset]) if row[0] == box.id], before)

    def test_ordinary_old_suggestions_are_still_replaced(self) -> None:
        asset = self.normal_assets[0]
        old = BoxAnnotation.objects.create(
            asset=asset, label="damaged", x=0.9, y=0.9, width=0.05, height=0.05,
            origin="model", status="suggested", model_name="fake-labeler", confidence=0.5,
        )
        self.start(only_unlabeled=False)
        self.run_next()
        self.assertFalse(BoxAnnotation.objects.filter(pk=old.pk).exists())
        self.assertEqual(self.suggestions_by(asset).count(), 2)


class BulkReviewExplainsWhatItLeftTests(FeedbackAutoLabelBase):
    """The review endpoint says what it did not touch and what a reject would remove, without changing what it does."""

    def setUp(self) -> None:
        super().setUp()
        # Two ordinary pictures get four model suggestions (0.9 and 0.8 each); the feedback pictures keep a confirmed
        # box (the model's own, score 0.9) and an operator-drawn box (no score).
        self.start(only_unlabeled=False)
        self.run_next()
        mine = [*self.feedback_assets, *self.normal_assets]
        BoxAnnotation.objects.filter(asset__project=self.project).exclude(asset__in=mine).delete()  # other fixture pictures
        self.review_url = f"/api/projects/{self.project.id}/annotations/review"

    def review(self, **payload):
        response = self.post(self.review_url, payload)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def suggested(self) -> int:
        return BoxAnnotation.objects.filter(asset__project=self.project, status="suggested").count()

    def test_setup_has_six_suggestions_two_from_the_operator(self) -> None:
        self.assertEqual(self.suggested(), 6)
        feedback = BoxAnnotation.objects.filter(model_name__startswith="operator feedback")
        self.assertEqual((feedback.count(), feedback.filter(confidence__isnull=True).count()), (2, 1))

    def test_accept_with_a_threshold_says_which_boxes_it_left_and_why(self) -> None:
        body = self.review(action="accept", min_confidence=0.85)
        self.assertEqual((body["updated"], body["pending_boxes"]), (3, 3))  # unchanged behaviour: the unscored box is not accepted
        self.assertEqual(body["left_for_review"], {"total": 3, "without_score": 1, "from_operator_feedback": 1})
        self.assertEqual(
            BoxAnnotation.objects.get(asset__in=self.feedback_assets, confidence__isnull=True).status, "suggested"
        )

    def test_accept_any_suggestion_leaves_nothing_behind(self) -> None:
        body = self.review(action="accept")
        self.assertEqual((body["updated"], body["pending_boxes"]), (6, 0))
        self.assertEqual(body["left_for_review"], {"total": 0, "without_score": 0, "from_operator_feedback": 0})

    def test_left_counts_stay_inside_the_picture_scope(self) -> None:
        body = self.review(action="accept", min_confidence=0.85, asset_id=str(self.feedback_assets[1].id))
        self.assertEqual(body["updated"], 0)
        self.assertEqual(body["left_for_review"], {"total": 1, "without_score": 1, "from_operator_feedback": 1})
        body = self.review(action="accept", min_confidence=0.85, asset_id=str(self.normal_assets[0].id))
        self.assertEqual(body["updated"], 1)
        self.assertEqual(body["left_for_review"], {"total": 1, "without_score": 0, "from_operator_feedback": 0})

    def test_left_counts_follow_the_named_ids(self) -> None:
        drawn = BoxAnnotation.objects.get(asset__in=self.feedback_assets, confidence__isnull=True)
        scored = BoxAnnotation.objects.filter(asset__in=self.normal_assets, confidence__gte=0.85).first()
        body = self.review(action="accept", min_confidence=0.5, ids=[str(drawn.pk), str(scored.pk)])
        self.assertEqual(body["updated"], 1)
        self.assertEqual(body["left_for_review"], {"total": 1, "without_score": 1, "from_operator_feedback": 1})

    def test_reject_with_a_threshold_also_reports_what_stayed(self) -> None:
        body = self.review(action="reject", min_confidence=0.85)
        self.assertEqual(body["updated"], 3)
        self.assertEqual(body["from_operator_feedback"], 1)  # the confirmed box (0.9) went; the unscored drawn one stayed
        self.assertEqual(body["left_for_review"], {"total": 3, "without_score": 1, "from_operator_feedback": 1})

    def test_preview_of_reject_all_counts_operator_boxes_and_changes_nothing(self) -> None:
        before = self.snapshot(self.feedback_assets + self.normal_assets)
        body = self.review(action="reject", preview=True)
        self.assertTrue(body["preview"])
        self.assertEqual(body["would_update"], 6)
        self.assertEqual(body["from_operator_feedback"], 2)
        self.assertEqual((body["updated"], body["pending_boxes"]), (0, 6))
        self.assertEqual(body["left_for_review"], {"total": 0, "without_score": 0, "from_operator_feedback": 0})
        self.assertEqual(self.snapshot(self.feedback_assets + self.normal_assets), before)

    def test_preview_of_accept_changes_nothing_and_matches_the_real_run(self) -> None:
        before = self.snapshot(self.feedback_assets + self.normal_assets)
        preview = self.review(action="accept", min_confidence=0.85, preview=True)
        self.assertEqual(self.snapshot(self.feedback_assets + self.normal_assets), before)
        self.assertEqual((preview["updated"], preview["would_update"]), (0, 3))
        real = self.review(action="accept", min_confidence=0.85)
        self.assertEqual(real["updated"], preview["would_update"])
        self.assertEqual(real["left_for_review"], preview["left_for_review"])
        self.assertEqual(real["from_operator_feedback"], preview["from_operator_feedback"])

    def test_a_normal_run_reports_preview_false_and_would_update_equal_to_updated(self) -> None:
        body = self.review(action="accept", min_confidence=0.85)
        self.assertFalse(body["preview"])
        self.assertEqual(body["would_update"], body["updated"])

    def test_preview_must_be_true_or_false(self) -> None:
        for value in ("yes", 1, []):
            with self.subTest(value=value):
                self.assertEqual(self.post(self.review_url, {"action": "reject", "preview": value}).status_code, 400)
        self.assertEqual(self.suggested(), 6)

    def test_the_picture_links_stay_one_per_observation_after_a_reject(self) -> None:
        self.review(action="reject")
        self.assertEqual(FeedbackImport.objects.count(), 2)
        self.assertEqual(self.do_import().json()["totals"]["imported"], 0)  # the pictures are not pulled in a second time

    def test_viewers_cannot_preview_or_change(self) -> None:
        from .tests_roles import client_for, make_user

        viewer = client_for(make_user("v", "viewer"))
        self.assertEqual(self.post(self.review_url, {"action": "reject", "preview": True}, client=viewer).status_code, 403)
        self.assertEqual(self.suggested(), 6)
