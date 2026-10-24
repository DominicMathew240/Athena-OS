"""Turning operator feedback into reviewable pictures: Needs-review boxes, never ground truth, with provenance."""

from __future__ import annotations

import datetime
import errno
import hashlib
import io
import json
import uuid
from decimal import Decimal
from unittest import mock

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from PIL import Image

from . import feedback_import
from .datasets import service as dataset_service, splits
from .inference import service as inference_service
from .models import (
    Asset,
    BoxAnnotation,
    DatasetVersion,
    FeedbackImport,
    FeedbackRecord,
    InferenceApp,
    ModelVersion,
    ObservationRecord,
    Project,
    ReviewDecision,
    TrainingRun,
)
from .tests_inference import InferenceBase, image_upload
from .tests_roles import client_for, make_user
from .tests_training import FakeBackend
from .training.base import BoxPrediction

TWO_BOXES = [BoxPrediction("damaged", 0.1, 0.2, 0.3, 0.4, 0.9), BoxPrediction("normal", 0.5, 0.5, 0.4, 0.4, 0.8)]
SPLIT = splits.SplitConfig.default()
MISSED_BOX = {"x": 0.6, "y": 0.1, "width": 0.2, "height": 0.3}


def row_digest(model) -> str:
    rows = list(model.objects.order_by("pk").values())
    return hashlib.sha256(json.dumps(rows, default=str, sort_keys=True).encode()).hexdigest()


class FeedbackBase(InferenceBase):
    def setUp(self) -> None:
        super().setUp()
        FakeBackend.boxes = list(TWO_BOXES)
        self.app = self.make_app(name="Feedback line")
        self.colour = 10

    # -- fixtures -------------------------------------------------------------------------
    def observe(self, *, process: bool = True) -> str:
        """A finished check-screen run on a picture of its own colour (so every picture has its own hash)."""
        self.colour += 17
        response = self.run_image(self.app["id"], image_upload(color=(self.colour, 40, 90)), process=process)
        self.assertEqual(response.status_code, 202, response.content)
        return response.json()["observation_id"]

    def give(self, observation_id: str, **payload):
        url = self.observation_url(self.app["id"], observation_id, "/feedback")
        response = self.post(url, payload)
        self.assertEqual(response.status_code, 201, response.content)
        return response

    def tp(self, observation_id: str, index: int = 0):
        return self.give(observation_id, prediction_index=index, verdict="true_positive")

    def fp(self, observation_id: str, index: int = 0):
        return self.give(observation_id, prediction_index=index, verdict="false_positive", note="A shadow.")

    def missed(self, observation_id: str, label: str = "normal", box=MISSED_BOX):
        payload = {"prediction_index": "missed", "label": label, "note": "The model missed it."}
        if box is not None:
            payload["box"] = box
        return self.give(observation_id, **payload)

    def decide(self, observation_id: str, decision: str, note: str = "") -> None:
        """A whole-result decision exactly as the check screen stores it (oldest to newest by time, then id)."""
        payload = {"decision": decision}
        if note:
            payload["note"] = note
        response = self.post(self.observation_url(self.app["id"], observation_id, "/decision"), payload)
        self.assertEqual(response.status_code, 201, response.content)

    # -- urls -----------------------------------------------------------------------------
    def list_url(self, project=None) -> str:
        return self.api("/feedback/corrections", project)

    def import_url(self, project=None) -> str:
        return self.api("/feedback/corrections/import", project)

    def do_import(self, payload=None, project=None, client=None):
        return self.post(self.import_url(project), payload, client=client)

    def listing(self, project=None) -> dict:
        response = self.client.get(self.list_url(project))
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def stored_files(self) -> int:
        return len(default_storage.listdir(f"projects/{self.project.id}/assets")[1])

    def imported_asset(self, observation_id: str) -> Asset:
        return FeedbackImport.objects.get(observation_id=observation_id).asset


class ImportHappyPathTests(FeedbackBase):
    def test_correct_detection_and_missed_box_become_needs_review_boxes(self) -> None:
        oid = self.observe()
        self.tp(oid, 0)
        self.fp(oid, 1)
        self.missed(oid, "normal", MISSED_BOX)
        self.missed(oid, "damaged", None)
        observation = ObservationRecord.objects.get(pk=oid)

        response = self.do_import()
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["skipped"], [])
        self.assertEqual(len(body["imported"]), 1)
        item = body["imported"][0]
        self.assertEqual((item["observation_id"], item["boxes_created"], item["missed_without_box"]), (oid, 2, 1))
        self.assertEqual(body["totals"]["boxes_created"], 2)
        self.assertEqual(body["totals"]["missed_without_box"], 1)

        asset = Asset.objects.get(pk=item["asset_id"])
        self.assertEqual(asset.project_id, self.project.id)
        self.assertEqual((asset.sha256, asset.width, asset.height), (observation.image_sha256, 40, 30))
        self.assertEqual(asset.content_type, "image/png")
        with default_storage.open(asset.storage_key, "rb") as handle, default_storage.open(observation.image_key, "rb") as original:
            self.assertEqual(handle.read(), original.read())
        self.assertNotEqual(asset.storage_key, observation.image_key)  # a copy, not a reference

        boxes = {box.label: box for box in asset.annotations.all()}
        self.assertEqual(sorted(boxes), ["damaged", "normal"])  # the wrong detection (index 1, "normal") made no box of its own
        correct, drawn = boxes["damaged"], boxes["normal"]
        for box in (correct, drawn):
            self.assertEqual((box.status, box.origin), ("suggested", "model"))
            self.assertIn("operator feedback", box.model_name)
        self.assertEqual((float(correct.x), float(correct.y), float(correct.width), float(correct.height)), (0.1, 0.2, 0.3, 0.4))
        self.assertEqual(correct.confidence, 0.9)
        self.assertEqual((float(drawn.x), float(drawn.y), float(drawn.width), float(drawn.height)), (0.6, 0.1, 0.2, 0.3))
        self.assertIsNone(drawn.confidence)
        self.assertEqual(correct.created_by_id, self.user.id)  # the operator whose verdict it is

        link = FeedbackImport.objects.get(observation=observation)
        self.assertEqual((link.asset_id, link.asset_ref), (asset.id, str(asset.id)))
        self.assertEqual(link.observation_ref, oid)
        self.assertEqual(link.project_id, self.project.id)
        self.assertEqual((link.model_version_ref, link.model_version_number), (str(self.model.id), self.model.version))
        self.assertEqual((link.artifact_sha256, link.image_sha256), (self.model.artifact_sha256, observation.image_sha256))
        self.assertEqual(link.kinds, {"true_positive": 1, "false_positive": 1, "missed": 2})
        self.assertEqual((link.boxes_created, link.missed_without_box, link.skipped), (2, 1, []))
        self.assertEqual(sorted(link.annotation_ids), sorted(str(box.id) for box in boxes.values()))
        self.assertEqual((link.imported_by_id, link.imported_by_ref, link.imported_by_role), (self.user.id, f"u{self.user.id}", "approver"))

    def test_the_existing_pictures_list_shows_the_origin_and_needs_review_count(self) -> None:
        oid = self.observe()
        self.tp(oid, 0)
        before = self.client.get(self.api("/assets")).json()["assets"]
        self.assertTrue(all(a["origin"] == "upload" and a["from_observation"] is None for a in before))
        self.do_import()
        listing = self.client.get(self.api("/assets")).json()["assets"]
        imported = [a for a in listing if a["origin"] == "operator_feedback"]
        self.assertEqual(len(imported), 1)
        self.assertEqual(imported[0]["from_observation"], oid)
        self.assertEqual((imported[0]["pending_boxes"], imported[0]["accepted_boxes"]), (1, 0))
        for key in ("id", "filename", "content_type", "sha256", "size_bytes", "width", "height", "created_at", "content_url"):
            self.assertIn(key, imported[0])  # additive: nothing the page uses went away
        self.assertEqual(self.client.get(imported[0]["content_url"]).status_code, 200)
        boxes = self.client.get(self.api(f"/assets/{imported[0]['id']}/annotations")).json()["annotations"]
        self.assertEqual([(b["status"], b["origin"]) for b in boxes], [("suggested", "model")])

    def test_false_alarm_only_picture_is_imported_without_boxes(self) -> None:
        oid = self.observe()
        self.fp(oid, 0)
        self.fp(oid, 1)
        item = self.do_import().json()["imported"][0]
        self.assertEqual((item["boxes_created"], item["missed_without_box"], item["false_alarms"]), (0, 0, 2))
        self.assertEqual(Asset.objects.get(pk=item["asset_id"]).annotations.count(), 0)
        link = FeedbackImport.objects.get(observation_id=oid)
        self.assertEqual(link.kinds, {"true_positive": 0, "false_positive": 2, "missed": 0})

    def test_missed_object_without_a_box_creates_nothing_but_is_counted(self) -> None:
        oid = self.observe()
        self.missed(oid, "damaged", None)
        item = self.do_import().json()["imported"][0]
        self.assertEqual((item["boxes_created"], item["missed_without_box"]), (0, 1))
        self.assertEqual(BoxAnnotation.objects.filter(asset_id=item["asset_id"]).count(), 0)

    def test_observation_without_feedback_is_never_listed_or_imported(self) -> None:
        oid = self.observe()
        self.assertEqual(self.listing()["total"], 0)
        body = self.do_import({"observation_ids": [oid]}).json()
        self.assertEqual(body["imported"], [])
        self.assertEqual([(s["observation_id"], s["reason"]) for s in body["skipped"]], [(oid, "no_feedback")])


