from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import uuid
from unittest import mock
import zipfile
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.db import IntegrityError
from django.test import Client, TestCase, override_settings
from PIL import Image

from .datasets import service as dataset_service, splits, validation
from .models import Asset, BoxAnnotation, DatasetBox, DatasetImage, DatasetVersion, ExportEvent, Project
from .tests import SignedInMixin


def png_bytes(color: tuple[int, int, int], size: tuple[int, int] = (40, 30)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color=color).save(buffer, format="PNG")
    return buffer.getvalue()


class DatasetBase(SignedInMixin, TestCase):
    LABELS = ["damaged", "normal"]

    def setUp(self) -> None:
        self.sign_in()
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        override = override_settings(MEDIA_ROOT=self.media.name)
        override.enable()
        self.addCleanup(override.disable)
        self.project = Project.objects.create(
            name="Line 4", modality="image", task="Object detection", labels=list(self.LABELS)
        )
        self._counter = 0

    # -- fixtures -----------------------------------------------------------------
    def make_asset(self, project=None, *, data: bytes | None = None, size=(40, 30), filename=None) -> Asset:
        project = project or self.project
        self._counter += 1
        if data is None:
            data = png_bytes((self._counter % 256, (self._counter * 7) % 256, 90), size)
        asset_id = uuid.uuid4()
        key = default_storage.save(
            f"projects/{project.id}/assets/{asset_id}.png", io.BytesIO(data)
        )
        return Asset.objects.create(
            id=asset_id,
            project=project,
            filename=filename or f"item-{self._counter}.png",
            storage_key=key,
            content_type="image/png",
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
            width=size[0],
            height=size[1],
        )

    def make_box(self, asset, label="damaged", x="0.1", y="0.2", w="0.3", h="0.4", **extra) -> BoxAnnotation:
        return BoxAnnotation.objects.create(
            asset=asset,
            label=label,
            x=Decimal(x),
            y=Decimal(y),
            width=Decimal(w),
            height=Decimal(h),
            **extra,
        )

    def make_suggestion(self, asset, label="damaged", **kw) -> BoxAnnotation:
        return self.make_box(
            asset,
            label,
            origin=BoxAnnotation.ORIGIN_MODEL,
            status=BoxAnnotation.STATUS_SUGGESTED,
            confidence=0.8,
            model_name="fake",
            **kw,
        )

    def labelled_asset(self, **kwargs) -> Asset:
        asset = self.make_asset(**kwargs)
        self.make_box(asset)
        return asset

    # -- URLs ---------------------------------------------------------------------
    def url(self, suffix: str = "", project=None) -> str:
        return f"/api/projects/{(project or self.project).id}/datasets{suffix}"

    def create_version(self, payload: dict | None = None, project=None):
        return self.client.post(
            self.url(project=project),
            data=json.dumps(payload or {}),
            content_type="application/json",
        )

    def export(self, version_id, fmt="coco", client=None, project=None):
        client = client or self.client
        return client.get(self.url(f"/{version_id}/export", project=project) + f"?format={fmt}")

    @staticmethod
    def read_zip(response) -> zipfile.ZipFile:
        content = b"".join(response.streaming_content)
        return zipfile.ZipFile(io.BytesIO(content))


class AccessTests(DatasetBase):
    def test_every_dataset_route_requires_sign_in(self) -> None:
        anonymous = Client()
        version_id = uuid.uuid4()
        for method, suffix in (
            ("get", ""),
            ("post", ""),
            ("get", "/readiness"),
            ("get", f"/{version_id}"),
            ("get", f"/{version_id}/export?format=coco"),
        ):
            with self.subTest(method=method, suffix=suffix):
                response = getattr(anonymous, method)(self.url(suffix))
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json(), {"error": "Sign in to continue."})

    def test_other_projects_datasets_are_not_reachable_through_this_project(self) -> None:
        other = Project.objects.create(
            name="Other", modality="image", task="Detect", labels=["damaged"]
        )
        asset = self.make_asset(project=other)
        self.make_box(asset)
        created = self.create_version(project=other)
        self.assertEqual(created.status_code, 201)
        version_id = created.json()["dataset"]["id"]

        self.assertEqual(self.client.get(self.url(f"/{version_id}")).status_code, 404)
        self.assertEqual(self.export(version_id).status_code, 404)
        self.assertEqual(self.client.get(self.url()).json()["datasets"], [])
        self.assertEqual(self.client.get(self.url(f"/{version_id}", project=other)).status_code, 200)

    def test_unknown_project_and_non_image_project_are_404(self) -> None:
        missing = f"/api/projects/{uuid.uuid4()}/datasets"
        self.assertEqual(self.client.get(missing).status_code, 404)
        self.assertEqual(self.client.get(missing + "/readiness").status_code, 404)
        text = Project.objects.create(name="Docs", modality="text", task="Tag", labels=["a"])
        self.assertEqual(self.client.get(self.url(project=text)).status_code, 404)

    def test_unsupported_methods_are_rejected(self) -> None:
        self.labelled_asset()
        version_id = self.create_version().json()["dataset"]["id"]
        for method in ("put", "patch", "delete"):
            with self.subTest(method=method):
                self.assertEqual(getattr(self.client, method)(self.url(f"/{version_id}")).status_code, 405)
        self.assertEqual(self.client.delete(self.url()).status_code, 405)
        self.assertEqual(self.client.post(self.url("/readiness")).status_code, 405)

    def test_post_requires_json(self) -> None:
        self.labelled_asset()
        response = self.client.post(self.url(), data="x=1", content_type="text/plain")
        self.assertEqual(response.status_code, 415)


class ReviewGatingTests(DatasetBase):
    def test_suggested_boxes_never_enter_a_version_and_are_reported(self) -> None:
        asset = self.make_asset()
        accepted = self.make_box(asset, "damaged")
        self.make_suggestion(asset, "normal", x="0.5", y="0.5", w="0.2", h="0.2")
        self.make_suggestion(asset, "damaged", x="0.6", y="0.1", w="0.1", h="0.1")
        only_suggested = self.make_asset()
        self.make_suggestion(only_suggested, "normal")

        response = self.create_version()

        self.assertEqual(response.status_code, 201)
        body = response.json()["dataset"]
        self.assertEqual(body["counts"]["boxes"], 1)
        self.assertEqual(body["counts"]["images"], 1)
        excluded = body["excluded"]
        self.assertEqual(excluded["suggested_boxes"], 3)
        self.assertEqual(excluded["images_without_accepted_boxes"], 1)
        self.assertIn("Needs review", excluded["reason"])
        stored = DatasetBox.objects.filter(image__version_id=body["id"])
        self.assertEqual(stored.count(), 1)
        self.assertEqual(stored.get().source_annotation_id, accepted.id)

    def test_accepted_model_boxes_are_exportable_and_keep_their_provenance(self) -> None:
        asset = self.make_asset()
        reviewer = get_user_model().objects.create_user("reviewer", email="rev@example.com")
        self.make_box(
            asset,
            origin=BoxAnnotation.ORIGIN_MODEL,
            status=BoxAnnotation.STATUS_ACCEPTED,
            confidence=0.9,
            model_name="sam-clip",
            reviewed_by=reviewer,
        )
        response = self.create_version()
        self.assertEqual(response.status_code, 201)
        box = response.json()["dataset"]["manifest"]["images"][0]["boxes"][0]
        self.assertEqual(box["origin"], "model")
        self.assertEqual(box["reviewed_by"], f"u{reviewer.pk}")
        self.assertNotIn("rev@example.com", json.dumps(response.json()))

    def test_zero_accepted_boxes_is_refused_with_a_clear_message(self) -> None:
        asset = self.make_asset()
        self.make_suggestion(asset)
        self.make_asset()  # no boxes at all

        response = self.create_version()

        self.assertEqual(response.status_code, 400)
        message = response.json()["error"]
        self.assertIn("accepted", message)
        self.assertEqual(response.json()["excluded"]["suggested_boxes"], 1)
        self.assertEqual(DatasetVersion.objects.count(), 0)

    def test_project_without_images_is_refused(self) -> None:
        response = self.create_version()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(DatasetVersion.objects.count(), 0)


