"""Generic (non-image) frozen dataset versions: entries, manifests, splits, exports.

Driven by a fake text modality registered only inside these tests (the Wave 0 pattern from
``tests_modal_foundation``): ``.txt`` files annotated with ``clip_labels``. Covers readiness,
version creation, immutability of the frozen rows, suggested-annotation exclusion, split
determinism, manifest checksum stability, missing-file problems, the deletion guard, the
dataset list API shape, and the runner's ``export_entries`` / ``entry_ground_truth`` helpers.
"""

from __future__ import annotations

import hashlib
import io
import json
import tempfile
import uuid
import zipfile
from pathlib import Path

from django.core.files.storage import default_storage
from django.db.models import QuerySet

from .datasets import generic as generic_service
from .datasets import manifest as manifest_lib
from .datasets import splits
from .models import Asset, AssetAnnotation, DatasetEntry, DatasetVersion, Project, TrainingRun
from .modalities import register_plugin, unregister_plugin
from .modalities.base import ModalityPlugin
from .tests import SignedInMixin
from .tests_datasets import DatasetBase


class FakeClipPlugin(ModalityPlugin):
    """A minimal non-image modality: text files, one accepted label per clip."""

    modality = "text"
    asset_noun = "text file"
    annotation_kind = "clip_labels"

    def validate_upload(self, upload):
        if upload.size <= 0:
            raise ValueError("Text files cannot be empty.")
        content = upload.read()
        upload.seek(0)
        return {
            "filename": (upload.name or "clip.txt").rsplit("/", 1)[-1],
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
            "extension": "txt",
            "content_type": "text/plain",
        }

    def validate_annotation(self, payload, project):
        if not isinstance(payload, dict) or set(payload) - {"label"}:
            raise ValueError("A clip label needs exactly one label.")
        label = payload.get("label")
        if not isinstance(label, str) or label not in project.labels:
            raise ValueError("Choose a label defined in this project.")
        return {"label": label}


class GenericDatasetBase(DatasetBase):
    """A signed-in user, a media sandbox, and the fake text plugin registered per test."""

    def setUp(self) -> None:
        super().setUp()
        register_plugin(FakeClipPlugin, replace=True)
        self.addCleanup(unregister_plugin, "text")
        self.text_project = Project.objects.create(
            name="Sound clips", modality="text", task="Tell sounds apart", labels=["healthy", "faulty"]
        )

    # -- fixtures -----------------------------------------------------------------
    def make_text_asset(self, project=None, *, content: bytes | None = None, filename="clip.txt") -> Asset:
        project = project or self.text_project
        if content is None:
            content = f"clip {uuid.uuid4()}\n".encode()
        asset_id = uuid.uuid4()
        key = default_storage.save(f"projects/{project.id}/assets/{asset_id}.txt", io.BytesIO(content))
        return Asset.objects.create(
            id=asset_id,
            project=project,
            filename=filename,
            storage_key=key,
            content_type="text/plain",
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            width=0,
            height=0,
        )

    def make_clip_label(self, asset, label="healthy", **extra) -> AssetAnnotation:
        return AssetAnnotation.objects.create(
            asset=asset, kind="clip_labels", data={"label": label}, **extra
        )

    def make_suggestion(self, asset, label="faulty") -> AssetAnnotation:
        return self.make_clip_label(
            asset,
            label,
            origin=AssetAnnotation.ORIGIN_MODEL,
            status=AssetAnnotation.STATUS_SUGGESTED,
            confidence=0.8,
            model_name="fake",
        )

    def labelled_asset(self, content: bytes | None = None, **kwargs) -> Asset:
        asset = self.make_text_asset(content=content, **kwargs)
        self.make_clip_label(asset)
        return asset

    # -- helpers ------------------------------------------------------------------
    def generic_url(self, suffix: str = "", project=None) -> str:
        return f"/api/projects/{(project or self.text_project).id}/datasets{suffix}"

    def create_generic_version(self, payload: dict | None = None, project=None):
        return self.client.post(
            self.generic_url(project=project),
            data=json.dumps(payload or {}),
            content_type="application/json",
        )

    def readiness(self, project=None, query=""):
        return self.client.get(self.generic_url("/readiness", project=project) + query)