class SelectionRuleTests(FeedbackBase):
    def test_latest_verdict_per_detection_wins(self) -> None:
        oid = self.observe()
        self.tp(oid, 0)
        self.fp(oid, 0)  # changed their mind: now wrong
        self.fp(oid, 1)
        self.tp(oid, 1)  # and the other way round
        item = self.do_import().json()["imported"][0]
        boxes = list(Asset.objects.get(pk=item["asset_id"]).annotations.all())
        self.assertEqual([(b.label, float(b.x)) for b in boxes], [("normal", 0.5)])
        link = FeedbackImport.objects.get(observation_id=oid)
        self.assertEqual(link.kinds, {"true_positive": 1, "false_positive": 1, "missed": 0})

    def test_the_rule_is_the_one_the_export_uses(self) -> None:
        oid = self.observe()
        self.tp(oid, 0)
        self.fp(oid, 0)
        self.tp(oid, 1)
        self.missed(oid, "damaged", MISSED_BOX)
        rows, _ = inference_service.export_rows(InferenceApp.objects.get(pk=self.app["id"]))
        effective = {r["feedback_id"] for r in rows}
        records = list(FeedbackRecord.objects.filter(observation_id=oid).order_by("created_at", "id"))
        chosen = {str(r.id) for r in feedback_import.effective_feedback(records)}
        self.assertEqual(chosen, effective)

    def test_label_not_in_project_is_reported_and_the_box_skipped(self) -> None:
        oid = self.observe()
        self.tp(oid, 1)  # "normal"
        self.missed(oid, "normal", MISSED_BOX)
        self.tp(oid, 0)  # "damaged", still fine
        Project.objects.filter(pk=self.project.pk).update(labels=["damaged", "Normal"])  # case-sensitive: "normal" is gone
        body = self.do_import().json()
        item = body["imported"][0]
        self.assertEqual(item["boxes_created"], 1)
        reasons = [(s["reason"], s["label"]) for s in item["skipped_boxes"]]
        self.assertEqual(reasons, [("label_not_in_project", "normal"), ("label_not_in_project", "normal")])
        self.assertEqual([b.label for b in Asset.objects.get(pk=item["asset_id"]).annotations.all()], ["damaged"])
        self.assertFalse(BoxAnnotation.objects.filter(label__in=["normal", "Normal"]).exists())  # never invented
        self.assertEqual(len(FeedbackImport.objects.get(observation_id=oid).skipped), 2)

    def test_invalid_stored_geometry_is_skipped_with_a_reason(self) -> None:
        oid = self.observe()
        self.tp(oid, 0)
        observation = ObservationRecord.objects.get(pk=oid)
        broken = [dict(p) for p in observation.predictions]
        broken[0]["box"] = {"x": 0.9, "y": 0.2, "width": 0.5, "height": 0.4}  # runs out of the picture
        ObservationRecord.objects.filter(pk=oid).update(predictions=broken)
        item = self.do_import().json()["imported"][0]
        self.assertEqual(item["boxes_created"], 0)
        self.assertEqual([s["reason"] for s in item["skipped_boxes"]], ["box_outside_image"])