class ValidationTests(DatasetBase):
    def problems_for(self, response) -> list[dict]:
        self.assertEqual(response.status_code, 422, response.content)
        self.assertEqual(DatasetVersion.objects.count(), 0)
        return response.json()["problems"]

    def test_label_not_in_project_blocks_the_version(self) -> None:
        asset = self.make_asset()
        self.make_box(asset, "damaged")
        self.make_box(asset, "ghost")
        problems = self.problems_for(self.create_version())
        self.assertEqual([p["code"] for p in problems], ["label_not_in_project"])
        self.assertEqual(problems[0]["asset_id"], str(asset.id))
        self.assertIn("ghost", problems[0]["message"])

    def test_label_match_is_case_sensitive(self) -> None:
        asset = self.make_asset()
        self.make_box(asset, "Damaged")
        problems = self.problems_for(self.create_version())
        self.assertEqual(problems[0]["code"], "label_not_in_project")

    def test_missing_file_blocks_the_version(self) -> None:
        asset = self.labelled_asset()
        default_storage.delete(asset.storage_key)
        problems = self.problems_for(self.create_version())
        self.assertEqual(problems[0]["code"], "file_missing")
        self.assertEqual(problems[0]["asset_id"], str(asset.id))

    def test_changed_file_content_blocks_the_version(self) -> None:
        asset = self.labelled_asset()
        Path(default_storage.path(asset.storage_key)).write_bytes(png_bytes((1, 2, 3)))
        problems = self.problems_for(self.create_version())
        self.assertEqual(problems[0]["code"], "hash_mismatch")
        self.assertEqual(problems[0]["asset_id"], str(asset.id))

    def test_every_problem_is_reported_not_just_the_first(self) -> None:
        missing = self.labelled_asset()
        default_storage.delete(missing.storage_key)
        bad_label = self.make_asset()
        self.make_box(bad_label, "ghost")
        self.labelled_asset()  # healthy
        problems = self.problems_for(self.create_version())
        self.assertEqual({(p["asset_id"], p["code"]) for p in problems}, {
            (str(missing.id), "file_missing"),
            (str(bad_label.id), "label_not_in_project"),
        })

    def test_geometry_rules(self) -> None:
        def box(x, y, w, h):
            return SimpleNamespace(
                id=uuid.uuid4(), label="damaged", x=Decimal(x), y=Decimal(y), width=Decimal(w), height=Decimal(h)
            )

        cases = {
            "ok": (box("0.1", "0.1", "0.5", "0.5"), None),
            "edge": (box("0", "0", "1", "1"), None),
            "negative_x": (box("-0.1", "0", "0.5", "0.5"), "geometry_out_of_range"),
            "overflow": (box("0.6", "0", "0.5", "0.5"), "geometry_out_of_range"),
            "zero_width": (box("0.1", "0.1", "0", "0.5"), "non_positive_size"),
            "negative_height": (box("0.1", "0.1", "0.5", "-0.1"), "non_positive_size"),
        }
        for name, (item, expected) in cases.items():
            with self.subTest(name):
                found = validation.geometry_problem(item)
                self.assertEqual(found.code if found else None, expected)

    def test_non_finite_geometry_is_rejected(self) -> None:
        item = SimpleNamespace(id=uuid.uuid4(), x=Decimal("NaN"), y=0, width=Decimal("0.5"), height=Decimal("0.5"))
        self.assertEqual(validation.geometry_problem(item).code, "geometry_out_of_range")

    def test_label_with_line_break_is_refused_because_it_would_corrupt_class_files(self) -> None:
        project = Project.objects.create(
            name="Odd", modality="image", task="Detect", labels=["a\nb"]
        )
        asset = self.make_asset(project=project)
        self.make_box(asset, "a\nb")
        response = self.create_version(project=project)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["problems"][0]["code"], "label_unsupported_characters")

    def test_readiness_reports_without_creating_anything(self) -> None:
        good = self.labelled_asset()
        self.make_suggestion(good, "normal", x="0.7", y="0.7", w="0.1", h="0.1")
        broken = self.labelled_asset()
        default_storage.delete(broken.storage_key)

        response = self.client.get(self.url("/readiness"))

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["ready"])
        self.assertEqual(body["counts"]["accepted_boxes"], 2)
        self.assertEqual(body["counts"]["suggested_boxes"], 1)
        self.assertEqual(body["excluded"]["suggested_boxes"], 1)
        self.assertEqual(body["problems"][0]["code"], "file_missing")
        self.assertTrue(all(isinstance(line, str) and line for line in body["blockers"]))
        self.assertEqual(DatasetVersion.objects.count(), 0)

    def test_readiness_when_everything_is_fine_and_when_nothing_is_accepted(self) -> None:
        empty = self.client.get(self.url("/readiness")).json()
        self.assertFalse(empty["ready"])
        self.assertTrue(any("accepted" in line for line in empty["blockers"]))

        self.labelled_asset()
        ok = self.client.get(self.url("/readiness") + "?seed=3&train=0.5&val=0.25&test=0.25").json()
        self.assertTrue(ok["ready"])
        self.assertEqual(ok["blockers"], [])
        self.assertEqual(ok["split_config"]["seed"], 3)
        self.assertEqual(ok["counts"]["images"], 1)

    def test_readiness_rejects_bad_split_parameters(self) -> None:
        response = self.client.get(self.url("/readiness") + "?train=0.9&val=0.9&test=0.9")
        self.assertEqual(response.status_code, 400)