class ReadinessTests(GenericDatasetBase):
    def test_empty_project_is_not_ready_and_says_why(self) -> None:
        body = self.readiness().json()
        self.assertFalse(body["ready"])
        self.assertTrue(body["blockers"])
        self.assertEqual(body["counts"]["assets_total"], 0)
        self.assertEqual(body["preview"], {})

    def test_unlabeled_asset_is_reported_until_labeled(self) -> None:
        asset = self.make_text_asset()
        body = self.readiness().json()
        self.assertFalse(body["ready"])
        self.assertEqual(body["problem_count"], 1)
        self.assertEqual(body["problems"][0]["code"], "asset_without_accepted")
        self.assertEqual(body["problems"][0]["asset_id"], str(asset.id))

        self.make_clip_label(asset)
        body = self.readiness().json()
        self.assertTrue(body["ready"], body)
        self.assertFalse(body["blockers"])
        self.assertEqual(body["counts"]["assets"], 1)

    def test_wrong_annotation_kind_is_reported(self) -> None:
        asset = self.make_text_asset()
        AssetAnnotation.objects.create(asset=asset, kind="transcripts", data={"label": "healthy"})
        body = self.readiness().json()
        self.assertFalse(body["ready"])
        self.assertEqual(body["problems"][0]["code"], "annotation_kind_mismatch")

    def test_missing_file_is_reported(self) -> None:
        asset = self.labelled_asset()
        default_storage.delete(asset.storage_key)
        body = self.readiness().json()
        self.assertFalse(body["ready"])
        self.assertEqual(body["problems"][0]["code"], "file_missing")

    def test_changed_file_is_reported(self) -> None:
        asset = self.labelled_asset()
        with default_storage.open(asset.storage_key, "wb") as handle:
            handle.write(b"tampered")
        body = self.readiness().json()
        self.assertFalse(body["ready"])
        self.assertEqual(body["problems"][0]["code"], "hash_mismatch")

    def test_suggested_annotations_do_not_make_the_project_ready(self) -> None:
        asset = self.make_text_asset()
        self.make_suggestion(asset)
        body = self.readiness().json()
        self.assertFalse(body["ready"])
        self.assertEqual(body["counts"]["suggested_annotations"], 1)
        self.assertEqual(body["counts"]["assets"], 0)
        self.assertEqual(body["excluded"]["reason"], generic_service.EXCLUDED_REASON)

    def test_split_config_comes_from_the_query_like_images(self) -> None:
        self.labelled_asset()
        body = self.readiness(query="?seed=3&train=0.6&val=0.2&test=0.2").json()
        self.assertEqual(
            body["split_config"],
            {"seed": 3, "train": 0.6, "val": 0.2, "test": 0.2, "split_algorithm": splits.ALGORITHM},
        )

    def test_a_project_without_a_plugin_keeps_the_old_refusal(self) -> None:
        speech = Project.objects.create(name="Raw", modality="speech", task="x", labels=["a"])
        self.assertEqual(self.client.get(self.generic_url(project=speech)).status_code, 404)
        self.assertEqual(self.readiness(project=speech).status_code, 404)

    def test_health_says_not_available_for_this_data_type(self) -> None:
        self.labelled_asset()
        response = self.client.get(f"/api/projects/{self.text_project.id}/datasets/health")
        self.assertEqual(response.status_code, 400)
        self.assertIn("not available for this data type", response.json()["error"])