class PictureProblemTests(FeedbackBase):
    def key_of(self, oid: str) -> str:
        return ObservationRecord.objects.get(pk=oid).image_key

    def test_a_file_that_is_not_an_image_is_named_and_left_out(self) -> None:
        bad, good = self.observe(), self.observe()
        for oid in (bad, good):
            self.tp(oid, 0)
        default_storage.delete(self.key_of(bad))
        default_storage.save(self.key_of(bad), ContentFile(b"%PDF-1.4 definitely not a picture"))
        # the stored hash no longer matches either, but "not an image" is the more useful thing to say
        files_before = self.stored_files()
        body = self.do_import().json()
        self.assertEqual([i["observation_id"] for i in body["imported"]], [good])
        self.assertEqual([(s["observation_id"], s["reason"]) for s in body["skipped"]], [(bad, "picture_not_a_supported_image")])
        self.assertTrue(body["skipped"][0]["message"])
        self.assertEqual(self.stored_files(), files_before + 1)
        self.assertFalse(FeedbackImport.objects.filter(observation_id=bad).exists())

    def test_a_missing_file_is_refused_in_the_list_and_the_import(self) -> None:
        oid = self.observe()
        self.tp(oid, 0)
        default_storage.delete(self.key_of(oid))
        item = self.listing()["items"][0]
        self.assertEqual((item["state"], item["reason"]), ("cannot_import", "picture_file_missing"))
        self.assertEqual(self.listing()["cannot_import"], 1)
        body = self.do_import({"observation_ids": [oid]}).json()
        self.assertEqual([(s["observation_id"], s["reason"]) for s in body["skipped"]], [(oid, "picture_file_missing")])
        self.assertEqual(Asset.objects.filter(annotations__status="suggested").count(), 0)

    def test_an_oversized_file_is_refused(self) -> None:
        oid = self.observe()
        self.tp(oid, 0)
        with mock.patch("projects.views.MAX_IMAGE_BYTES", 10):
            self.assertEqual(self.listing()["items"][0]["reason"], "picture_too_large")
            body = self.do_import().json()
        self.assertEqual([(s["observation_id"], s["reason"]) for s in body["skipped"]], [(oid, "picture_too_large")])
        self.assertFalse(FeedbackImport.objects.exists())

    def test_a_file_changed_since_the_check_is_refused(self) -> None:
        oid = self.observe()
        self.tp(oid, 0)
        key = self.key_of(oid)
        default_storage.delete(key)
        default_storage.save(key, image_upload(color=(1, 2, 3)))  # a valid image, but not the one that was checked
        body = self.do_import().json()
        self.assertEqual([s["reason"] for s in body["skipped"]], ["picture_changed_since_check"])
        self.assertFalse(FeedbackImport.objects.exists())

    def test_a_failed_database_write_leaves_no_file_behind(self) -> None:
        oid = self.observe()
        self.tp(oid, 0)
        before = self.stored_files()
        with mock.patch.object(FeedbackImport.objects, "create", side_effect=RuntimeError("disk on fire")):
            with self.assertRaises(RuntimeError):
                feedback_import.import_observation(self.project, oid, self.user)
        self.assertEqual(self.stored_files(), before)
        self.assertEqual(Asset.objects.filter(annotations__status="suggested").count(), 0)
        self.assertFalse(BoxAnnotation.objects.filter(status="suggested").exists())

    # -- W10-FX1 ---------------------------------------------------------------------------------
    def observe_picture(self, data: bytes, name: str, content_type: str) -> str:
        response = self.run_image(self.app["id"], SimpleUploadedFile(name, data, content_type=content_type))
        self.assertEqual(response.status_code, 202, response.content)
        oid = response.json()["observation_id"]
        self.tp(oid, 0)
        return oid

    @staticmethod
    def jpeg(orientation: int | None, size=(40, 30)) -> bytes:
        image = Image.new("RGB", size, (90, 40, 10))
        exif = Image.Exif()
        if orientation is not None:
            exif[0x0112] = orientation
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", exif=exif)
        return buffer.getvalue()

    SIDEWAYS = "This picture is stored sideways (the camera rotated it), so the boxes would not line up. It was left out."

    def test_a_picture_the_camera_rotated_is_left_out_with_a_plain_reason(self) -> None:
        sideways = self.observe_picture(self.jpeg(6), "sideways.jpg", "image/jpeg")
        upright = self.observe_picture(self.jpeg(1, size=(41, 30)), "upright.jpg", "image/jpeg")
        plain = self.observe_picture(self.jpeg(None, size=(42, 30)), "plain.jpg", "image/jpeg")
        files = self.stored_files()
        body = self.do_import().json()
        self.assertEqual({i["observation_id"] for i in body["imported"]}, {upright, plain})
        self.assertEqual(
            [(s["observation_id"], s["reason"], s["message"]) for s in body["skipped"]],
            [(sideways, "picture_rotated_by_camera", self.SIDEWAYS)],
        )
        self.assertEqual(self.stored_files(), files + 2)
        self.assertFalse(FeedbackImport.objects.filter(observation_id=sideways).exists())

    def test_every_other_orientation_is_also_left_out(self) -> None:
        for orientation in (2, 3, 4, 5, 7, 8):
            with self.subTest(orientation=orientation):
                oid = self.observe_picture(self.jpeg(orientation, size=(40 + orientation, 30)), "o.jpg", "image/jpeg")
                skipped = self.do_import({"observation_ids": [oid]}).json()["skipped"]
                self.assertEqual([s["reason"] for s in skipped], ["picture_rotated_by_camera"])

    def test_a_tampered_image_key_is_a_missing_picture_not_an_error_page(self) -> None:
        oid = self.observe()
        self.tp(oid, 0)
        for key in ("../../../etc/passwd", "/etc/passwd"):
            with self.subTest(key=key):
                with connection.cursor() as cursor:  # the model refuses edits to image_key; this is a tampered row
                    cursor.execute("UPDATE projects_observationrecord SET image_key = %s WHERE id = %s", [key, uuid.UUID(oid).hex])
                listing = self.client.get(self.list_url())
                self.assertEqual(listing.status_code, 200, listing.content[:200])
                self.assertEqual(listing.json()["items"][0]["reason"], "picture_file_missing")
                response = self.do_import({"observation_ids": [oid]})
                self.assertEqual(response.status_code, 200, response.content[:200])
                self.assertEqual([s["reason"] for s in response.json()["skipped"]], ["picture_file_missing"])
                default = self.do_import()
                self.assertEqual(default.status_code, 200, default.content[:200])

    def test_a_full_disk_skips_that_picture_and_keeps_the_rest(self) -> None:
        first, second = self.observe(), self.observe()
        for oid in (first, second):
            self.tp(oid, 0)
        real_save, calls = default_storage.save, []

        def save(name, content, *args, **kwargs):
            calls.append(name)
            if len(calls) == 1:
                raise OSError(errno.ENOSPC, "No space left on device")
            return real_save(name, content, *args, **kwargs)

        files, assets = self.stored_files(), Asset.objects.count()
        wrapper = mock.Mock(wraps=default_storage)
        wrapper.save.side_effect = save
        with mock.patch.object(feedback_import, "default_storage", wrapper):
            response = self.do_import({"observation_ids": [first, second]})
        self.assertEqual(response.status_code, 200, response.content[:200])
        body = response.json()
        self.assertEqual([i["observation_id"] for i in body["imported"]], [second])
        self.assertEqual(
            [(s["observation_id"], s["reason"], s["message"]) for s in body["skipped"]],
            [(first, "storage_error", "The picture could not be saved in the project (the disk may be full), so it was left out. Nothing was changed.")],
        )
        self.assertEqual(self.stored_files(), files + 1)  # nothing orphaned, nothing half-written
        self.assertEqual(Asset.objects.count(), assets + 1)
        self.assertFalse(FeedbackImport.objects.filter(observation_id=first).exists())
        self.assertEqual(self.listing()["pending"], 1)  # it can be tried again once there is room

    def test_a_picture_with_too_many_pixels_has_its_own_reason(self) -> None:
        oid = self.observe()
        self.tp(oid, 0)
        key = self.key_of(oid)
        huge = io.BytesIO()
        Image.new("L", (5100, 5000)).save(huge, "PNG")  # 25.5 million pixels, a few KB of file
        default_storage.delete(key)
        default_storage.save(key, ContentFile(huge.getvalue()))
        body = self.do_import().json()
        self.assertEqual([s["reason"] for s in body["skipped"]], ["picture_dimensions_too_large"])
        self.assertEqual(body["skipped"][0]["message"], "The picture has more pixels than the project accepts (25 megapixels), so it was left out.")
        self.assertFalse(FeedbackImport.objects.exists())

    def test_the_size_and_dimension_reasons_do_not_depend_on_the_wording_of_the_upload_check(self) -> None:
        oid = self.observe()
        self.tp(oid, 0)
        with mock.patch("projects.views._validate_uploaded_image", side_effect=ValueError("Each image must be 20 MB or smaller.")):
            skipped = self.do_import().json()["skipped"]
        self.assertEqual([s["reason"] for s in skipped], ["picture_not_a_supported_image"])  # no text matching