class SplitTests(DatasetBase):
    def test_assignment_is_deterministic_and_depends_on_seed(self) -> None:
        shas = [hashlib.sha256(str(i).encode()).hexdigest() for i in range(300)]
        config = splits.SplitConfig.default()
        first = [splits.assign_split(s, config) for s in shas]
        self.assertEqual(first, [splits.assign_split(s, config) for s in shas])
        other = splits.SplitConfig(seed=7, train=0.8, val=0.1, test=0.1)
        self.assertNotEqual(first, [splits.assign_split(s, other) for s in shas])

    def test_default_is_80_10_10_and_roughly_honoured(self) -> None:
        config = splits.SplitConfig.default()
        self.assertEqual((config.train, config.val, config.test), (0.8, 0.1, 0.1))
        shas = [hashlib.sha256(str(i).encode()).hexdigest() for i in range(2000)]
        counts = {name: 0 for name in splits.SPLIT_NAMES}
        for sha in shas:
            counts[splits.assign_split(sha, config)] += 1
        self.assertAlmostEqual(counts["train"] / 2000, 0.8, delta=0.04)
        self.assertAlmostEqual(counts["val"] / 2000, 0.1, delta=0.03)
        self.assertAlmostEqual(counts["test"] / 2000, 0.1, delta=0.03)

    def test_zero_fraction_split_stays_empty(self) -> None:
        config = splits.SplitConfig(seed=0, train=0.5, val=0.5, test=0.0)
        shas = [hashlib.sha256(str(i).encode()).hexdigest() for i in range(500)]
        self.assertNotIn("test", {splits.assign_split(s, config) for s in shas})

    def test_small_sets_never_leave_a_requested_split_empty(self) -> None:
        config = splits.SplitConfig.default()
        for size in (3, 5, 15, 30):
            for seed in range(25):
                cfg = splits.SplitConfig(seed, config.train, config.val, config.test)
                shas = [hashlib.sha256(f"{size}-{i}".encode()).hexdigest() for i in range(size)]
                result = splits.assign_splits(shas, cfg)
                self.assertEqual(set(result.values()), {"train", "val", "test"}, (size, seed))
                self.assertEqual(result, splits.assign_splits(reversed(shas), cfg))  # order-independent

    def test_rebalance_keeps_identical_pictures_together_and_respects_zero_splits(self) -> None:
        shas = [hashlib.sha256(str(i).encode()).hexdigest() for i in range(4)]
        result = splits.assign_splits(shas + shas, splits.SplitConfig.default())  # duplicates in the input
        self.assertEqual(set(result), set(shas))
        two = splits.SplitConfig(seed=0, train=0.5, val=0.5, test=0.0)
        self.assertNotIn("test", set(splits.assign_splits(shas, two).values()))
        # fewer pictures than requested splits: nothing is invented, some split stays empty
        self.assertEqual(len(set(splits.assign_splits(shas[:2], splits.SplitConfig.default()).values())) <= 2, True)

    def test_a_15_picture_snapshot_has_a_final_test_group(self) -> None:
        assets = [self.make_asset() for _ in range(15)]
        for asset in assets:
            self.make_box(asset)
        default = splits.SplitConfig.default()

        def plain(seed: int) -> set[str]:
            cfg = splits.SplitConfig(seed, default.train, default.val, default.test)
            return {splits.assign_split(a.sha256, cfg) for a in assets}

        # Precondition: pick a seed where the plain hash rule would leave a group empty.
        seed = next((s for s in range(500) if plain(s) != {"train", "val", "test"}), None)
        self.assertIsNotNone(seed, "no seed leaves a group empty; test cannot discriminate")
        body = self.create_version({"seed": seed}).json()["dataset"]["manifest"]
        counts = body["split_counts"]
        self.assertEqual(sum(counts[name]["images"] for name in ("train", "val", "test")), 15)
        for name in ("train", "val", "test"):
            self.assertGreaterEqual(counts[name]["images"], 1, name)

    def test_three_or_more_distinct_pictures_always_fill_every_requested_group(self) -> None:
        config = splits.SplitConfig.default()
        for size in range(3, 41):
            for seed in range(8):
                cfg = splits.SplitConfig(seed, config.train, config.val, config.test)
                shas = [hashlib.sha256(f"g{size}-{i}".encode()).hexdigest() for i in range(size)]
                result = splits.assign_splits(shas + shas[:2], cfg)  # two exact copies in the input
                counts = {name: list(result.values()).count(name) for name in splits.SPLIT_NAMES}
                self.assertTrue(all(counts.values()), (size, seed, counts))
                self.assertEqual(result, splits.assign_splits(reversed(shas), cfg), (size, seed))

    def test_fewer_than_three_pictures_say_so_instead_of_inventing_a_group(self) -> None:
        for size in (1, 2):
            shas = [hashlib.sha256(f"s{size}-{i}".encode()).hexdigest() for i in range(size)]
            result = splits.assign_splits(shas, splits.SplitConfig.default())
            self.assertLess(len(set(result.values())), 3)
            notes = splits.shortfall_notes(len(shas), splits.SplitConfig.default())
            self.assertEqual(len(notes), 1)
            self.assertIn("at least 3", notes[0])
            self.assertIn(f"{size} different", notes[0])
        self.assertEqual(splits.shortfall_notes(3, splits.SplitConfig.default()), [])
        # a split the person set to 0% is not a shortfall
        self.assertEqual(splits.shortfall_notes(2, splits.SplitConfig(0, 0.5, 0.5, 0.0)), [])

    def test_new_versions_record_the_algorithm_and_the_actual_counts(self) -> None:
        for _ in range(15):
            self.labelled_asset()
        dataset = self.create_version().json()["dataset"]
        self.assertEqual(dataset["split_config"]["split_algorithm"], splits.ALGORITHM)
        self.assertEqual(dataset["manifest"]["split_config"]["split_algorithm"], splits.ALGORITHM)
        self.assertEqual(dataset["split_algorithm"], splits.ALGORITHM)
        counts = dataset["manifest"]["split_counts"]
        self.assertTrue(all(counts[name]["images"] >= 1 for name in splits.SPLIT_NAMES), counts)
        self.assertEqual(sum(counts[name]["images"] for name in splits.SPLIT_NAMES), 15)
        self.assertEqual(dataset["manifest"]["split_notes"], [])

    def test_a_two_picture_snapshot_explains_why_a_group_is_empty(self) -> None:
        for _ in range(2):
            self.labelled_asset()
        manifest = self.create_version().json()["dataset"]["manifest"]
        self.assertEqual(sum(manifest["split_counts"][n]["images"] for n in splits.SPLIT_NAMES), 2)
        self.assertEqual(len(manifest["split_notes"]), 1)
        self.assertIn("at least 3", manifest["split_notes"][0])
        readiness = self.client.get(self.url("/readiness")).json()
        self.assertEqual(readiness["preview"]["split_notes"], manifest["split_notes"])

    def test_empty_groups_get_a_plain_warning_with_the_real_count(self) -> None:
        for _ in range(2):
            self.labelled_asset()
        manifest = self.create_version().json()["dataset"]["manifest"]
        empty = [n for n in splits.SPLIT_NAMES if manifest["split_counts"][n]["images"] == 0]
        self.assertTrue(empty)
        titles = {"train": "Training", "val": "Checking", "test": "Final test"}
        for name in empty:
            self.assertTrue(
                any(w.startswith(f"{titles[name]} has 0 pictures - ") for w in manifest["warnings"]),
                manifest["warnings"],
            )
        if "test" in empty:
            self.assertIn(
                "Final test has 0 pictures - add more pictures to check how well the model does.",
                manifest["warnings"],
            )

    def test_a_split_set_to_zero_percent_is_not_warned_about(self) -> None:
        from .datasets import manifest as manifest_lib

        image = {"asset_id": "a", "sha256": "0" * 64, "split": "train", "boxes": []}
        out = manifest_lib.summarise([image], [], requested={"train": 1.0, "val": 0.0, "test": 0.0})
        self.assertFalse([w for w in out["warnings"] if "0 pictures" in w], out["warnings"])

    def test_versions_made_before_the_algorithm_field_stay_valid(self) -> None:
        from .datasets import manifest as manifest_lib

        # A version row as it was saved before the algorithm field and split_notes existed.
        config = {"seed": 0, "train": 0.8, "val": 0.1, "test": 0.1}
        version = DatasetVersion(project=self.project, version=1, label_names=["damaged"], split_config=config)
        document = manifest_lib.build_manifest(
            dataset_id=version.id, version=1, created_at=version.created_at.isoformat(), created_by=None,
            athena_version="old", project_id=self.project.id, project_name="Line 4", labels=["damaged"],
            split_config=config, excluded={}, images=[],
        )
        document.pop("split_notes")
        version.manifest = document
        version.manifest_sha256 = manifest_lib.sha256_hex(document)
        version.save()
        version.refresh_from_db()
        self.assertEqual(splits.algorithm_of(version.split_config), splits.LEGACY_ALGORITHM)
        self.assertEqual(dataset_service.verify_integrity(version), [])  # checksum semantics unchanged
        shown = self.client.get(self.url(f"/{version.id}")).json()["dataset"]
        self.assertEqual(shown["split_algorithm"], splits.LEGACY_ALGORITHM)
        self.assertNotIn("split_algorithm", shown["split_config"])

    def test_config_validation(self) -> None:
        for bad in (
            {"train": 0.5, "val": 0.1, "test": 0.1},
            {"train": -0.1, "val": 0.6, "test": 0.5},
            {"train": 1.0, "val": 0.0},
            {"train": "0.8", "val": 0.1, "test": 0.1},
            {"train": 0.8, "val": 0.1, "test": 0.1, "extra": 0},
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                splits.SplitConfig.from_payload({"split": bad})
        with self.assertRaises(ValueError):
            splits.SplitConfig.from_payload({"seed": True})
        with self.assertRaises(ValueError):
            splits.SplitConfig.from_payload({"seed": 1.5})

    def test_same_content_always_lands_in_the_same_split_across_versions(self) -> None:
        assets = [self.labelled_asset() for _ in range(25)]
        first = self.create_version().json()["dataset"]["id"]
        second = self.create_version().json()["dataset"]["id"]
        def mapping(version_id):
            return {
                row.asset_id: row.split
                for row in DatasetImage.objects.filter(version_id=version_id)
            }
        self.assertEqual(mapping(first), mapping(second))
        self.assertEqual(len(mapping(first)), len(assets))

    def test_identical_images_never_straddle_splits(self) -> None:
        # 40 distinct pictures, each uploaded 3 times (different asset ids, same sha256)
        groups: dict[str, list[Asset]] = {}
        for index in range(40):
            data = png_bytes((index * 5 % 256, 10, 200 - index))
            copies = [self.make_asset(data=data) for _ in range(3)]
            for copy in copies:
                self.make_box(copy)
            groups[hashlib.sha256(data).hexdigest()] = copies

        response = self.create_version({"seed": 11})

        self.assertEqual(response.status_code, 201)
        split_of = {row.asset_id: row.split for row in DatasetImage.objects.all()}
        for sha, copies in groups.items():
            self.assertEqual(len({split_of[c.id] for c in copies}), 1, sha)
        self.assertGreater(len(set(split_of.values())), 1)  # the test must actually exercise several splits
        duplicates = response.json()["dataset"]["manifest"]["duplicates"]
        self.assertEqual(duplicates["groups"], 40)
        self.assertEqual(duplicates["images_in_groups"], 120)
        self.assertEqual(duplicates["leaks"], 0)
        self.assertEqual(len(duplicates["examples"][0]["asset_ids"]), 3)

    def test_class_balance_is_summarised_per_split(self) -> None:
        for index in range(30):
            asset = self.make_asset()
            self.make_box(asset, "damaged")
            if index % 3 == 0:
                self.make_box(asset, "normal", x="0.6", y="0.6", w="0.1", h="0.1")
        body = self.create_version().json()["dataset"]["manifest"]
        balance = body["class_balance"]
        self.assertEqual(set(balance), {"damaged", "normal"})
        self.assertEqual(sum(balance["damaged"][s] for s in ("train", "val", "test")), 30)
        self.assertEqual(sum(balance["normal"][s] for s in ("train", "val", "test")), 10)
        self.assertEqual(balance["damaged"]["total"], 30)
        self.assertEqual(sum(body["split_counts"][s]["images"] for s in ("train", "val", "test")), 30)

    def test_custom_split_config_is_recorded(self) -> None:
        self.labelled_asset()
        response = self.create_version({"seed": 5, "split": {"train": 0.6, "val": 0.2, "test": 0.2}})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            response.json()["dataset"]["split_config"],
            {"seed": 5, "train": 0.6, "val": 0.2, "test": 0.2, "split_algorithm": splits.ALGORITHM},
        )

    def test_bad_split_config_is_a_400(self) -> None:
        self.labelled_asset()
        response = self.create_version({"split": {"train": 0.9, "val": 0.9, "test": 0.9}})
        self.assertEqual(response.status_code, 400)
        response = self.create_version({"surprise": 1})
        self.assertEqual(response.status_code, 400)