class CreateVersionTests(GenericDatasetBase):
    def test_create_freezes_one_entry_per_asset(self) -> None:
        first = self.labelled_asset(content=b"alpha clip")
        second = self.labelled_asset(content=b"beta clip")
        response = self.create_generic_version()
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()["dataset"]
        self.assertEqual(body["version"], 1)
        self.assertEqual(body["counts"]["entry_count"], 2)
        self.assertEqual(body["counts"]["annotation_count"], 2)
        self.assertEqual(body["label_names"], ["healthy", "faulty"])
        self.assertEqual(DatasetVersion.objects.get().image_count, 0)
        self.assertEqual(DatasetVersion.objects.get().box_count, 0)
        entries = list(DatasetEntry.objects.all())
        self.assertEqual(len(entries), 2)
        by_asset = {str(row.asset_uuid): row for row in entries}
        self.assertEqual(by_asset[str(first.id)].annotation, {"label": "healthy"})
        self.assertEqual(by_asset[str(first.id)].label, "healthy")
        self.assertEqual(by_asset[str(first.id)].filename, first.filename)
        self.assertEqual(by_asset[str(second.id)].sha256, second.sha256)

    def test_creating_with_problems_is_refused_and_writes_nothing(self) -> None:
        self.labelled_asset()
        self.make_text_asset()  # a second file with no accepted label
        response = self.create_generic_version()
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertIn("Nothing was created", body["error"])
        self.assertTrue(body["blockers"])
        self.assertEqual(DatasetVersion.objects.count(), 0)

    def test_creating_with_no_accepted_labels_gives_the_plain_message(self) -> None:
        response = self.create_generic_version()
        self.assertEqual(response.status_code, 400)
        self.assertIn("no accepted labels", response.json()["error"])

    def test_frozen_rows_are_immutable(self) -> None:
        self.labelled_asset()
        self.create_generic_version()
        row = DatasetEntry.objects.get()
        row.label = "faulty"
        with self.assertRaises(ValueError):
            row.save()
        with self.assertRaises(ValueError):
            DatasetEntry.objects.update(label="faulty")
        with self.assertRaises(ValueError):
            row.delete()

    def test_suggested_annotations_are_excluded_from_the_version(self) -> None:
        asset = self.labelled_asset()
        self.make_suggestion(asset)
        response = self.create_generic_version()
        self.assertEqual(response.status_code, 201)
        body = response.json()["dataset"]
        self.assertEqual(body["excluded"]["suggested_annotations"], 1)
        self.assertEqual(body["excluded"]["assets_without_accepted"], 0)
        self.assertEqual(body["counts"]["entry_count"], 1)
        # And the frozen annotation is the accepted one, not the suggestion.
        row = DatasetEntry.objects.get()
        self.assertEqual(row.annotation, {"label": "healthy"})

    def test_splits_are_deterministic_and_follow_the_content(self) -> None:
        # Same content twice: both land in the same split, in this and every later version.
        content = b"identical clip"
        first_copy = self.labelled_asset(content=content)
        twin = self.labelled_asset(content=content)
        for index in range(4):
            self.labelled_asset(content=f"clip {index}".encode())
        first = self.create_generic_version().json()["dataset"]
        again = self.create_generic_version()
        self.assertEqual(again.status_code, 201)
        splits_of = {str(row.asset_uuid): row.split for row in DatasetEntry.objects.filter(version__version=1)}
        self.assertEqual(len(splits_of), 6)
        self.assertTrue(all(split in splits.SPLIT_NAMES for split in splits_of.values()))
        # Byte-identical twins share a split.
        self.assertEqual(splits_of[str(first_copy.id)], splits_of[str(twin.id)])
        # Deterministic: the same project re-frozen assigns the same splits.
        second = {str(row.asset_uuid): row.split for row in DatasetEntry.objects.filter(version__version=2)}
        self.assertEqual(splits_of, second)
        self.assertTrue(first["manifest_sha256"] != "")

    def test_the_split_rule_is_the_image_one_keyed_on_sha256(self) -> None:
        asset = self.labelled_asset(content=b"known content")
        config = splits.SplitConfig.default()
        self.create_generic_version()
        row = DatasetEntry.objects.get()
        self.assertEqual(row.split, splits.assign_split(asset.sha256, config))

    def test_manifest_checksum_is_stable_and_matches_the_canonical_json(self) -> None:
        self.labelled_asset(content=b"stable")
        created = self.create_generic_version().json()["dataset"]
        version = DatasetVersion.objects.get()
        self.assertEqual(
            hashlib.sha256(manifest_lib.canonical_json(version.manifest)).hexdigest(),
            created["manifest_sha256"],
        )
        self.assertEqual(version.manifest_sha256, created["manifest_sha256"])
        # The manifest carries the entries, never "images".
        self.assertIn("entries", version.manifest)
        self.assertNotIn("images", version.manifest)
        self.assertEqual(version.manifest["counts"]["entry_count"], 1)
        self.assertEqual(version.manifest["entries"][0]["annotation"], {"label": "healthy"})

    def test_verify_integrity_raises_when_a_frozen_row_is_tampered_with(self) -> None:
        self.labelled_asset()
        self.create_generic_version()
        plain = QuerySet(model=DatasetEntry)
        plain.filter(pk=DatasetEntry.objects.get().pk).update(label="faulty")
        with self.assertRaises(ValueError) as raised:
            generic_service.verify_integrity(DatasetVersion.objects.get())
        self.assertIn("checksum", str(raised.exception))

    def test_missing_file_after_freezing_is_an_integrity_problem(self) -> None:
        asset = self.labelled_asset()
        self.create_generic_version()
        default_storage.delete(asset.storage_key)
        version = DatasetVersion.objects.get()
        problems = generic_service.file_problems(version)
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0].code, "file_missing")
        # verify_integrity (rows vs manifest) still passes: only the file is gone.
        self.assertEqual(len(generic_service.verify_integrity(version)), 1)

    def test_second_version_gets_the_next_number(self) -> None:
        self.labelled_asset()
        self.create_generic_version()
        created = self.create_generic_version().json()["dataset"]
        self.assertEqual(created["version"], 2)
        self.assertEqual(DatasetVersion.objects.count(), 2)