class IdempotenceTests(FeedbackBase):
    def test_second_import_adds_nothing(self) -> None:
        oid = self.observe()
        self.tp(oid, 0)
        first = self.do_import().json()
        assets, boxes, files = Asset.objects.count(), BoxAnnotation.objects.count(), self.stored_files()
        second = self.do_import().json()
        self.assertEqual((second["imported"], second["skipped"]), ([], []))
        self.assertEqual((Asset.objects.count(), BoxAnnotation.objects.count(), self.stored_files()), (assets, boxes, files))
        explicit = self.do_import({"observation_ids": [oid]}).json()
        self.assertEqual(explicit["imported"], [])
        self.assertEqual([(s["observation_id"], s["reason"]) for s in explicit["skipped"]], [(oid, "already_imported")])
        self.assertEqual(explicit["skipped"][0]["asset_id"], first["imported"][0]["asset_id"])
        self.assertEqual(FeedbackImport.objects.count(), 1)

    def test_new_feedback_after_an_import_does_not_create_a_second_picture(self) -> None:
        oid = self.observe()
        self.tp(oid, 0)
        self.do_import()
        self.fp(oid, 1)  # more feedback later: the picture was already taken once
        self.assertEqual(self.listing()["pending"], 0)
        self.do_import()
        self.assertEqual(FeedbackImport.objects.count(), 1)

    def test_two_stale_imports_make_exactly_one_picture(self) -> None:
        """Both callers decided 'not imported yet' before either wrote: the database has the last word."""
        oid = self.observe()
        self.tp(oid, 0)
        files = self.stored_files()
        with mock.patch.object(feedback_import, "_link_exists", return_value=False):
            first = feedback_import.import_observation(self.project, oid, self.user)
            second = feedback_import.import_observation(self.project, oid, self.user)
        self.assertEqual((first.imported, second.imported), (True, False))
        self.assertEqual(second.reason, "already_imported")
        self.assertEqual(FeedbackImport.objects.filter(observation_id=oid).count(), 1)
        self.assertEqual(Asset.objects.filter(annotations__status="suggested").distinct().count(), 1)
        self.assertEqual(BoxAnnotation.objects.filter(status="suggested").count(), 1)
        self.assertEqual(self.stored_files(), files + 1)  # the loser's copy of the file was removed again

    def test_the_link_is_unique_per_observation_in_the_database(self) -> None:
        from django.db import IntegrityError, transaction

        oid = self.observe()
        self.tp(oid, 0)
        self.do_import()
        link = FeedbackImport.objects.get()
        clone = FeedbackImport(
            project=link.project, observation=link.observation, observation_ref=oid, model_version_ref="x",
            model_version_number=1, artifact_sha256="a" * 64, image_sha256="b" * 64,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            clone.save()


class ListingTests(FeedbackBase):
    def test_shape_counts_and_order(self) -> None:
        first, second, third = self.observe(), self.observe(), self.observe()
        self.tp(first, 0)
        self.fp(second, 0)
        self.missed(second, "normal", None)
        self.tp(third, 0)
        default_storage.delete(ObservationRecord.objects.get(pk=third).image_key)
        self.do_import({"observation_ids": [first]})
        body = self.listing()
        self.assertEqual((body["pending"], body["imported"], body["cannot_import"], body["total"]), (1, 1, 1, 3))
        ids = [item["observation_id"] for item in body["items"]]
        self.assertEqual(ids, [third, second, first])  # newest first
        by_id = {item["observation_id"]: item for item in body["items"]}
        self.assertEqual(by_id[second]["state"], "pending")
        self.assertEqual(by_id[second]["kinds"], {"true_positive": 0, "false_positive": 1, "missed": 1})
        self.assertEqual(by_id[first]["state"], "imported")
        self.assertEqual(by_id[first]["asset_id"], str(self.imported_asset(first).id))
        item = by_id[second]
        self.assertEqual(item["app_id"], self.app["id"])
        self.assertEqual(item["app_name"], "Feedback line")
        self.assertEqual(item["model_version"], {"id": str(self.model.id), "version": self.model.version})
        self.assertIn("taken_at", item)
        self.assertIsNone(item["reason"])
        self.assertTrue(by_id[third]["reason"])

    def test_the_list_is_capped_but_the_counts_are_not(self) -> None:
        ids = [self.observe() for _ in range(3)]
        for oid in ids:
            self.tp(oid, 0)
        with mock.patch.object(feedback_import, "LIST_LIMIT", 2):
            body = self.listing()
        self.assertEqual((len(body["items"]), body["pending"], body["total"]), (2, 3, 3))
        self.assertEqual([i["observation_id"] for i in body["items"]], ids[::-1][:2])

    def test_listing_does_not_scale_its_queries_with_the_number_of_observations(self) -> None:
        for _ in range(2):
            self.tp(self.observe(), 0)
        with CaptureQueriesContext(connection) as small:
            self.listing()
        for _ in range(4):
            self.tp(self.observe(), 0)
        with CaptureQueriesContext(connection) as large:
            self.listing()
        self.assertLessEqual(len(large), len(small) + 1)

    # -- W10-FX1: the window is over what is still waiting ----------------------------------------
    def bulk_feedback_observations(self, count: int, *, linked_newest: int) -> list[ObservationRecord]:
        """``count`` observations with feedback, one second apart, newest first; the newest ``linked_newest`` imported."""
        template = ObservationRecord.objects.get(pk=self.observe())
        now = timezone.now()
        rows = [
            ObservationRecord(
                id=uuid.uuid4(), app_id=template.app_id, model_version=template.model_version, status="succeeded",
                image_key=template.image_key, image_sha256=template.image_sha256, filename=template.filename,
                content_type=template.content_type, size_bytes=template.size_bytes, width=template.width,
                height=template.height, config_snapshot=template.config_snapshot, predictions=template.predictions,
                created_by=self.user,
            )
            for _ in range(count)
        ]
        ObservationRecord.objects.bulk_create(rows)
        for index, row in enumerate(rows):
            ObservationRecord.objects.filter(pk=row.pk).update(created_at=now - datetime.timedelta(seconds=index + 1))
        FeedbackRecord.objects.bulk_create(
            [
                FeedbackRecord(observation=row, prediction_index=0, label="damaged", verdict="true_positive",
                               decided_by=self.user, decided_by_snapshot=self.user.pk)
                for row in rows
            ]
        )
        FeedbackImport.objects.bulk_create(
            [
                FeedbackImport(project=self.project, observation=row, observation_ref=str(row.pk),
                               model_version_ref=str(template.model_version_id), model_version_number=1,
                               artifact_sha256="a" * 64, image_sha256=row.image_sha256)
                for row in rows[:linked_newest]
            ]
        )
        return rows

    def test_older_pending_results_stay_reachable_when_the_newest_2000_are_imported(self) -> None:
        rows = self.bulk_feedback_observations(2010, linked_newest=2000)
        listing_queries = CaptureQueriesContext(connection)
        with listing_queries:
            body = self.listing()
        self.assertEqual(
            (body["pending"], body["imported"], body["cannot_import"], body["total"], body["truncated"]),
            (10, 2000, 0, 2010, False),
        )
        self.assertEqual([i["state"] for i in body["items"]].count("pending"), 10)  # what needs doing is never cut off
        self.assertEqual(len(body["items"]), feedback_import.LIST_LIMIT)
        waiting = {str(r.pk) for r in rows[2000:]}
        self.assertEqual({i["observation_id"] for i in body["items"] if i["state"] == "pending"}, waiting)
        self.assertLessEqual(len(listing_queries), 10)  # 5 for the request itself + 5 for the list, whatever the backlog

        response = self.do_import()
        self.assertEqual(response.status_code, 200, response.content[:200])
        totals = response.json()["totals"]
        self.assertEqual((totals["requested"], totals["imported"], totals["remaining_pending"]), (10, 10, 0))
        self.assertEqual(self.listing()["imported"], 2010)

    def test_the_list_queries_do_not_grow_with_a_big_backlog(self) -> None:
        self.tp(self.observe(), 0)
        with CaptureQueriesContext(connection) as small:
            self.listing()
        self.bulk_feedback_observations(300, linked_newest=100)
        with CaptureQueriesContext(connection) as large:
            self.listing()
        self.assertLessEqual(len(large), len(small) + 1)

    def test_a_window_smaller_than_the_backlog_is_over_what_is_waiting_and_moves_on(self) -> None:
        self.bulk_feedback_observations(8, linked_newest=3)  # 5 waiting, 3 already pulled in (the newest)
        with mock.patch.object(feedback_import, "SCAN_LIMIT", 3):
            first = self.listing()
            self.assertEqual((first["pending"], first["imported"], first["total"], first["truncated"]), (3, 3, 8, True))
            self.assertEqual(self.do_import().json()["totals"]["imported"], 3)
            second = self.listing()
            self.assertEqual((second["pending"], second["imported"], second["total"], second["truncated"]), (2, 6, 8, False))

    def test_cannot_import_is_counted_only_among_the_results_looked_at(self) -> None:
        good, gone = self.observe(), self.observe()
        for oid in (good, gone):
            self.tp(oid, 0)
        default_storage.delete(ObservationRecord.objects.get(pk=gone).image_key)
        body = self.listing()
        self.assertEqual((body["pending"], body["cannot_import"], body["cannot_import_scanned"]), (1, 1, 2))

    def test_an_imported_picture_shows_how_many_boxes_were_left_out_and_why(self) -> None:
        oid = self.observe()
        self.tp(oid, 1)
        self.missed(oid, "normal", MISSED_BOX)
        self.tp(oid, 0)
        clean = self.observe()
        self.tp(clean, 0)
        Project.objects.filter(pk=self.project.pk).update(labels=["damaged", "Normal"])
        self.do_import()
        by_id = {item["observation_id"]: item for item in self.listing()["items"]}
        item = by_id[oid]
        self.assertEqual((item["state"], item["boxes_created"], item["boxes_skipped"]), ("imported", 1, 2))
        self.assertEqual(
            item["skipped_reasons"],
            [{"reason": "label_not_in_project", "count": 2, "message": feedback_import.MESSAGES["label_not_in_project"]}],
        )
        self.assertEqual((by_id[clean]["boxes_skipped"], by_id[clean]["skipped_reasons"]), (0, []))


class BatchTests(FeedbackBase):
    def test_default_import_is_capped_per_request(self) -> None:
        ids = [self.observe() for _ in range(3)]
        for oid in ids:
            self.tp(oid, 0)
        self.assertEqual(feedback_import.IMPORT_BATCH_LIMIT, 50)
        with mock.patch.object(feedback_import, "IMPORT_BATCH_LIMIT", 2):
            body = self.do_import().json()
            self.assertEqual(len(body["imported"]), 2)
            self.assertEqual(body["totals"]["remaining_pending"], 1)
            self.assertEqual(self.do_import({"observation_ids": ids}).status_code, 400)  # too many named at once
        self.assertEqual(self.listing()["pending"], 1)
        self.assertEqual(len(self.do_import().json()["imported"]), 1)
        self.assertEqual(self.listing()["pending"], 0)

    def test_bad_requests(self) -> None:
        for payload in ({"observation_ids": "x"}, {"observation_ids": ["nope"]}, {"observation_ids": [1]}, {"surprise": 1}, {"observation_ids": [None]}):
            with self.subTest(payload=payload):
                self.assertEqual(self.do_import(payload).status_code, 400)
        self.assertEqual(self.client.post(self.import_url(), data="[]", content_type="application/json").status_code, 400)
        self.assertEqual(self.do_import({"observation_ids": []}).json()["imported"], [])  # empty list: nothing, not "everything"

    def test_the_same_id_twice_is_imported_once(self) -> None:
        oid = self.observe()
        self.tp(oid, 0)
        body = self.do_import({"observation_ids": [oid, oid]}).json()
        self.assertEqual((len(body["imported"]), len(body["skipped"])), (1, 0))


class AccessTests(FeedbackBase):
    def setUp(self) -> None:
        super().setUp()
        self.oid = self.observe()
        self.tp(self.oid, 0)

    def test_role_matrix(self) -> None:
        viewer, labeler = client_for(make_user("v", "viewer")), client_for(make_user("l", "labeler"))
        self.assertEqual(viewer.get(self.list_url()).status_code, 200)
        denied = self.do_import(client=viewer)
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json()["required_role"], "labeler")
        self.assertEqual(denied["Cache-Control"], "no-store")
        self.assertFalse(FeedbackImport.objects.exists())
        self.assertEqual(self.do_import(client=labeler).status_code, 200)
        self.assertEqual(FeedbackImport.objects.get().imported_by_role, "labeler")
        self.assertEqual(labeler.get(self.list_url()).status_code, 200)

    def test_anonymous_callers_get_401(self) -> None:
        anonymous = Client()
        self.assertEqual(anonymous.get(self.list_url()).status_code, 401)
        self.assertEqual(self.do_import(client=anonymous).status_code, 401)
        self.assertFalse(FeedbackImport.objects.exists())

    def test_csrf_and_methods(self) -> None:
        self.assertEqual(self.csrf_client().post(self.import_url(), data="{}", content_type="application/json").status_code, 403)
        self.assertEqual(self.client.get(self.import_url()).status_code, 405)
        self.assertEqual(self.client.post(self.list_url(), data="{}", content_type="application/json").status_code, 405)

    def test_a_role_given_for_one_project_applies_there(self) -> None:
        from .models import ProjectRole

        user = make_user("limited", "viewer")
        ProjectRole.objects.create(project=self.project, user=user, role="labeler")
        self.assertEqual(self.do_import(client=client_for(user)).status_code, 200)

    def test_other_projects_and_other_modalities_leak_nothing(self) -> None:
        other = Project.objects.create(name="Other", modality="image", task="Detect", labels=["damaged", "normal"])
        text = Project.objects.create(name="Docs", modality="text", task="Tag", labels=["a"])
        self.assertEqual(self.listing(other)["total"], 0)
        body = self.do_import({"observation_ids": [self.oid, str(uuid.uuid4())]}, project=other).json()
        self.assertEqual(body["imported"], [])
        self.assertEqual({s["reason"] for s in body["skipped"]}, {"not_found"})  # same answer for 'elsewhere' and 'never existed'
        self.assertEqual({s["message"] for s in body["skipped"]}, {body["skipped"][0]["message"]})
        self.assertFalse(FeedbackImport.objects.exists())
        self.assertEqual(Asset.objects.filter(project=other).count(), 0)
        default_import = self.do_import(project=other).json()
        self.assertEqual(default_import["imported"], [])
        for project in (text,):
            self.assertEqual(self.client.get(self.list_url(project)).status_code, 404)
            self.assertEqual(self.do_import(project=project).status_code, 404)
        self.assertEqual(self.client.get(f"/api/projects/{uuid.uuid4()}/feedback/corrections").status_code, 404)
        self.assertEqual(self.do_import(project=type("P", (), {"id": uuid.uuid4()})()).status_code, 404)