class VersioningTests(DatasetBase):
    def test_versions_increase_per_project_and_list_newest_first(self) -> None:
        self.labelled_asset()
        other = Project.objects.create(name="O", modality="image", task="d", labels=["damaged"])
        self.make_box(self.make_asset(project=other))
        one = self.create_version().json()["dataset"]
        two = self.create_version().json()["dataset"]
        other_one = self.create_version(project=other).json()["dataset"]
        self.assertEqual((one["version"], two["version"], other_one["version"]), (1, 2, 1))
        listing = self.client.get(self.url()).json()["datasets"]
        self.assertEqual([item["version"] for item in listing], [2, 1])
        self.assertNotIn("manifest", listing[0])

    def test_version_number_is_unique_per_project(self) -> None:
        self.labelled_asset()
        version = DatasetVersion.objects.get(id=self.create_version().json()["dataset"]["id"])
        with self.assertRaises(IntegrityError):
            DatasetVersion.objects.create(
                project=self.project, version=version.version, label_names=[], split_config={}, manifest_sha256="0" * 64
            )

    def test_frozen_rows_cannot_be_modified(self) -> None:
        self.labelled_asset()
        version = DatasetVersion.objects.get(id=self.create_version().json()["dataset"]["id"])
        version.box_count = 99
        with self.assertRaises(ValueError):
            version.save()
        with self.assertRaises(ValueError):
            DatasetVersion.objects.filter(pk=version.pk).update(box_count=5)
        image = version.images.get()
        image.split = "test"
        with self.assertRaises(ValueError):
            image.save()
        box = image.boxes.get()
        box.label = "normal"
        with self.assertRaises(ValueError):
            box.save()
        with self.assertRaises(ValueError):
            DatasetBox.objects.filter(pk=box.pk).update(label="normal")
        with self.assertRaises(ValueError):
            version.delete()
        version.refresh_from_db()
        self.assertEqual(version.box_count, 1)

    def test_later_edits_do_not_change_a_frozen_version(self) -> None:
        asset = self.make_asset()
        box = self.make_box(asset, "damaged")
        created = self.create_version().json()["dataset"]
        before = self.client.get(self.url(f"/{created['id']}")).json()["dataset"]

        box.label = "normal"
        box.x = Decimal("0.5")
        box.save()
        self.make_box(asset, "normal", x="0.7", y="0.7", w="0.1", h="0.1")
        self.project.labels = ["renamed"]
        self.project.save()
        Asset.objects.filter(pk=asset.pk).update(sha256="f" * 64, width=999)

        after = self.client.get(self.url(f"/{created['id']}")).json()["dataset"]
        self.assertEqual(before, after)
        self.assertEqual(after["label_names"], ["damaged", "normal"])
        self.assertEqual(after["manifest"]["images"][0]["boxes"][0]["label"], "damaged")
        self.assertEqual(after["manifest"]["images"][0]["boxes"][0]["x"], "0.10000000")

    def test_manifest_hash_matches_recorded_value(self) -> None:
        self.labelled_asset()
        created = self.create_version().json()["dataset"]
        canonical = json.dumps(
            created["manifest"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
        self.assertEqual(hashlib.sha256(canonical).hexdigest(), created["manifest_sha256"])
        self.assertEqual(DatasetVersion.objects.get().manifest_sha256, created["manifest_sha256"])
        self.assertEqual(created["status"], "ready")

    def test_manifest_has_no_emails_or_storage_paths(self) -> None:
        self.user.email = "operator@example.com"
        self.user.save()
        asset = self.make_asset(filename="../../etc/passwd.png")
        self.make_box(asset, created_by=self.user)
        text = self.create_version().content.decode()
        self.assertNotIn("operator@example.com", text)
        self.assertNotIn("operator", text.replace("operator@", ""))  # no username either
        self.assertNotIn(asset.storage_key, text)
        self.assertIn(f"u{self.user.pk}", text)

    def test_deleting_a_project_still_works_after_versioning(self) -> None:
        self.labelled_asset()
        self.create_version()
        self.assertEqual(self.client.delete(f"/api/projects/{self.project.id}").status_code, 200)
        self.assertEqual(DatasetVersion.objects.count(), 0)


class ProvenanceTests(DatasetBase):
    def test_manual_box_created_through_the_api_records_its_author(self) -> None:
        asset = self.make_asset()
        response = self.client.post(
            f"/api/projects/{self.project.id}/assets/{asset.id}/annotations",
            data=json.dumps({"label": "damaged", "x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(BoxAnnotation.objects.get().created_by, self.user)
        # the public annotation payload does not leak the author
        self.assertNotIn("created_by", response.json()["annotation"])

    def test_deleting_the_author_keeps_the_box(self) -> None:
        asset = self.make_asset()
        author = get_user_model().objects.create_user("temp")
        box = self.make_box(asset, created_by=author)
        author.delete()
        box.refresh_from_db()
        self.assertIsNone(box.created_by)

    def test_exports_carry_an_opaque_author_id(self) -> None:
        asset = self.make_asset()
        self.make_box(asset, created_by=self.user)
        version_id = self.create_version().json()["dataset"]["id"]
        archive = self.read_zip(self.export(version_id, "coco"))
        manifest = json.loads(archive.read("manifest.json"))
        box = manifest["dataset"]["images"][0]["boxes"][0]
        self.assertEqual(box["created_by"], f"u{self.user.pk}")
        self.assertNotIn("email", archive.read("manifest.json").decode().lower())


class ExportTests(DatasetBase):
    def populated(self, count: int = 12):
        assets = []
        for index in range(count):
            asset = self.make_asset(size=(40 + index, 30 + index))
            self.make_box(asset, "damaged", x="0.12345678", y="0.00000001", w="0.33333333", h="0.5")
            self.make_box(asset, "normal", x="0.5", y="0.25", w="0.4", h="0.7")
            assets.append(asset)
        return assets

    def assert_close(self, a: float, b: float) -> None:
        self.assertLessEqual(abs(a - b), 1e-6 * max(abs(a), abs(b), 1e-12), (a, b))

    def test_coco_round_trip_matches_the_database(self) -> None:
        assets = self.populated()
        version_id = self.create_version({"seed": 2}).json()["dataset"]["id"]

        response = self.export(version_id, "coco")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.streaming)
        self.assertEqual(response["Content-Type"], "application/zip")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn("-v1-coco.zip", response["Content-Disposition"])
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        archive = self.read_zip(response)
        by_asset = {str(a.id): a for a in assets}
        snapshot = {str(row.asset_id): row for row in DatasetImage.objects.all()}
        seen = 0
        for split in ("train", "val", "test"):
            coco = json.loads(archive.read(f"annotations/instances_{split}.json"))
            self.assertEqual(
                [(c["id"], c["name"]) for c in coco["categories"]], [(1, "damaged"), (2, "normal")]
            )
            self.assertEqual(coco["info"]["dataset_version"], 1)
            self.assertEqual(coco["info"]["project_id"], str(self.project.id))
            self.assertEqual(coco["info"]["project_name"], "Line 4")
            self.assertIn("athena_version", coco["info"])
            self.assertEqual(
                coco["info"]["manifest_sha256"], DatasetVersion.objects.get().manifest_sha256
            )
            images = {img["id"]: img for img in coco["images"]}
            for image in coco["images"]:
                name = image["file_name"]
                self.assertEqual(Path(name).name, name)
                data = archive.read(f"images/{split}/{name}")
                row = next(r for r in snapshot.values() if r.split == split and name.endswith(f"{r.sha256[:12]}.png"))
                self.assertEqual(hashlib.sha256(data).hexdigest(), row.sha256)
                self.assertEqual((image["width"], image["height"]), (row.width, row.height))
            for annotation in coco["annotations"]:
                image = images[annotation["image_id"]]
                row = next(
                    r for r in snapshot.values()
                    if r.split == split and image["file_name"].endswith(f"{r.sha256[:12]}.png")
                )
                label = coco["categories"][annotation["category_id"] - 1]["name"]
                source = by_asset[str(row.asset_id)].annotations.get(label=label)
                w, h = image["width"], image["height"]
                x, y, bw, bh = annotation["bbox"]
                self.assert_close(x, float(source.x) * w)
                self.assert_close(y, float(source.y) * h)
                self.assert_close(bw, float(source.width) * w)
                self.assert_close(bh, float(source.height) * h)
                self.assert_close(annotation["area"], bw * bh)
                self.assertEqual(annotation["iscrowd"], 0)
                seen += 1
        self.assertEqual(seen, 24)

    def test_yolo_round_trip_matches_the_database(self) -> None:
        assets = self.populated()
        version_id = self.create_version({"seed": 2}).json()["dataset"]["id"]

        response = self.export(version_id, "yolo")

        self.assertEqual(response.status_code, 200)
        self.assertIn("-v1-yolo.zip", response["Content-Disposition"])
        archive = self.read_zip(response)
        names = archive.namelist()
        self.assertEqual(archive.read("classes.txt").decode().splitlines(), ["damaged", "normal"])
        self.assertIn("data.yaml", names)
        by_asset = {str(a.id): a for a in assets}
        seen = 0
        for row in DatasetImage.objects.all():
            stem = next(
                n for n in names if n.startswith(f"images/{row.split}/") and row.sha256[:12] in n
            )
            label_path = f"labels/{row.split}/" + Path(stem).stem + ".txt"
            lines = archive.read(label_path).decode().strip().splitlines()
            self.assertEqual(len(lines), 2)
            for line in lines:
                cls, cx, cy, w, h = line.split()
                label = ["damaged", "normal"][int(cls)]
                source = by_asset[str(row.asset_id)].annotations.get(label=label)
                self.assert_close(float(cx), float(source.x + source.width / 2))
                self.assert_close(float(cy), float(source.y + source.height / 2))
                self.assert_close(float(w), float(source.width))
                self.assert_close(float(h), float(source.height))
                seen += 1
        self.assertEqual(seen, 24)

    def test_zip_manifest_lists_every_file_with_a_correct_hash(self) -> None:
        self.populated(6)
        version = DatasetVersion.objects.get(id=self.create_version().json()["dataset"]["id"])
        for fmt in ("coco", "yolo"):
            with self.subTest(fmt=fmt):
                archive = self.read_zip(self.export(version.id, fmt))
                manifest = json.loads(archive.read("manifest.json"))
                self.assertEqual(manifest["manifest_sha256"], version.manifest_sha256)
                self.assertEqual(manifest["export"]["format"], fmt)
                listed = {item["path"]: item for item in manifest["export"]["files"]}
                self.assertEqual(set(listed), set(archive.namelist()) - {"manifest.json"})
                for path, item in listed.items():
                    data = archive.read(path)
                    self.assertEqual(hashlib.sha256(data).hexdigest(), item["sha256"], path)
                    self.assertEqual(len(data), item["bytes"])
                canonical = json.dumps(
                    manifest["dataset"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ).encode()
                self.assertEqual(hashlib.sha256(canonical).hexdigest(), version.manifest_sha256)

    def test_archive_paths_never_contain_user_controlled_components(self) -> None:
        asset = self.make_asset(filename="../../../etc/passwd")
        self.make_box(asset)
        evil = self.make_asset(filename="a\\b/..\\..\\x.png")
        self.make_box(evil)
        version_id = self.create_version().json()["dataset"]["id"]
        for fmt in ("coco", "yolo"):
            archive = self.read_zip(self.export(version_id, fmt))
            for name in archive.namelist():
                self.assertFalse(name.startswith("/"), name)
                self.assertNotIn("..", name.split("/"), name)
                self.assertNotIn("\\", name)
                self.assertNotIn("passwd", name)
                self.assertRegex(name, r"^[A-Za-z0-9_./-]+$")

    def test_export_is_deterministic(self) -> None:
        self.populated(5)
        version_id = self.create_version().json()["dataset"]["id"]
        first = b"".join(self.export(version_id, "coco").streaming_content)
        second = b"".join(self.export(version_id, "coco").streaming_content)
        self.assertEqual(first, second)

    def test_unknown_or_missing_format_is_rejected(self) -> None:
        self.populated(1)
        version_id = self.create_version().json()["dataset"]["id"]
        self.assertEqual(self.export(version_id, "pascal").status_code, 400)
        self.assertEqual(self.client.get(self.url(f"/{version_id}/export")).status_code, 400)
        self.assertEqual(self.client.post(self.url(f"/{version_id}/export?format=coco")).status_code, 405)

    def test_export_refuses_when_an_image_changed_after_the_version_was_made(self) -> None:
        asset = self.labelled_asset()
        version_id = self.create_version().json()["dataset"]["id"]
        Path(default_storage.path(asset.storage_key)).write_bytes(png_bytes((9, 9, 9)))
        response = self.export(version_id, "coco")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["problems"][0]["code"], "hash_mismatch")

    def test_export_refuses_when_an_image_file_vanished_after_the_version_was_made(self) -> None:
        asset = self.labelled_asset()
        version_id = self.create_version().json()["dataset"]["id"]
        default_storage.delete(asset.storage_key)  # lost outside the app (e.g. disk problem)
        response = self.export(version_id, "yolo")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["problems"][0]["code"], "file_missing")

    def test_export_detects_tampered_snapshot_rows(self) -> None:
        self.labelled_asset()
        version_id = self.create_version().json()["dataset"]["id"]
        # bypass the model guard the way a raw SQL edit would
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute("UPDATE projects_datasetbox SET label = 'normal'")
        response = self.export(version_id, "coco")
        self.assertEqual(response.status_code, 409)
        self.assertIn("changed", response.json()["error"])

    @override_settings(DATASET_EXPORT_MAX_BYTES=10)
    def test_export_size_cap(self) -> None:
        self.labelled_asset()
        version_id = self.create_version().json()["dataset"]["id"]
        response = self.export(version_id, "coco")
        self.assertEqual(response.status_code, 413)

    def test_unknown_dataset_id_is_404(self) -> None:
        self.assertEqual(self.export(uuid.uuid4()).status_code, 404)
        self.assertEqual(self.client.get(self.url(f"/{uuid.uuid4()}")).status_code, 404)

    def test_detail_with_trailing_slash(self) -> None:
        self.labelled_asset()
        version_id = self.create_version().json()["dataset"]["id"]
        response = self.client.get(self.url(f"/{version_id}/"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["dataset"]["id"], version_id)
        self.assertEqual(response["Cache-Control"], "no-store")


class SnapshotDeletionTests(DatasetBase):
    def asset_url(self, asset, query="") -> str:
        return f"/api/projects/{self.project.id}/assets/{asset.id}{query}"

    def test_deleting_a_picture_in_a_snapshot_is_refused_with_a_plain_explanation(self) -> None:
        asset = self.labelled_asset()
        self.create_version()
        self.create_version()
        response = self.client.delete(self.asset_url(asset))
        self.assertEqual(response.status_code, 409)
        body = response.json()
        self.assertEqual(sorted(body), ["error", "hint", "snapshots"])
        self.assertEqual(body["snapshots"], [1, 2])
        self.assertIn("remove_from_snapshots=1", body["hint"])
        self.assertIn("v1, v2", body["error"])
        self.assertTrue(Asset.objects.filter(id=asset.id).exists())
        self.assertTrue(default_storage.exists(asset.storage_key))
        self.assertFalse(any(v.is_incomplete for v in DatasetVersion.objects.all()))

    def test_only_the_exact_flag_value_one_allows_the_delete(self) -> None:
        asset = self.labelled_asset()
        self.create_version()
        for query in ("?remove_from_snapshots=0", "?remove_from_snapshots=true", "?remove_from_snapshots="):
            with self.subTest(query=query):
                self.assertEqual(self.client.delete(self.asset_url(asset, query)).status_code, 409)
        self.assertTrue(Asset.objects.filter(id=asset.id).exists())

    def test_a_picture_in_no_snapshot_deletes_as_before(self) -> None:
        kept = self.labelled_asset()
        self.create_version()
        loose = self.make_asset()
        response = self.client.delete(self.asset_url(loose))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["deleted"], True)
        self.assertFalse(default_storage.exists(loose.storage_key))
        self.assertFalse(DatasetVersion.objects.get().is_incomplete)
        self.assertTrue(default_storage.exists(kept.storage_key))

    def test_forced_delete_removes_the_picture_and_marks_every_affected_snapshot(self) -> None:
        doomed = self.labelled_asset(filename="doomed.png")
        self.labelled_asset()
        self.create_version()
        self.create_version()
        later = self.make_asset()
        self.make_box(later)
        response = self.client.delete(self.asset_url(doomed, "?remove_from_snapshots=1"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"deleted": True, "snapshots_marked_incomplete": [1, 2]})
        self.assertFalse(Asset.objects.filter(id=doomed.id).exists())
        self.assertFalse(default_storage.exists(doomed.storage_key))
        for version in DatasetVersion.objects.all():
            self.assertTrue(version.is_incomplete)
            self.assertIn("doomed.png", version.incomplete_reason)
            self.assertIn(f"u{self.user.pk}", version.incomplete_reason)

    def test_a_snapshot_without_the_picture_stays_complete(self) -> None:
        doomed = self.labelled_asset()
        self.create_version()  # v1 contains it
        doomed.refresh_from_db()
        self.client.delete(self.asset_url(doomed, "?remove_from_snapshots=1"))
        survivor = self.labelled_asset()
        v2 = self.create_version().json()["dataset"]
        self.assertFalse(v2["is_incomplete"])
        self.assertTrue(self.export(v2["id"]).status_code == 200)
        self.assertTrue(survivor.id)

    def test_incomplete_snapshot_refuses_export_but_manifest_stays_verifiable(self) -> None:
        doomed = self.labelled_asset()
        self.labelled_asset()
        created = self.create_version().json()["dataset"]
        self.client.delete(self.asset_url(doomed, "?remove_from_snapshots=1"))
        for fmt in ("coco", "yolo"):
            response = self.export(created["id"], fmt)
            self.assertEqual(response.status_code, 409)
            body = response.json()
            self.assertTrue(body["incomplete"])
            self.assertEqual(body["missing_pictures"], 1)
            self.assertTrue(body["manifest_verified"])
            self.assertIn("1 of 2 picture(s)", body["error"])
            self.assertIn("reason", body)
        version = DatasetVersion.objects.get()
        self.assertEqual(version.manifest_sha256, created["manifest_sha256"])
        self.assertEqual(len(dataset_service.verify_integrity(version)), 2)  # no ValueError
        detail = self.client.get(self.url(f"/{created['id']}")).json()["dataset"]
        self.assertTrue(detail["is_incomplete"])
        self.assertEqual(detail["missing_pictures"], 1)
        self.assertEqual(detail["manifest_sha256"], created["manifest_sha256"])
        listed = self.client.get(self.url()).json()["datasets"][0]
        self.assertTrue(listed["is_incomplete"])

    def test_incomplete_flag_accumulates_and_cannot_be_cleared(self) -> None:
        first = self.labelled_asset(filename="first.png")
        second = self.labelled_asset(filename="second.png")
        self.create_version()
        self.client.delete(self.asset_url(first, "?remove_from_snapshots=1"))
        self.client.delete(self.asset_url(second, "?remove_from_snapshots=1"))
        reason = DatasetVersion.objects.get().incomplete_reason
        self.assertIn("first.png", reason)
        self.assertIn("second.png", reason)
        self.assertEqual(self.export(DatasetVersion.objects.get().id).json()["missing_pictures"], 2)

    def test_incomplete_reason_is_the_only_field_that_can_change(self) -> None:
        asset = self.labelled_asset()
        self.create_version()
        version = DatasetVersion.objects.get()

        def snapshot():
            row = DatasetVersion.objects.filter(pk=version.pk).values().get()
            row.pop("incomplete_reason")
            return row

        before = snapshot()
        DatasetVersion.mark_incomplete(version.id, "note one")
        DatasetVersion.mark_incomplete(version.id, "note two")
        self.assertEqual(snapshot(), before)
        self.assertEqual(DatasetVersion.objects.get().incomplete_reason, "note one\nnote two")
        self.assertEqual(DatasetVersion._meta.model.MUTABLE_FIELDS, ("incomplete_reason",))

        # every other write path stays closed, including for incomplete_reason itself
        version.refresh_from_db()
        version.incomplete_reason = ""
        with self.assertRaises(ValueError):
            version.save()
        for field in [f.name for f in DatasetVersion._meta.concrete_fields if f.name != "id"]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                DatasetVersion.objects.filter(pk=version.pk).update(**{field: getattr(version, field)})
        with self.assertRaises(ValueError):
            DatasetVersion.objects.filter(pk=version.pk).delete()
        self.assertEqual(DatasetVersion.objects.get().incomplete_reason, "note one\nnote two")
        self.assertTrue(asset.id)

    def test_deleting_the_project_still_works_with_incomplete_snapshots(self) -> None:
        asset = self.labelled_asset()
        self.create_version()
        self.client.delete(self.asset_url(asset, "?remove_from_snapshots=1"))
        self.assertEqual(self.client.delete(f"/api/projects/{self.project.id}").status_code, 200)
        self.assertEqual(DatasetVersion.objects.count(), 0)

    def test_other_projects_pictures_are_not_reported_as_snapshot_members(self) -> None:
        other = Project.objects.create(name="Other", modality="image", task="Detect", labels=["damaged"])
        self.labelled_asset()
        self.create_version()
        stranger = self.make_asset(project=other)
        response = self.client.delete(f"/api/projects/{other.id}/assets/{stranger.id}")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(DatasetVersion.objects.get().is_incomplete)


class ReadinessCacheTests(DatasetBase):
    def setUp(self) -> None:
        super().setUp()
        validation.clear_verification_cache()
        self.addCleanup(validation.clear_verification_cache)

    def readiness(self):
        return self.client.get(self.url("/readiness")).json()

    def hasher(self):
        return mock.patch.object(validation, "storage_sha256", wraps=validation.storage_sha256)

    def test_repeat_readiness_calls_stat_instead_of_hashing(self) -> None:
        for _ in range(3):
            self.labelled_asset()
        with self.hasher() as hasher:
            first = self.readiness()
            self.assertEqual(hasher.call_count, 3)
            second = self.readiness()
            third = self.readiness()
            self.assertEqual(hasher.call_count, 3)  # unchanged: served from the cache
        self.assertTrue(first["ready"] and second["ready"] and third["ready"])

    def test_changed_size_forces_a_rehash_and_is_reported(self) -> None:
        asset = self.labelled_asset()
        self.assertTrue(self.readiness()["ready"])
        Path(default_storage.path(asset.storage_key)).write_bytes(png_bytes((9, 9, 9), (50, 50)))
        with self.hasher() as hasher:
            result = self.readiness()
        self.assertEqual(hasher.call_count, 1)
        self.assertFalse(result["ready"])
        self.assertEqual(result["problems"][0]["code"], "hash_mismatch")

    def test_changed_mtime_forces_a_rehash(self) -> None:
        asset = self.labelled_asset()
        self.readiness()
        path = default_storage.path(asset.storage_key)
        stat = os.stat(path)
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
        with self.hasher() as hasher:
            result = self.readiness()
        self.assertEqual(hasher.call_count, 1)
        self.assertTrue(result["ready"])  # same bytes: re-hashed and still fine

    def test_deleted_file_is_reported_even_when_cached(self) -> None:
        asset = self.labelled_asset()
        self.readiness()
        default_storage.delete(asset.storage_key)
        self.assertEqual(self.readiness()["problems"][0]["code"], "file_missing")

    def tamper_same_size_same_mtime(self, asset) -> None:
        path = default_storage.path(asset.storage_key)
        stat = os.stat(path)
        data = bytearray(Path(path).read_bytes())
        data[-5] ^= 0xFF
        Path(path).write_bytes(bytes(data))
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        after = os.stat(path)
        self.assertEqual((after.st_size, after.st_mtime_ns), (stat.st_size, stat.st_mtime_ns))

    def test_creation_always_hashes_and_catches_same_size_same_mtime_tampering(self) -> None:
        asset = self.labelled_asset()
        self.assertTrue(self.readiness()["ready"])  # warms the cache
        self.tamper_same_size_same_mtime(asset)
        # documented limitation: the advisory readiness view is fooled by this specific trick...
        self.assertTrue(self.readiness()["ready"])
        # ...but nothing can be frozen from it:
        with self.hasher() as hasher:
            response = self.create_version()
        self.assertEqual(hasher.call_count, 1)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["problems"][0]["code"], "hash_mismatch")
        self.assertEqual(DatasetVersion.objects.count(), 0)

    def test_export_always_hashes_and_catches_same_size_same_mtime_tampering(self) -> None:
        asset = self.labelled_asset()
        version_id = self.create_version().json()["dataset"]["id"]
        self.readiness()
        self.tamper_same_size_same_mtime(asset)
        self.assertTrue(self.readiness()["ready"])  # cache still trusts the stat key
        with self.hasher() as hasher:
            response = self.export(version_id)
        self.assertEqual(hasher.call_count, 1)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["problems"][0]["code"], "hash_mismatch")

    def test_creation_and_export_never_read_or_fill_the_cache(self) -> None:
        self.labelled_asset()
        validation.clear_verification_cache()
        version_id = self.create_version().json()["dataset"]["id"]
        self.assertEqual(len(validation._VERIFIED), 0)
        self.export(version_id)
        self.assertEqual(len(validation._VERIFIED), 0)
        self.readiness()
        self.assertEqual(len(validation._VERIFIED), 1)
        with self.hasher() as hasher:
            self.create_version()
            self.export(version_id)
        self.assertEqual(hasher.call_count, 2)

    def test_cache_is_bounded(self) -> None:
        with mock.patch.object(validation, "CACHE_MAX_ENTRIES", 2):
            for index in range(5):
                validation._cache_put((f"k{index}", 1, 1), "x")
        self.assertEqual(len(validation._VERIFIED), 2)


class ExportAuditTests(DatasetBase):
    def make_version(self) -> str:
        self.labelled_asset()
        return self.create_version().json()["dataset"]["id"]

    def exports_url(self, version_id, project=None) -> str:
        return self.url(f"/{version_id}/exports", project=project)

    def test_successful_export_is_recorded_with_size_and_user(self) -> None:
        version_id = self.make_version()
        response = self.export(version_id, "yolo")
        content = b"".join(response.streaming_content)
        event = ExportEvent.objects.get()
        self.assertTrue(event.success)
        self.assertEqual((event.format, event.status_code, event.bytes_sent), ("yolo", 200, len(content)))
        self.assertEqual(event.user_id, self.user.pk)
        self.assertEqual(str(event.version_id), version_id)

    def test_refused_exports_are_recorded_as_failures(self) -> None:
        version_id = self.make_version()
        self.assertEqual(self.export(version_id, "pdf").status_code, 400)
        asset = Asset.objects.get()
        Path(default_storage.path(asset.storage_key)).write_bytes(png_bytes((9, 9, 9)))
        self.assertEqual(self.export(version_id, "coco").status_code, 409)
        events = list(ExportEvent.objects.order_by("id"))
        self.assertEqual([e.status_code for e in events], [400, 409])
        self.assertFalse(any(e.success for e in events))
        self.assertEqual(events[0].format, "pdf")
        self.assertIn("cannot be exported", events[1].error)
        self.assertEqual({e.bytes_sent for e in events}, {0})

    def test_incomplete_version_attempts_are_recorded(self) -> None:
        version_id = self.make_version()
        asset = Asset.objects.get()
        self.client.delete(f"/api/projects/{self.project.id}/assets/{asset.id}?remove_from_snapshots=1")
        self.assertEqual(self.export(version_id).status_code, 409)
        event = ExportEvent.objects.get()
        self.assertFalse(event.success)
        self.assertIn("incomplete", event.error)

    def test_an_abandoned_download_is_recorded_as_failed(self) -> None:
        version_id = self.make_version()
        response = self.export(version_id)
        next(iter(response.streaming_content))
        response.close()
        event = ExportEvent.objects.get()
        self.assertFalse(event.success)
        self.assertIn("stopped", event.error)

    def test_unknown_version_writes_no_event(self) -> None:
        self.export(uuid.uuid4())
        self.assertEqual(ExportEvent.objects.count(), 0)

    def test_list_endpoint_is_newest_first_and_scoped_to_the_version(self) -> None:
        version_id = self.make_version()
        for fmt in ("coco", "yolo"):
            b"".join(self.export(version_id, fmt).streaming_content)
        self.export(version_id, "nope")
        response = self.client.get(self.exports_url(version_id))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        body = response.json()
        self.assertEqual(body["total"], 3)
        self.assertEqual([e["format"] for e in body["exports"]], ["nope", "yolo", "coco"])
        first = body["exports"][2]
        self.assertEqual(set(first), {"id", "format", "bytes", "success", "status", "error", "user", "created_at"})
        self.assertEqual(first["user"], f"u{self.user.pk}")
        self.assertTrue(first["success"])
        self.assertGreater(first["bytes"], 0)
        other = self.client.get(self.exports_url(self.create_version().json()["dataset"]["id"])).json()
        self.assertEqual(other["exports"], [])

    def test_list_endpoint_requires_sign_in_and_matching_project(self) -> None:
        version_id = self.make_version()
        self.assertEqual(Client().get(self.exports_url(version_id)).status_code, 401)
        other = Project.objects.create(name="Other", modality="image", task="Detect", labels=["a"])
        self.assertEqual(self.client.get(self.exports_url(version_id, project=other)).status_code, 404)
        self.assertEqual(self.client.post(self.exports_url(version_id)).status_code, 405)

    def test_events_are_append_only_and_survive_user_deletion(self) -> None:
        version_id = self.make_version()
        b"".join(self.export(version_id).streaming_content)
        event = ExportEvent.objects.get()
        with self.assertRaises(ValueError):
            event.success = False
            event.save()
        with self.assertRaises(ValueError):
            event.delete()
        with self.assertRaises(ValueError):
            ExportEvent.objects.all().update(success=False)
        with self.assertRaises(ValueError):
            ExportEvent.objects.all().delete()
        self.user.delete()
        event = ExportEvent.objects.get()
        self.assertIsNone(event.user_id)
        self.assertIsNotNone(event.user_id_snapshot)


class SharedHelperTests(DatasetBase):
    """One public fingerprint and one accepted-boxes query, used by every caller (hygiene follow-ups H1, H2)."""

    def old_fingerprint(self) -> str:
        # The formula as it was written before the helper existed: pinned here so the refactor cannot change the value.
        rows = (
            BoxAnnotation.objects.filter(asset__project=self.project, status=BoxAnnotation.STATUS_ACCEPTED)
            .order_by("id")
            .values_list("id", "label", "x", "y", "width", "height", "updated_at", "asset__sha256")
        )
        digest = hashlib.sha256()
        for row in rows:
            digest.update(repr(row).encode())
        return digest.hexdigest()

    def test_the_public_fingerprint_and_the_old_private_name_are_one_function_with_one_value(self) -> None:
        self.assertIs(dataset_service._fingerprint, dataset_service.content_fingerprint)
        self.labelled_asset()
        self.make_suggestion(self.make_asset())
        value = dataset_service.content_fingerprint(self.project)
        self.assertEqual(value, dataset_service._fingerprint(self.project))
        self.assertEqual(value, self.old_fingerprint())

    def test_lifecycle_uses_the_public_name_not_the_private_one(self) -> None:
        from .training import lifecycle

        source = Path(lifecycle.__file__).read_text()
        self.assertIn("dataset_service.content_fingerprint(", source)
        self.assertNotIn("dataset_service._fingerprint", source)

    def test_accepted_boxes_is_the_old_query_ordered_by_id_with_the_picture_loaded(self) -> None:
        first = self.labelled_asset()
        self.make_suggestion(self.make_asset())  # never counts
        second = self.labelled_asset()
        by_id = sorted([first, second], key=lambda asset: asset.annotations.get().id)  # box ids are random UUIDs
        boxes = dataset_service.accepted_boxes(self.project)
        with self.assertNumQueries(1):
            found = list(boxes)
            self.assertEqual([box.asset_id for box in found], [asset.id for asset in by_id])
            self.assertEqual([box.asset.sha256 for box in found], [asset.sha256 for asset in by_id])  # no extra query
        self.assertEqual([box.id for box in found], sorted(box.id for box in found))
        other = Project.objects.create(name="Other", modality="image", task="Object detection", labels=["damaged"])
        self.assertEqual(list(dataset_service.accepted_boxes(other)), [])

    def test_the_four_callers_see_the_same_boxes_after_add_accept_and_delete(self) -> None:
        from .datasets import health
        from .training import lifecycle

        def seen():
            """The accepted-box ids each caller works from, plus how many times the shared helper was asked."""
            with mock.patch.object(dataset_service, "accepted_boxes", wraps=dataset_service.accepted_boxes) as shared:
                dataset_service.content_fingerprint(self.project)
                analysis = dataset_service.analyse(self.project, splits.SplitConfig.default())
                health.build(self.project)
            self.assertEqual(shared.call_count, 4)  # fingerprint 1, analyse 2 (its query and its fingerprint), health 1
            return analysis

        def expected_ids():
            return sorted(
                BoxAnnotation.objects.filter(
                    asset__project=self.project, status=BoxAnnotation.STATUS_ACCEPTED
                ).values_list("id", flat=True)
            )

        def check(label):
            analysis = seen()
            self.assertEqual([box.id for box in analysis.accepted], expected_ids(), label)
            self.assertEqual([box.id for box in dataset_service.accepted_boxes(self.project)], expected_ids(), label)
            self.assertEqual(analysis.fingerprint, self.old_fingerprint(), label)
            return analysis

        empty = check("empty")
        self.assertEqual(empty.accepted, [])
        asset = self.labelled_asset()
        added = check("added")
        self.assertEqual(len(added.accepted), 1)
        suggestion = self.make_suggestion(self.make_asset())
        self.assertEqual(len(check("suggestion only").accepted), 1)
        suggestion.status = BoxAnnotation.STATUS_ACCEPTED
        suggestion.save()
        accepted = check("accepted")
        self.assertEqual(len(accepted.accepted), 2)
        self.assertNotEqual(accepted.fingerprint, added.fingerprint)
        # the fourth caller: the "is the latest snapshot current?" comparison reads the analysis the others share
        version = dataset_service.create_version(accepted, self.user)
        self.assertTrue(lifecycle.snapshot_is_current(self.project, version, accepted))
        asset.annotations.all().delete()
        deleted = check("deleted")
        self.assertEqual(len(deleted.accepted), 1)
        self.assertFalse(lifecycle.snapshot_is_current(self.project, version, deleted))

    def test_health_has_no_private_copy_of_the_status_constants(self) -> None:
        from .datasets import health

        self.assertIs(health.ACCEPTED, dataset_service.ACCEPTED)
        self.assertIs(health.SUGGESTED, dataset_service.SUGGESTED)
        self.assertNotIn("ACCEPTED = BoxAnnotation", Path(health.__file__).read_text())
        self.assertNotIn("SUGGESTED = BoxAnnotation", Path(health.__file__).read_text())


class FileCheckBudgetTests(DatasetBase):
    """``analyse(file_check=...)``: how many picture files are re-checked (hygiene follow-up H3)."""

    def setUp(self) -> None:
        super().setUp()
        validation.clear_verification_cache()
        self.addCleanup(validation.clear_verification_cache)

    def hasher(self):
        return mock.patch.object(validation, "storage_sha256", wraps=validation.storage_sha256)

    def ticking(self, step: float = 1.0):
        """A clock that moves ``step`` seconds every time it is read, starting at 0."""
        state = {"now": -step}

        def clock() -> float:
            state["now"] += step
            return state["now"]

        return mock.patch.object(dataset_service, "_clock", clock)

    def analyse(self, **kw):
        return dataset_service.analyse(self.project, splits.SplitConfig.default(), **kw)

    def test_full_is_the_default_and_checks_every_file(self) -> None:
        for _ in range(3):
            self.labelled_asset()
        with self.hasher() as hasher:
            analysis = self.analyse()
        self.assertEqual(hasher.call_count, 3)
        self.assertTrue(analysis.files_rechecked)
        self.assertEqual((analysis.files_checked, analysis.files_not_rechecked), (3, 0))

    def test_skip_checks_no_file_and_says_so(self) -> None:
        asset = self.labelled_asset()
        self.labelled_asset()
        default_storage.delete(asset.storage_key)
        with self.hasher() as hasher:
            analysis = self.analyse(file_check="skip")
        hasher.assert_not_called()
        self.assertFalse(analysis.files_rechecked)
        self.assertEqual((analysis.files_checked, analysis.files_not_rechecked), (0, 2))
        self.assertEqual(analysis.problems, [])  # the missing file is NOT reported: it was not looked at

    def test_a_budget_stops_re_checking_when_the_clock_runs_out(self) -> None:
        for _ in range(5):
            self.labelled_asset()
        with self.ticking(1.0), self.hasher() as hasher:
            analysis = self.analyse(file_check=2.5)
        self.assertEqual(hasher.call_count, 2)  # read at 0 (start); files go at 1 and 2; at 3 the 2.5 s are used up
        self.assertFalse(analysis.files_rechecked)
        self.assertEqual((analysis.files_checked, analysis.files_not_rechecked), (2, 3))

    def test_a_budget_that_is_never_used_up_checks_everything(self) -> None:
        for _ in range(3):
            self.labelled_asset()
        with self.ticking(0.001), self.hasher() as hasher:
            analysis = self.analyse(file_check=30)
        self.assertEqual(hasher.call_count, 3)
        self.assertTrue(analysis.files_rechecked)

    def test_files_checked_before_the_budget_ran_out_still_report_their_problems(self) -> None:
        first, second = self.labelled_asset(), self.labelled_asset()
        default_storage.delete(first.storage_key)
        default_storage.delete(second.storage_key)
        with self.ticking(1.0):
            analysis = self.analyse(file_check=1.5)
        self.assertEqual([p.code for p in analysis.problems], ["file_missing"])  # one file looked at, one not
        self.assertEqual(analysis.files_not_rechecked, 1)

    def test_other_checks_are_unchanged_when_files_are_skipped(self) -> None:
        asset = self.labelled_asset()
        BoxAnnotation.objects.filter(asset=asset).update(label="not-a-label")
        analysis = self.analyse(file_check="skip")
        self.assertEqual([p.code for p in analysis.problems], ["label_not_in_project"])

    def test_nothing_accepted_means_nothing_was_left_unchecked(self) -> None:
        analysis = self.analyse(file_check="skip")
        self.assertTrue(analysis.files_rechecked)
        self.assertEqual(analysis.files_not_rechecked, 0)

    def test_an_unknown_mode_is_refused_plainly(self) -> None:
        for bad in ("fast", "", None, True, -1, float("nan"), [1]):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self.analyse(file_check=bad)


class PartialAnalysisCannotFreezeTests(DatasetBase):
    """A version is only ever frozen from an analysis that looked at every picture file (W10-FX3 / B2)."""

    REFUSAL = (
        "This check did not look at every picture file (2 of 2 were left unchecked), so a dataset version cannot be "
        "frozen from it. Run the full check and try again. Nothing was created."
    )

    def analyse(self, **kw):
        return dataset_service.analyse(self.project, splits.SplitConfig.default(), **kw)

    def setUp(self) -> None:
        super().setUp()
        self.first, self.second = self.labelled_asset(), self.labelled_asset()

    def test_complete_is_true_only_when_every_file_was_looked_at(self) -> None:
        self.assertIs(self.analyse().complete, True)
        self.assertIs(self.analyse(file_check="skip").complete, False)
        self.assertIs(self.analyse(file_check=0).complete, False)

    def test_ready_keeps_its_meaning_for_the_preview(self) -> None:
        default_storage.delete(self.first.storage_key)
        skipped = self.analyse(file_check="skip")
        self.assertTrue(skipped.ready)  # the preview's answer: nothing it looked at is wrong...
        self.assertFalse(skipped.complete)  # ...and this says it did not look at everything
        full = self.analyse()
        self.assertFalse(full.ready)
        self.assertTrue(full.complete)

    def test_a_partial_analysis_with_a_missing_file_cannot_be_frozen(self) -> None:
        default_storage.delete(self.first.storage_key)
        partial = self.analyse(file_check="skip")
        self.assertTrue(partial.ready)
        with self.assertRaises(dataset_service.IncompleteAnalysis) as caught:
            dataset_service.create_version(partial, self.user)
        self.assertEqual(str(caught.exception), self.REFUSAL)
        self.assertEqual(DatasetVersion.objects.count(), 0)
        self.assertEqual(DatasetImage.objects.count(), 0)

    def test_a_partial_analysis_with_nothing_wrong_is_refused_too(self) -> None:
        with self.assertRaises(dataset_service.IncompleteAnalysis):
            dataset_service.create_version(self.analyse(file_check="skip"), self.user)
        self.assertEqual(DatasetVersion.objects.count(), 0)

    def test_it_is_a_plain_value_error_not_a_version_conflict(self) -> None:
        self.assertTrue(issubclass(dataset_service.IncompleteAnalysis, ValueError))
        self.assertFalse(issubclass(dataset_service.IncompleteAnalysis, dataset_service.VersionConflict))

    def test_a_full_analysis_still_freezes(self) -> None:
        version = dataset_service.create_version(self.analyse(), self.user)
        self.assertEqual((version.version, version.image_count), (1, 2))