class DeletionGuardTests(GenericDatasetBase):
    def asset_url(self, asset, query="") -> str:
        return f"/api/projects/{self.text_project.id}/assets/{asset.id}{query}"

    def test_deleting_a_frozen_file_is_refused_with_a_plain_explanation(self) -> None:
        asset = self.labelled_asset()
        self.create_generic_version()
        response = self.client.delete(self.asset_url(asset))
        self.assertEqual(response.status_code, 409)
        body = response.json()
        self.assertEqual(body["snapshots"], [1])
        self.assertIn("remove_from_snapshots=1", body["hint"])
        self.assertIn("v1", body["error"])
        self.assertTrue(Asset.objects.filter(id=asset.id).exists())
        self.assertFalse(DatasetVersion.objects.get().is_incomplete)

    def test_forced_delete_marks_the_version_incomplete(self) -> None:
        asset = self.labelled_asset(filename="gone.txt")
        self.labelled_asset()
        self.create_generic_version()
        response = self.client.delete(self.asset_url(asset, "?remove_from_snapshots=1"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"deleted": True, "snapshots_marked_incomplete": [1]})
        version = DatasetVersion.objects.get()
        self.assertTrue(version.is_incomplete)
        self.assertIn("gone.txt", version.incomplete_reason)

    def test_an_unfrozen_file_deletes_as_before(self) -> None:
        frozen = self.labelled_asset()
        self.create_generic_version()
        loose = self.make_text_asset()
        response = self.client.delete(self.asset_url(loose))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(default_storage.exists(loose.storage_key))
        self.assertFalse(DatasetVersion.objects.get().is_incomplete)
        self.assertTrue(default_storage.exists(frozen.storage_key))