class ProvenanceAndSafetyTests(FeedbackBase):
    def test_model_run_dataset_and_app_rows_are_untouched(self) -> None:
        oid = self.observe()
        self.tp(oid, 0)
        self.fp(oid, 1)
        self.missed(oid, "normal", MISSED_BOX)
        models = (ModelVersion, TrainingRun, DatasetVersion, InferenceApp, ObservationRecord, FeedbackRecord)
        before = {model.__name__: row_digest(model) for model in models}
        approved = ModelVersion.objects.filter(stage="approved").values_list("pk", flat=True)
        self.do_import()
        self.do_import()
        self.assertEqual({model.__name__: row_digest(model) for model in models}, before)
        self.assertEqual(list(ModelVersion.objects.filter(stage="approved").values_list("pk", flat=True)), list(approved))

    def test_deleting_the_picture_keeps_the_text_provenance(self) -> None:
        oid = self.observe()
        self.tp(oid, 0)
        asset_id = self.do_import().json()["imported"][0]["asset_id"]
        response = self.client.delete(self.api(f"/assets/{asset_id}"))
        self.assertEqual(response.status_code, 200, response.content)
        link = FeedbackImport.objects.get(observation_id=oid)
        self.assertIsNone(link.asset_id)
        self.assertEqual(link.asset_ref, asset_id)
        self.assertEqual((link.observation_ref, link.model_version_ref, link.artifact_sha256), (oid, str(self.model.id), self.model.artifact_sha256))
        self.assertEqual(link.annotation_ids and len(link.annotation_ids), 1)
        item = self.listing()["items"][0]
        self.assertEqual((item["state"], item["asset_id"]), ("imported", None))  # taken once; not offered again
        self.assertEqual(self.do_import().json()["imported"], [])

    def test_the_link_cannot_be_edited_or_deleted(self) -> None:
        oid = self.observe()
        self.tp(oid, 0)
        self.do_import()
        link = FeedbackImport.objects.get()
        link.boxes_created = 99
        with self.assertRaises(ValueError):
            link.save()
        with self.assertRaises(ValueError):
            link.delete()
        with self.assertRaises(ValueError):
            FeedbackImport.objects.all().update(boxes_created=0)
        with self.assertRaises(ValueError):
            FeedbackImport.objects.all().delete()
        with self.assertRaises(ValueError):
            FeedbackImport.objects.all()._raw_delete(using="default")
        self.assertEqual(FeedbackImport.objects.get().boxes_created, 1)

    def test_deleting_the_project_still_works(self) -> None:
        oid = self.observe()
        self.tp(oid, 0)
        self.do_import()
        self.assertEqual(self.client.delete(f"/api/projects/{self.project.id}").status_code, 200)
        self.assertFalse(FeedbackImport.objects.exists())