class ApiShapeTests(GenericDatasetBase):
    def test_the_dataset_list_has_the_generic_shape(self) -> None:
        self.labelled_asset()
        self.create_generic_version()
        listing = self.client.get(self.generic_url()).json()["datasets"]
        self.assertEqual(len(listing), 1)
        body = listing[0]
        self.assertEqual(
            set(body),
            {
                "id", "version", "project_id", "status", "created_at", "created_by", "label_names",
                "counts", "excluded", "split_config", "split_algorithm", "manifest_sha256",
                "export_formats", "is_incomplete", "incomplete_reason",
            },
        )
        self.assertEqual(body["counts"]["entry_count"], 1)
        self.assertEqual(body["export_formats"], [generic_service.EXPORT_FORMAT])
        detail = self.client.get(self.generic_url(f"/{body['id']}")).json()["dataset"]
        self.assertIn("manifest", detail)
        self.assertIn("entries", detail["manifest"])

    def test_readiness_shape(self) -> None:
        self.labelled_asset()
        body = self.readiness().json()
        self.assertEqual(
            set(body),
            {
                "ready", "blockers", "counts", "excluded", "split_config", "label_names",
                "preview", "problems", "problem_count", "problems_truncated",
            },
        )
        self.assertEqual(body["counts"], {"assets_total": 1, "assets": 1, "accepted_annotations": 1,
                                          "suggested_annotations": 0})
        self.assertEqual(
            sum(counts["entries"] for counts in body["preview"]["split_counts"].values()), 1
        )
        self.assertEqual(body["preview"]["label_counts"], {"healthy": 1, "faulty": 0})

    def test_image_projects_still_get_the_image_shape(self) -> None:
        asset = self.make_asset()
        self.make_box(asset)
        listing = self.client.get(self.url()).json()["datasets"]
        self.assertEqual(listing, [])
        created = self.create_version().json()["dataset"]
        self.assertEqual(created["counts"], {"images": 1, "boxes": 1})
        detail = self.client.get(self.url(f"/{created['id']}")).json()["dataset"]
        self.assertIn("images", detail["manifest"])
        self.assertEqual(DatasetEntry.objects.count(), 0)


class ExportTests(GenericDatasetBase):
    def export(self, version_id, project=None, query=""):
        return self.client.get(self.generic_url(f"/{version_id}/export", project=project) + query)

    def test_export_streams_a_manifest_plus_the_raw_files(self) -> None:
        asset = self.labelled_asset(content=b"exported clip")
        created = self.create_generic_version().json()["dataset"]
        response = self.export(created["id"])
        self.assertEqual(response.status_code, 200)
        archive = zipfile.ZipFile(io.BytesIO(b"".join(response.streaming_content)))
        names = archive.namelist()
        self.assertIn("classes.txt", names)
        self.assertIn("manifest.json", names)
        entry_names = [name for name in names if name.startswith("entries/")]
        self.assertEqual(len(entry_names), 1)
        self.assertIn(asset.sha256[:12], entry_names[0])
        self.assertEqual(archive.read(entry_names[0]), b"exported clip")
        self.assertEqual(archive.read("classes.txt").decode().splitlines(), ["healthy", "faulty"])
        wrapper = json.loads(archive.read("manifest.json"))
        self.assertEqual(wrapper["manifest_sha256"], created["manifest_sha256"])
        self.assertEqual(wrapper["dataset"]["entries"][0]["annotation"], {"label": "healthy"})

    def test_only_the_manifest_format_is_accepted(self) -> None:
        self.labelled_asset()
        created = self.create_generic_version().json()["dataset"]
        self.assertEqual(self.export(created["id"], query="?format=coco").status_code, 400)
        self.assertEqual(self.export(created["id"], query="?format=manifest").status_code, 200)

    def test_export_refuses_when_a_file_is_missing(self) -> None:
        asset = self.labelled_asset()
        created = self.create_generic_version().json()["dataset"]
        default_storage.delete(asset.storage_key)
        response = self.export(created["id"])
        self.assertEqual(response.status_code, 409)
        self.assertIn("missing or changed on disk", response.json()["error"])

    def test_export_refuses_an_incomplete_version(self) -> None:
        asset = self.labelled_asset()
        self.labelled_asset()
        created = self.create_generic_version().json()["dataset"]
        self.client.delete(
            f"/api/projects/{self.text_project.id}/assets/{asset.id}?remove_from_snapshots=1"
        )
        response = self.export(created["id"])
        self.assertEqual(response.status_code, 409)
        body = response.json()
        self.assertTrue(body["incomplete"])
        self.assertEqual(body["missing_files"], 1)

    def test_the_export_audit_trail_is_written(self) -> None:
        from .models import ExportEvent

        self.labelled_asset()
        created = self.create_generic_version().json()["dataset"]
        response = self.export(created["id"])
        self.assertEqual(response.status_code, 200)
        b"".join(response.streaming_content)  # finish the download so the event is written
        event = ExportEvent.objects.get()
        self.assertTrue(event.success)
        self.assertEqual(event.format, generic_service.EXPORT_FORMAT)
        listing = self.client.get(self.generic_url(f"/{created['id']}/exports")).json()
        self.assertEqual(listing["total"], 1)


class RunnerHelpersTests(GenericDatasetBase):
    """The exact API the Wave 1 training backends code against."""

    def make_run(self) -> TrainingRun:
        self.labelled_asset(content=b"first clip")
        self.labelled_asset(content=b"second clip")
        self.labelled_asset(content=b"third clip")
        version = None
        for seed in range(50):
            config = splits.SplitConfig(seed, 0.6, 0.2, 0.2)
            analysis = generic_service.analyse(self.text_project, config)
            if analysis.accepted and analysis.ready:
                candidate = generic_service.create_version(analysis, self.user)
                if candidate.manifest["split_counts"]["train"]["entries"] > 0:
                    version = candidate
                    break
        self.assertIsNotNone(version)
        return TrainingRun.objects.create(
            project=self.text_project,
            dataset_version=version,
            dataset_manifest_sha256=version.manifest_sha256,
            backend="fake-audio",
            config={},
            config_sha256="0" * 64,
        )

    def test_export_entries_materializes_files_with_sha_verification(self) -> None:
        from .training import runner

        run = self.make_run()
        with tempfile.TemporaryDirectory() as scratch:
            dest = Path(scratch) / "dataset"
            written = runner.export_entries(run, dest)
            version = run.dataset_version
            self.assertEqual(len(written), version.entries.count())
            for path in written:
                self.assertTrue(path.exists())
                self.assertEqual(
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                    next(row.sha256 for row in version.entries.all() if row.sha256[:12] in path.name),
                )
                self.assertTrue(str(path.relative_to(dest)).startswith("entries/"))

    def test_export_entries_refuses_an_image_version(self) -> None:
        from .datasets import service as dataset_service
        from .training import runner

        asset = self.make_asset()
        self.make_box(asset)
        analysis = dataset_service.analyse(self.project, splits.SplitConfig.default())
        version = dataset_service.create_version(analysis, self.user)
        run = TrainingRun.objects.create(
            project=self.project, dataset_version=version,
            dataset_manifest_sha256=version.manifest_sha256, backend="fake",
            config={}, config_sha256="0" * 64,
        )
        with tempfile.TemporaryDirectory() as scratch:
            with self.assertRaises(ValueError) as raised:
                runner.export_entries(run, Path(scratch))
        self.assertIn("no frozen entries", str(raised.exception))

    def test_export_entries_refuses_a_missing_file(self) -> None:
        from .training import runner

        run = self.make_run()
        row = run.dataset_version.entries.first()
        default_storage.delete(row.storage_key)
        with tempfile.TemporaryDirectory() as scratch:
            with self.assertRaises(ValueError) as raised:
                runner.export_entries(run, Path(scratch))
        self.assertIn("missing or changed on disk", str(raised.exception))

    def test_entry_ground_truth_pairs_with_export_entries_by_position(self) -> None:
        from .training import runner

        run = self.make_run()
        ground_truth = runner.entry_ground_truth(run)
        with tempfile.TemporaryDirectory() as scratch:
            written = runner.export_entries(run, Path(scratch))
            self.assertEqual(len(ground_truth), len(written))
            for item, path in zip(ground_truth, written):
                self.assertEqual(
                    set(item), {"split", "label", "annotation", "asset_id", "filename", "sha256"}
                )
                self.assertIn(item["split"], splits.SPLIT_NAMES)
                self.assertEqual(item["label"], "healthy")
                self.assertEqual(item["annotation"], {"label": "healthy"})
                self.assertEqual(item["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