class NotGroundTruthTests(FeedbackBase):
    """Imported boxes say Needs review. Nothing counts them until a person accepts them."""

    def setUp(self) -> None:
        super().setUp()
        self.oid = self.observe()
        self.tp(self.oid, 0)
        self.missed(self.oid, "normal", MISSED_BOX)
        self.reference = dataset_service.analyse(self.project, SPLIT)
        self.before_images = len(self.reference.assets)
        self.before_boxes = len(self.reference.accepted)

    def test_snapshot_readiness_and_health_ignore_unreviewed_boxes(self) -> None:
        self.do_import()
        analysis = dataset_service.analyse(self.project, SPLIT)
        self.assertEqual((len(analysis.assets), len(analysis.accepted)), (self.before_images, self.before_boxes))
        self.assertEqual(analysis.suggested_boxes, self.reference.suggested_boxes + 2)
        self.assertEqual(analysis.images_without_accepted, self.reference.images_without_accepted + 1)
        self.assertEqual(analysis.fingerprint, self.reference.fingerprint)

        readiness = self.client.get(f"/api/projects/{self.project.id}/datasets/readiness").json()
        self.assertEqual(readiness["excluded"]["suggested_boxes"], self.reference.suggested_boxes + 2)
        self.assertEqual(readiness["counts"]["images"], self.before_images)
        self.assertEqual(readiness["counts"]["accepted_boxes"], self.before_boxes)
        self.assertEqual(readiness["counts"]["images_total"], self.before_images + 1)

        health = self.client.get(f"/api/projects/{self.project.id}/datasets/health").json()
        self.assertEqual(health["pictures"]["with_boxes"], self.before_images)
        self.assertEqual(health["pictures"]["without_boxes"], self.reference.images_without_accepted + 1)
        self.assertEqual(health["pictures"]["needs_review_boxes"], self.reference.suggested_boxes + 2)

        created = self.create_version_over_http()
        self.assertEqual((created["image_count"], created["box_count"]), (self.before_images, self.before_boxes))
        self.assertEqual(created["excluded_suggested_count"], self.reference.suggested_boxes + 2)
        asset = self.imported_asset(self.oid)
        self.assertNotIn(str(asset.id), {image["asset_id"] for image in created["manifest"]["images"]})
        self.assertNotIn(asset.sha256, {image["sha256"] for image in created["manifest"]["images"]})

    def test_export_of_a_snapshot_taken_after_import_has_no_imported_picture(self) -> None:
        self.do_import()
        version = self.create_version_over_http()
        response = self.client.get(f"/api/projects/{self.project.id}/datasets/{version['id']}/export?format=yolo")
        self.assertEqual(response.status_code, 200)
        import io
        import zipfile

        names = zipfile.ZipFile(io.BytesIO(b"".join(response.streaming_content))).namelist()
        asset = self.imported_asset(self.oid)
        self.assertFalse([name for name in names if str(asset.id) in name])
        self.assertEqual(len([n for n in names if n.startswith("images/")]), self.before_images)

    def test_training_readiness_is_unchanged(self) -> None:
        before = self.client.get(self.api("/training/readiness")).json()
        self.do_import()
        after = self.client.get(self.api("/training/readiness")).json()
        self.assertEqual(after, before)

    def test_after_a_person_accepts_them_the_next_snapshot_includes_the_picture_with_its_provenance(self) -> None:
        self.do_import()
        asset = self.imported_asset(self.oid)
        response = self.post(self.api("/annotations/review"), {"action": "accept", "asset_id": str(asset.id)})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["updated"], 2)
        analysis = dataset_service.analyse(self.project, SPLIT)
        self.assertEqual((len(analysis.assets), len(analysis.accepted)), (self.before_images + 1, self.before_boxes + 2))
        created = self.create_version_over_http()
        self.assertEqual((created["image_count"], created["box_count"]), (self.before_images + 1, self.before_boxes + 2))
        image = next(i for i in created["manifest"]["images"] if i["asset_id"] == str(asset.id))
        self.assertEqual(len(image["boxes"]), 2)
        reviewers = {box["reviewed_by"] for box in image["boxes"]}
        self.assertEqual(reviewers, {f"u{self.user.id}"})  # a person decided; the box says who
        link = FeedbackImport.objects.get(asset_id=image["asset_id"])
        self.assertEqual(str(link.observation_id), self.oid)
        self.assertEqual(link.artifact_sha256, self.model.artifact_sha256)
        self.assertEqual({box["annotation_id"] for box in image["boxes"]}, set(link.annotation_ids))
        self.assertTrue(all("operator feedback" in box["model_name"] for box in image["boxes"]))  # visible in the snapshot too

    def test_editing_one_box_accepts_only_that_box(self) -> None:
        self.do_import()
        asset = self.imported_asset(self.oid)
        box = asset.annotations.filter(label="damaged").get()
        patched = self.client.patch(
            self.api(f"/assets/{asset.id}/annotations/{box.id}"),
            data=json.dumps({"label": "damaged"}), content_type="application/json",
        )
        self.assertEqual(patched.status_code, 200)
        self.assertEqual(sorted(asset.annotations.values_list("status", flat=True)), ["accepted", "suggested"])
        self.assertEqual(len(dataset_service.analyse(self.project, SPLIT).accepted), self.before_boxes + 1)

    def create_version_over_http(self) -> dict:
        response = self.post(f"/api/projects/{self.project.id}/datasets", {})
        self.assertEqual(response.status_code, 201, response.content)
        version = DatasetVersion.objects.get(pk=response.json()["dataset"]["id"])
        return {
            "id": str(version.id), "image_count": version.image_count, "box_count": version.box_count,
            "excluded_suggested_count": version.excluded_suggested_count, "manifest": version.manifest,
        }


class WholeResultDecisionTests(FeedbackBase):
    """A Reject / Needs-a-second-look on the whole result reaches the supervisor even with no per-detection feedback."""

    def items(self) -> dict:
        return {item["observation_id"]: item for item in self.listing()["items"]}

    def test_reject_only_is_waiting_and_imports_the_picture_with_no_boxes(self) -> None:
        oid = self.observe()
        self.decide(oid, "reject", "The tin is fine, the model is wrong.")
        body = self.listing()
        self.assertEqual((body["pending"], body["imported"], body["total"]), (1, 0, 1))
        item = self.items()[oid]
        self.assertEqual((item["state"], item["decision"]), ("pending", "reject"))
        self.assertEqual(item["decision_note"], "The tin is fine, the model is wrong.")
        self.assertEqual(item["kinds"], {"true_positive": 0, "false_positive": 0, "missed": 0})

        result = self.do_import().json()
        self.assertEqual(result["skipped"], [])
        self.assertEqual(len(result["imported"]), 1)
        imported = result["imported"][0]
        self.assertEqual((imported["observation_id"], imported["boxes_created"]), (oid, 0))
        self.assertEqual((imported["decision"], imported["decision_note"]), ("reject", "The tin is fine, the model is wrong."))
        self.assertEqual(result["totals"]["pictures_from_decisions"], 1)
        self.assertEqual(result["totals"]["boxes_created"], 0)
        asset = Asset.objects.get(pk=imported["asset_id"])
        self.assertEqual(asset.annotations.count(), 0)  # the model's boxes are not trusted ground truth
        self.assertEqual(FeedbackImport.objects.get().boxes_created, 0)
        after = self.items()[oid]
        self.assertEqual((after["state"], after["decision"], after["boxes_created"]), ("imported", "reject", 0))
        self.assertEqual(self.listing()["pending"], 0)

    def test_second_look_only_is_waiting_and_named_second_look(self) -> None:
        oid = self.observe()
        self.decide(oid, "needs_second_look", "Glare on the lid.")
        item = self.items()[oid]
        self.assertEqual((item["state"], item["decision"], item["decision_note"]), ("pending", "second_look", "Glare on the lid."))
        imported = self.do_import().json()["imported"][0]
        self.assertEqual((imported["decision"], imported["boxes_created"]), ("second_look", 0))

    def test_confirm_only_is_not_eligible(self) -> None:
        oid = self.observe()
        self.decide(oid, "confirm")
        assets = Asset.objects.count()
        body = self.listing()
        self.assertEqual((body["pending"], body["total"], body["items"]), (0, 0, []))
        result = self.do_import({"observation_ids": [oid]}).json()
        self.assertEqual([(s["observation_id"], s["reason"]) for s in result["skipped"]], [(oid, "no_feedback")])
        self.assertEqual((Asset.objects.count(), FeedbackImport.objects.count()), (assets, 0))
        self.assertEqual(self.do_import().json()["totals"]["requested"], 0)

    def test_the_latest_decision_wins(self) -> None:
        rejected_then_confirmed, confirmed_then_rejected = self.observe(), self.observe()
        self.decide(rejected_then_confirmed, "reject", "No.")
        self.decide(rejected_then_confirmed, "confirm")
        self.decide(confirmed_then_rejected, "confirm")
        self.decide(confirmed_then_rejected, "reject", "On reflection, no.")
        items = self.items()
        self.assertEqual(sorted(items), [confirmed_then_rejected])
        self.assertEqual(items[confirmed_then_rejected]["decision"], "reject")
        self.assertEqual(items[confirmed_then_rejected]["decision_note"], "On reflection, no.")
        result = self.do_import({"observation_ids": [rejected_then_confirmed]}).json()
        self.assertEqual(result["skipped"][0]["reason"], "no_feedback")

    def test_reject_with_per_detection_feedback_behaves_as_before_and_reports_the_decision(self) -> None:
        oid = self.observe()
        self.tp(oid, 0)
        self.fp(oid, 1)
        self.decide(oid, "reject", "Half of it is wrong.")
        item = self.items()[oid]
        self.assertEqual(item["kinds"], {"true_positive": 1, "false_positive": 1, "missed": 0})
        self.assertEqual(item["decision"], "reject")
        result = self.do_import().json()
        imported = result["imported"][0]
        self.assertEqual((imported["boxes_created"], imported["false_alarms"], imported["decision"]), (1, 1, "reject"))
        self.assertEqual(result["totals"]["pictures_from_decisions"], 0)  # it has something to teach from: not decision-only
        self.assertEqual(Asset.objects.get(pk=imported["asset_id"]).annotations.count(), 1)

    def test_feedback_only_picture_has_no_decision(self) -> None:
        oid = self.observe()
        self.tp(oid, 0)
        item = self.items()[oid]
        self.assertIsNone(item["decision"])
        self.assertEqual(item["decision_note"], "")
        self.assertIsNone(self.do_import().json()["imported"][0]["decision"])

    def test_the_note_is_capped_and_is_data_not_markup(self) -> None:
        oid = self.observe()
        note = "<script>alert(1)</script>" + "x" * 600
        self.decide(oid, "reject", note)
        shown = self.items()[oid]["decision_note"]
        self.assertEqual(len(shown), 300)
        self.assertTrue(shown.startswith("<script>alert(1)</script>"))
        self.assertEqual(self.client.get(self.list_url())["Content-Type"], "application/json")

    def test_importing_twice_adds_nothing_and_counts_match_the_list(self) -> None:
        oid, other = self.observe(), self.observe()
        self.decide(oid, "reject", "No.")
        self.tp(other, 0)
        first = self.do_import().json()
        self.assertEqual((first["totals"]["imported"], first["totals"]["pictures_from_decisions"]), (2, 1))
        assets, files = Asset.objects.count(), self.stored_files()
        second = self.do_import().json()
        self.assertEqual((second["imported"], second["skipped"], second["totals"]["pictures_from_decisions"]), ([], [], 0))
        explicit = self.do_import({"observation_ids": [oid]}).json()
        self.assertEqual([s["reason"] for s in explicit["skipped"]], ["already_imported"])
        self.assertEqual((Asset.objects.count(), self.stored_files(), FeedbackImport.objects.count()), (assets, files, 2))
        body = self.listing()
        self.assertEqual((body["pending"], body["imported"], body["total"], len(body["items"])), (0, 2, 2, 2))

    def test_counts_agree_with_the_list(self) -> None:
        a, b, c, d = (self.observe() for _ in range(4))
        self.decide(a, "reject", "No.")
        self.decide(b, "needs_second_look")
        self.decide(c, "confirm")
        self.tp(d, 0)
        body = self.listing()
        self.assertEqual((body["pending"], body["imported"], body["cannot_import"], body["total"]), (3, 0, 0, 3))
        self.assertEqual(sorted(item["observation_id"] for item in body["items"]), sorted([a, b, d]))
        self.assertEqual(sum(1 for item in body["items"] if item["state"] == "pending"), body["pending"])

    def test_a_decision_on_a_picture_that_cannot_be_copied_is_named_not_hidden(self) -> None:
        oid = self.observe()
        self.decide(oid, "reject", "No.")
        default_storage.delete(ObservationRecord.objects.get(pk=oid).image_key)
        item = self.items()[oid]
        self.assertEqual((item["state"], item["decision"]), ("cannot_import", "reject"))
        self.assertTrue(item["reason"])

    def test_role_matrix_is_unchanged(self) -> None:
        oid = self.observe()
        self.decide(oid, "reject", "No.")
        viewer, labeler = client_for(make_user("v", "viewer")), client_for(make_user("l", "labeler"))
        self.assertEqual(viewer.get(self.list_url()).status_code, 200)
        self.assertEqual(viewer.get(self.list_url()).json()["pending"], 1)
        self.assertEqual(self.do_import(client=viewer).status_code, 403)
        self.assertFalse(FeedbackImport.objects.exists())
        self.assertEqual(self.do_import(client=labeler).status_code, 200)
        self.assertEqual(FeedbackImport.objects.get().imported_by_role, "labeler")

    def test_decisions_of_another_project_do_not_leak(self) -> None:
        oid = self.observe()
        self.decide(oid, "reject", "No.")
        other = Project.objects.create(name="Other", modality="image", task="t", labels=["damaged"])
        body = self.client.get(self.list_url(other)).json()
        self.assertEqual((body["pending"], body["total"], body["items"]), (0, 0, []))
        self.assertEqual(self.do_import({"observation_ids": [oid]}, project=other).json()["skipped"][0]["reason"], "not_found")

    def test_decisions_do_not_scale_the_list_queries(self) -> None:
        ids = [self.observe() for _ in range(2)]
        for oid in ids:
            self.decide(oid, "reject", "No.")
        with CaptureQueriesContext(connection) as small:
            self.listing()
        more = [self.observe() for _ in range(6)]
        for oid in more:
            self.decide(oid, "reject", "No.")
        with CaptureQueriesContext(connection) as large:
            self.listing()
        self.assertEqual(len(large), len(small))

    def test_new_keys_are_additive_on_a_listing_item(self) -> None:
        oid = self.observe()
        self.tp(oid, 0)
        item = self.items()[oid]
        for key in ("observation_id", "app_id", "app_name", "taken_at", "model_version", "kinds", "state", "reason", "message"):
            self.assertIn(key, item)
        self.assertIn("decision", item)
        self.assertIn("decision_note", item)


class ScanCountTests(FeedbackBase):
    def test_scanned_is_how_many_waiting_results_were_looked_at_and_total_stays(self) -> None:
        ids = [self.observe() for _ in range(3)]
        for oid in ids:
            self.tp(oid, 0)
        self.do_import({"observation_ids": [ids[0]]})
        body = self.listing()
        self.assertEqual((body["total"], body["scanned"], body["cannot_import_scanned"]), (3, 2, 2))  # 1 pulled in + 2 waiting
        with mock.patch.object(feedback_import, "SCAN_LIMIT", 1):
            capped = self.listing()
        self.assertEqual((capped["total"], capped["scanned"], capped["cannot_import_scanned"], capped["truncated"]), (3, 1, 1, True))
        self.assertLessEqual(capped["scanned"], 1)

    def test_scanned_is_zero_when_nothing_is_waiting(self) -> None:
        body = self.listing()
        self.assertEqual((body["scanned"], body["total"]), (0, 0))
