"""Data-health check: ``GET /api/projects/<id>/datasets/health``.

Everything here uses generated pictures (synthetic scenes of random rectangles and ellipses). Nothing
says how the check behaves on real scanner imagery; see the "what is not shown" notes in the
docstrings of the rotated/cropped tests.
"""

from __future__ import annotations

import hashlib
import io
import os
import random
import time
from unittest import mock

from django.core.files.storage import default_storage
from django.test import Client, SimpleTestCase, override_settings
from PIL import Image, ImageChops, ImageDraw

from . import tests_datasets
from .datasets import health, service, splits
from .models import Asset, BoxAnnotation, DatasetBox, DatasetImage, DatasetVersion, ExportEvent, Project
from .tests import grant

SIZE = (160, 128)


# -- generated pictures --------------------------------------------------------------------------
def scene(seed: int, size: tuple[int, int] = SIZE) -> Image.Image:
    """A repeatable synthetic scene: a vertical gradient plus random rectangles and ellipses."""
    rng = random.Random(seed)
    width, height = size
    base = tuple(rng.randrange(40, 215) for _ in range(3))
    image = Image.new("RGB", size, base)
    draw = ImageDraw.Draw(image)
    for y in range(height):
        shade = int(60 * y / height)
        draw.line([(0, y), (width, y)], fill=tuple(min(255, c + shade - 30) for c in base))
    for _ in range(9):
        x0, y0 = rng.randrange(0, width - 20), rng.randrange(0, height - 20)
        x1, y1 = x0 + rng.randrange(12, width // 2), y0 + rng.randrange(12, height // 2)
        colour = tuple(rng.randrange(0, 256) for _ in range(3))
        (draw.rectangle if rng.random() < 0.5 else draw.ellipse)([x0, y0, x1, y1], fill=colour)
    return image


def encode(image: Image.Image, fmt: str = "PNG", **options) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **options)
    return buffer.getvalue()


def noisy(image: Image.Image, sigma: float = 6.0) -> Image.Image:
    """Add grey-level noise to every channel."""
    noise = Image.effect_noise(image.size, sigma)  # L, centred on 128
    rgb = Image.merge("RGB", (noise, noise, noise))
    return ImageChops.add(image, rgb, scale=1, offset=-128)


class HealthBase(tests_datasets.DatasetBase):
    LABELS = ["Dent", "Scratch", "Rust"]

    def health_url(self, project=None, query: str = "") -> str:
        return self.url("/health", project=project) + query

    def health(self, query: str = "", client=None, project=None) -> dict:
        response = (client or self.client).get(self.health_url(project, query))
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def picture(self, seed: int, *boxes: str, fmt: str = "PNG", **options) -> Asset:
        """Store ``scene(seed)`` as a picture and give it one accepted box per label named."""
        data = encode(scene(seed), fmt, **options)
        asset = self.make_asset(data=data, size=SIZE)
        for label in boxes:
            self.make_box(asset, label)
        return asset

    def store(self, image: Image.Image, *boxes: str, fmt: str = "PNG", **options) -> Asset:
        asset = self.make_asset(data=encode(image, fmt, **options), size=image.size)
        for label in boxes:
            self.make_box(asset, label)
        return asset

    @staticmethod
    def by_label(body: dict) -> dict:
        return {row["label"]: row for row in body["labels"]}


# -- the response contract -------------------------------------------------------------------------
class ContractTests(HealthBase):
    def test_the_response_has_exactly_the_documented_blocks(self) -> None:
        self.picture(1, "Dent")
        body = self.health()
        self.assertEqual(
            set(body),
            {
                "labels", "pictures", "held_out", "near_duplicates", "ready_to_teach", "reasons", "blockers", "advice",
                "thresholds", "priority_notes", "headline",
            },
        )
        self.assertEqual(set(body["pictures"]), {"total", "with_boxes", "without_boxes", "needs_review_boxes"})
        self.assertEqual(set(body["held_out"]) >= {"would_have_test_pictures", "expected_counts", "message"}, True)
        self.assertEqual(set(body["held_out"]["expected_counts"]), {"train", "val", "test"})
        near = body["near_duplicates"]
        self.assertEqual((near["method"], near["hamming_threshold"]), ("dhash-9x8", health.HAMMING_THRESHOLD))
        for key in ("groups", "pictures_involved", "message", "identical_groups", "checked", "not_checked"):
            self.assertIn(key, near)
        for row in body["labels"]:
            self.assertEqual(
                set(row) >= {"label", "accepted_boxes", "pictures", "verdict", "message"}, True, row
            )
            self.assertIn(row["verdict"], ("enough", "few", "none"))

    def test_the_thresholds_are_echoed_by_name(self) -> None:
        self.picture(1, "Dent")
        thresholds = self.health()["thresholds"]
        for name in (
            "min_train_boxes_enough",
            "min_test_boxes_readable",
            "min_test_boxes_floor",
            "min_test_pictures",
            "hamming_threshold",
            "max_mean_grey_difference",
            "max_pictures_hashed",
        ):
            self.assertIn(name, thresholds)
        self.assertEqual(thresholds["min_train_boxes_enough"], health.MIN_TRAIN_BOXES_ENOUGH)
        self.assertEqual(thresholds["min_test_boxes_readable"], health.MIN_TEST_BOXES_READABLE)

    def test_the_test_side_numbers_are_the_ones_the_results_page_uses(self) -> None:
        """One source: training/evaluation.py. health and report re-export them under their own names."""
        from .training import evaluation, report

        self.assertEqual(health.MIN_TEST_BOXES_FLOOR, evaluation.MIN_BOXES_PER_LABEL)
        self.assertEqual(health.MIN_TEST_PICTURES, evaluation.MIN_IMAGES_FOR_CONFIDENCE)
        self.assertEqual(health.MIN_TEST_BOXES_READABLE, evaluation.MIN_BOXES_PER_LABEL_FOR_VERDICT)
        self.assertEqual(report.MIN_TEST_BOXES_PER_LABEL_FOR_VERDICT, evaluation.MIN_BOXES_PER_LABEL_FOR_VERDICT)

    def test_no_test_side_number_is_written_a_second_time(self) -> None:
        import re
        from pathlib import Path

        here = Path(__file__).resolve().parent
        for name, names in (
            ("datasets/health.py", ("MIN_TEST_BOXES_READABLE", "MIN_TEST_BOXES_FLOOR", "MIN_TEST_PICTURES")),
            ("training/report.py", ("MIN_TEST_BOXES_PER_LABEL_FOR_VERDICT",)),
        ):
            text = (here / name).read_text()
            for constant in names:
                self.assertIsNone(re.search(rf"^{constant} = \d+", text, re.M), f"{name} defines {constant} as a literal")
        # the learning-side judgement stays its own named number
        self.assertRegex((here / "datasets/health.py").read_text(), r"(?m)^MIN_TRAIN_BOXES_ENOUGH = 30")

    def test_the_numbers_can_be_read_without_numpy(self) -> None:
        """health and report import evaluation.py for its numbers; the base install has no numpy."""
        import subprocess
        import sys
        from pathlib import Path

        code = (
            "import sys; sys.modules['numpy'] = None\n"
            "import django; django.setup()\n"
            "from projects.training import evaluation, report\n"
            "from projects.datasets import health\n"
            "print(evaluation.MIN_BOXES_PER_LABEL_FOR_VERDICT)\n"
        )
        env = {**os.environ, "DJANGO_SETTINGS_MODULE": os.environ.get("DJANGO_SETTINGS_MODULE", "athena_web.settings")}
        done = subprocess.run(
            [sys.executable, "-c", code], cwd=Path(__file__).resolve().parent.parent, env=env, capture_output=True, text=True
        )
        self.assertEqual(done.returncode, 0, done.stderr[-800:])

    def test_the_wording_of_the_module_is_accurate(self) -> None:
        import inspect

        self.assertNotIn("nothing is cached", health.__doc__)
        self.assertNotIn("task brief", inspect.getsource(health))


# -- access ----------------------------------------------------------------------------------------
class AccessTests(HealthBase):
    def test_a_viewer_can_read_it(self) -> None:
        self.picture(1, "Dent")
        viewer = get_client("viewer")
        self.assertEqual(viewer.get(self.health_url()).status_code, 200)

    def test_every_role_and_an_unassigned_user_can_read_it(self) -> None:
        for role in ("labeler", "trainer", "approver"):
            self.assertEqual(get_client(role).get(self.health_url()).status_code, 200, role)

    def test_an_anonymous_request_is_refused_like_the_sibling_endpoints(self) -> None:
        anonymous = Client()
        sibling = anonymous.get(self.url("/readiness"))
        mine = anonymous.get(self.health_url())
        self.assertEqual(sibling.status_code, 401)
        self.assertEqual(mine.status_code, sibling.status_code)
        self.assertNotIn("labels", mine.json())

    def test_only_get_is_allowed(self) -> None:
        for method in ("post", "put", "patch", "delete"):
            response = getattr(self.client, method)(self.health_url())
            self.assertEqual(response.status_code, 405, method)

    def test_the_view_declares_its_role_for_the_route_walkers(self) -> None:
        from .dataset_views import project_dataset_health

        self.assertEqual(project_dataset_health.athena_access, {"GET": "view"})

    def test_unknown_and_non_image_projects_are_404_like_the_siblings(self) -> None:
        import uuid

        self.assertEqual(self.client.get(f"/api/projects/{uuid.uuid4()}/datasets/health").status_code, 404)
        text = Project.objects.create(name="Notes", modality="text", task="Classify", labels=["a"])
        self.assertEqual(self.client.get(self.health_url(text)).status_code, 404)

    def test_a_bad_query_is_a_400_with_a_plain_message(self) -> None:
        response = self.client.get(self.health_url(query="?seed=abc"))
        self.assertEqual(response.status_code, 400)
        self.assertIn("seed", response.json()["error"])

    def test_the_answer_is_not_cached(self) -> None:
        self.assertEqual(self.client.get(self.health_url())["Cache-Control"], "no-store")


def get_client(role: str) -> Client:
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.create_user(f"{role}-user")
    grant(user, role)
    client = Client()
    client.force_login(user)
    return client


# -- nothing is written ----------------------------------------------------------------------------
class ReadOnlyTests(HealthBase):
    def snapshot_state(self):
        files = sorted(
            os.path.join(root, name) for root, _dirs, names in os.walk(self.media.name) for name in names
        )
        counts = tuple(
            model.objects.count()
            for model in (Project, Asset, BoxAnnotation, DatasetVersion, DatasetImage, DatasetBox, ExportEvent)
        )
        stats = [(path, os.stat(path).st_mtime_ns, os.stat(path).st_size) for path in files]
        return counts, stats

    def test_a_check_writes_no_rows_and_no_files(self) -> None:
        base = scene(5)
        self.store(base, "Dent")
        self.store(noisy(base), "Scratch", fmt="JPEG", quality=70)
        for seed in range(30, 36):
            self.picture(seed, "Rust")
        self.make_suggestion(self.make_asset())
        before = self.snapshot_state()
        queries = []
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as captured:
            self.health()
            self.health("?seed=3&train=0.6&val=0.2&test=0.2")
        queries = [q["sql"] for q in captured if not q["sql"].lstrip().upper().startswith(("SELECT", "SAVEPOINT", "RELEASE"))]
        self.assertEqual(queries, [])
        self.assertEqual(self.snapshot_state(), before)


# -- per-label counts ------------------------------------------------------------------------------
class LabelCountTests(HealthBase):
    ALL_TRAIN = "?train=1&val=0&test=0"  # keep every picture in the training group so counts are exact

    def test_zero_three_and_forty_boxes_give_none_few_enough(self) -> None:
        for seed in range(40):
            self.picture(100 + seed, "Rust")
        for seed in range(3):
            self.picture(200 + seed, "Scratch")
        body = self.health(self.ALL_TRAIN)
        rows = self.by_label(body)
        self.assertEqual([row["label"] for row in body["labels"]], ["Dent", "Scratch", "Rust"])
        self.assertEqual((rows["Dent"]["accepted_boxes"], rows["Dent"]["pictures"], rows["Dent"]["verdict"]), (0, 0, "none"))
        self.assertEqual((rows["Scratch"]["accepted_boxes"], rows["Scratch"]["pictures"], rows["Scratch"]["verdict"]), (3, 3, "few"))
        self.assertEqual((rows["Rust"]["accepted_boxes"], rows["Rust"]["pictures"], rows["Rust"]["verdict"]), (40, 40, "enough"))
        self.assertIn("Dent", rows["Dent"]["message"])
        self.assertIn("only 3", rows["Scratch"]["message"])
        self.assertIn(str(health.MIN_TRAIN_BOXES_ENOUGH), rows["Scratch"]["message"])

    def test_the_boundary_is_exactly_the_named_constant(self) -> None:
        limit = health.MIN_TRAIN_BOXES_ENOUGH
        for seed in range(limit - 1):
            self.picture(300 + seed, "Dent")
        for seed in range(limit):
            self.picture(400 + seed, "Rust")
        rows = self.by_label(self.health(self.ALL_TRAIN))
        self.assertEqual(rows["Dent"]["verdict"], "few")
        self.assertEqual(rows["Rust"]["verdict"], "enough")

    def test_two_boxes_of_one_label_on_one_picture_count_as_two_boxes_and_one_picture(self) -> None:
        self.picture(1, "Dent", "Dent")
        row = self.by_label(self.health(self.ALL_TRAIN))["Dent"]
        self.assertEqual((row["accepted_boxes"], row["pictures"]), (2, 1))

    def test_needs_review_boxes_are_reported_and_never_counted(self) -> None:
        asset = self.picture(1, "Dent")
        other = self.make_asset(data=encode(scene(2)), size=SIZE)
        for _ in range(5):
            self.make_suggestion(other, "Scratch")
        self.make_suggestion(asset, "Dent")
        body = self.health(self.ALL_TRAIN)
        rows = self.by_label(body)
        self.assertEqual(rows["Dent"]["accepted_boxes"], 1)
        self.assertEqual((rows["Scratch"]["accepted_boxes"], rows["Scratch"]["verdict"]), (0, "none"))
        self.assertEqual(body["pictures"], {"total": 2, "with_boxes": 1, "without_boxes": 1, "needs_review_boxes": 6})
        self.assertTrue(any("Needs review" in line for line in body["advice"]))

    def test_a_label_missing_from_the_project_list_is_shown_and_blocks(self) -> None:
        self.picture(1, "Dent", "Dnet")
        body = self.health(self.ALL_TRAIN)
        self.assertIn("Dnet", self.by_label(body))
        self.assertFalse(body["ready_to_teach"])
        self.assertTrue(any("label list" in line for line in body["blockers"]))

    def test_the_numbers_match_a_real_snapshot_of_the_same_work(self) -> None:
        for seed in range(25):
            self.picture(500 + seed, "Dent", "Rust") if seed % 3 == 0 else self.picture(500 + seed, "Scratch")
        body = self.health()
        created = self.create_version()
        self.assertEqual(created.status_code, 201, created.content)
        self.assertEqual(created.json()["dataset"]["counts"]["images"], body["pictures"]["with_boxes"])
        preview = service.preview(service.analyse(self.project, splits.SplitConfig.default()))
        for name in ("train", "val", "test"):
            self.assertEqual(body["held_out"]["expected_counts"][name], preview["split_counts"][name]["images"], name)
        rows = self.by_label(body)
        for label, balance in preview["class_balance"].items():
            self.assertEqual(rows[label]["accepted_boxes"], balance["total"], label)
            self.assertEqual(rows[label]["expected_boxes"], {k: balance[k] for k in ("train", "val", "test")}, label)


# -- held-out group --------------------------------------------------------------------------------
class HeldOutTests(HealthBase):
    def test_one_or_two_pictures_leave_nothing_for_the_final_test(self) -> None:
        seed = next(  # a lone picture lands in whichever group its hash falls in; pick one that is not the test group
            n for n in range(1, 50)
            if splits.assign_split(hashlib.sha256(encode(scene(n))).hexdigest(), splits.SplitConfig.default()) != "test"
        )
        self.picture(seed, "Dent")
        held = self.health()["held_out"]
        self.assertFalse(held["would_have_test_pictures"])
        self.assertEqual(held["expected_counts"]["test"], 0)
        self.assertIn("No pictures would be held back", held["message"])

    def test_three_different_pictures_are_enough_for_a_final_test_group(self) -> None:
        for seed in range(3):
            self.picture(10 + seed, "Dent")
        held = self.health()["held_out"]
        self.assertTrue(held["would_have_test_pictures"])
        self.assertEqual(sum(held["expected_counts"].values()), 3)
        self.assertGreaterEqual(held["expected_counts"]["test"], 1)
        self.assertIn("fewer than", held["message"])  # still thin: 30 pictures are suggested

    def test_a_split_that_keeps_nothing_for_the_test_says_so(self) -> None:
        for seed in range(12):
            self.picture(20 + seed, "Dent")
        held = self.health("?train=0.9&val=0.1&test=0")["held_out"]
        self.assertFalse(held["would_have_test_pictures"])
        self.assertIn("split", held["message"].lower())

    def test_the_per_label_held_back_notes(self) -> None:
        for seed in range(40):
            self.picture(30 + seed, "Dent")
        body = self.health("?train=0.5&val=0&test=0.5")
        rows = self.by_label(body)
        self.assertEqual(rows["Rust"]["held_out_verdict"], "none")
        self.assertIn("No 'Rust'", rows["Rust"]["held_out_message"])
        test_boxes = rows["Dent"]["expected_boxes"]["test"]
        self.assertGreater(test_boxes, 0)
        self.assertEqual(
            rows["Dent"]["held_out_verdict"], "enough" if test_boxes >= health.MIN_TEST_BOXES_READABLE else "few"
        )

    def test_the_default_split_is_the_one_a_snapshot_uses(self) -> None:
        for seed in range(15):
            self.picture(60 + seed, "Dent")
        held = self.health()["held_out"]["expected_counts"]
        actual = self.create_version().json()["dataset"]
        self.assertEqual(sum(held.values()), actual["counts"]["images"])
        shas = [a.sha256 for a in self.project.assets.all()]
        expected = splits.assign_splits(shas, splits.SplitConfig.default())
        for name in ("train", "val", "test"):
            self.assertEqual(held[name], sum(1 for sha in shas if expected[sha] == name))


# -- overall readiness -----------------------------------------------------------------------------
class ReadinessTests(HealthBase):
    def test_a_project_with_no_pictures_is_not_ready_and_everything_is_zero(self) -> None:
        body = self.health()
        self.assertFalse(body["ready_to_teach"])
        self.assertEqual(body["pictures"], {"total": 0, "with_boxes": 0, "without_boxes": 0, "needs_review_boxes": 0})
        self.assertEqual([row["verdict"] for row in body["labels"]], ["none", "none", "none"])
        self.assertEqual(body["near_duplicates"]["groups"], [])
        self.assertFalse(body["held_out"]["would_have_test_pictures"])
        self.assertTrue(body["reasons"] and body["reasons"][0] == body["blockers"][0])
        self.assertIn("accepted", body["blockers"][0])

    def test_pictures_with_no_accepted_box_do_not_make_a_project_ready(self) -> None:
        asset = self.make_asset(data=encode(scene(1)), size=SIZE)
        self.make_suggestion(asset)
        body = self.health()
        self.assertFalse(body["ready_to_teach"])
        self.assertEqual(body["pictures"]["with_boxes"], 0)

    def test_a_defined_label_with_no_boxes_is_a_warning_and_not_a_blocker(self) -> None:
        for seed in range(6):
            self.picture(70 + seed, "Dent")
        body = self.health()
        self.assertTrue(body["ready_to_teach"])
        self.assertEqual(body["blockers"], [])
        self.assertTrue(any("Scratch" in line for line in body["advice"]))
        self.assertEqual(body["reasons"], body["blockers"] + body["advice"])

    def test_few_examples_and_a_thin_test_group_are_advice_only(self) -> None:
        for seed in range(1, 7):
            self.picture(seed, "Dent")
        body = self.health()
        self.assertTrue(body["ready_to_teach"])
        joined = " ".join(body["advice"])
        self.assertIn("Dent", joined)
        self.assertIn("held back", joined)

    def test_a_missing_picture_file_blocks_because_a_snapshot_would_be_refused(self) -> None:
        asset = self.picture(1, "Dent")
        default_storage.delete(asset.storage_key)
        body = self.health()
        self.assertFalse(body["ready_to_teach"])
        self.assertTrue(any("missing" in line for line in body["blockers"]))
        self.assertEqual(self.client.post(self.url(), data="{}", content_type="application/json").status_code, 422)

    def test_a_box_outside_its_picture_blocks(self) -> None:
        """The database refuses such boxes today; the check stays as a guard, so it is exercised by patching."""
        from unittest import mock

        from .datasets import validation

        self.picture(1, "Dent")
        with mock.patch.object(health.validation, "geometry_problem", return_value=validation.Problem("geometry_out_of_range", "x")):
            body = self.health()
        self.assertFalse(body["ready_to_teach"])
        self.assertTrue(any("outside" in line for line in body["blockers"]))


# -- near duplicates -------------------------------------------------------------------------------
class NearDuplicateTests(HealthBase):
    def ids(self, *assets) -> list[str]:
        return sorted(str(a.id) for a in assets)

    def test_a_noisy_re_encoded_copy_is_grouped_with_its_original(self) -> None:
        base = scene(7)
        original = self.store(base, "Dent")
        copy = self.store(noisy(base), "Dent", fmt="JPEG", quality=70)
        unrelated = [self.picture(900 + n, "Dent") for n in range(5)]
        near = self.health()["near_duplicates"]
        self.assertEqual(near["groups"], [self.ids(original, copy)])
        self.assertEqual(near["pictures_involved"], 2)
        self.assertEqual(near["identical_groups"], [])
        for other in unrelated:
            self.assertNotIn(str(other.id), near["groups"][0])

    def test_unrelated_pictures_are_not_grouped(self) -> None:
        for n in range(30):
            self.picture(1000 + n, "Dent")
        near = self.health()["near_duplicates"]
        self.assertEqual((near["groups"], near["pictures_involved"]), ([], 0))
        self.assertEqual(near["checked"], 30)
        self.assertIn("No nearly identical", near["message"])

    def test_a_chain_of_edited_copies_forms_one_group(self) -> None:
        base = scene(11)
        copies = [self.store(base, "Dent"), self.store(noisy(base, 5), "Dent", fmt="JPEG", quality=80),
                  self.store(noisy(base, 8), "Dent", fmt="JPEG", quality=60)]
        near = self.health()["near_duplicates"]
        self.assertEqual(near["groups"], [self.ids(*copies)])

    def test_byte_identical_files_are_reported_separately_from_nearly_identical_ones(self) -> None:
        data = encode(scene(13))
        a = self.make_asset(data=data, size=SIZE)
        b = self.make_asset(data=data, size=SIZE)
        for asset in (a, b):
            self.make_box(asset, "Dent")
        near = self.health()["near_duplicates"]
        self.assertEqual(near["groups"], [])
        self.assertEqual(near["identical_groups"], [self.ids(a, b)])
        self.assertEqual(near["pictures_involved"], 0)

    def test_a_group_split_between_learning_and_the_test_is_counted_as_leaking(self) -> None:
        """Take copies until the splitter puts two near-copies in different groups, then check the count."""
        base = scene(21)
        copies = [self.store(noisy(base, 2 + n), "Dent", fmt="JPEG", quality=95 - n) for n in range(12)]
        for n in range(6):
            self.picture(1100 + n, "Dent")
        body = self.health()
        near = body["near_duplicates"]
        self.assertEqual(near["groups"], [self.ids(*copies)])
        shas = {c.sha256 for c in copies}
        assert len(shas) == len(copies)  # distinct bytes, so the splitter treats them as different pictures
        split_of = splits.assign_splits([a.sha256 for a in self.project.assets.all()], splits.SplitConfig.default())
        spans = len({split_of[c.sha256] for c in copies}) > 1
        self.assertEqual(near["groups_crossing_splits"], 1 if spans else 0)
        self.assertTrue(spans, "12 distinct copies across 18 pictures should land in more than one group")
        self.assertIn("score", near["message"])
        self.assertTrue(any("nearly identical" in line for line in body["advice"]))

    def test_rotation_is_not_detected_and_that_is_stated(self) -> None:
        """dHash is not rotation invariant. This test pins today's behaviour so nobody over-claims; it is not a promise."""
        base = scene(31)
        self.store(base, "Dent")
        self.store(base.rotate(90, expand=True), "Dent")
        for n in range(4):
            self.picture(1200 + n, "Dent")
        near = self.health()["near_duplicates"]
        self.assertEqual(near["groups"], [])
        self.assertIn("rotated", near["message"])

    def crop_copy(self, fraction: float) -> list:
        base = scene(41)
        self.store(base, "Dent")
        width, height = base.size
        dx, dy = int(width * fraction), int(height * fraction)
        self.store(base.crop((dx, dy, width - dx, height - dy)).resize(base.size), "Dent")
        for n in range(4):
            self.picture(1250 + n, "Dent")
        return self.health()["near_duplicates"]["groups"]

    def test_a_very_slight_crop_is_still_grouped_on_a_generated_scene(self) -> None:
        """Observed on one generated scene only (2% off every side moved 2 of 64 bits); not a promise for real pictures."""
        self.assertEqual(len(self.crop_copy(0.02)), 1)

    def test_a_noticeable_crop_is_not_found(self) -> None:
        """12.5% off every side moved 18 of 64 bits on the generated scene. Pinned so nobody over-claims; not a promise."""
        self.assertEqual(self.crop_copy(0.125), [])

    def test_an_unreadable_and_a_missing_file_are_reported_not_fatal(self) -> None:
        good = self.picture(1, "Dent")
        junk = self.make_asset(data=b"this is not a picture", size=SIZE)
        self.make_box(junk, "Dent")
        gone = self.picture(2, "Dent")
        default_storage.delete(gone.storage_key)
        response = self.client.get(self.health_url())
        self.assertEqual(response.status_code, 200)
        near = response.json()["near_duplicates"]
        reasons = {row["asset_id"]: row["reason"] for row in near["not_checked"]}
        self.assertEqual(reasons, {str(junk.id): "unreadable", str(gone.id): "missing"})
        self.assertEqual(near["checked"], 1)
        self.assertNotIn(str(good.id), reasons)
        self.assertIn("could not be opened", near["message"])

    def test_only_pictures_with_accepted_boxes_are_compared(self) -> None:
        base = scene(51)
        self.store(base, "Dent")
        self.make_asset(data=encode(noisy(base), "JPEG", quality=70), size=SIZE)  # no boxes: it would not enter a snapshot
        near = self.health()["near_duplicates"]
        self.assertEqual(near["groups"], [])
        self.assertEqual(near["checked"], 1)

    def test_a_solid_colour_picture_is_not_grouped_with_a_different_solid_colour(self) -> None:
        self.store(Image.new("RGB", SIZE, (10, 10, 10)), "Dent")
        self.store(Image.new("RGB", SIZE, (240, 240, 240)), "Dent")
        self.assertEqual(self.health()["near_duplicates"]["groups"], [])

    def test_the_cap_on_hashed_pictures_is_reported(self) -> None:
        for n in range(5):
            self.picture(1300 + n, "Dent")
        from unittest import mock

        with mock.patch.object(health, "MAX_PICTURES_HASHED", 3):
            near = self.health()["near_duplicates"]
        self.assertEqual(near["checked"], 3)
        self.assertEqual(sorted(row["reason"] for row in near["not_checked"]), ["skipped_limit"] * 2)


class GroupingUnitTests(HealthBase):
    def test_the_bucketed_search_finds_exactly_what_a_brute_force_search_finds(self) -> None:
        rng = random.Random(3)
        hashes = {}
        bases = [rng.getrandbits(64) for _ in range(20)]
        for index in range(300):
            value = rng.choice(bases)
            for bit in rng.sample(range(64), rng.randrange(0, 11)):  # 0..10 flipped bits
                value ^= 1 << bit
            hashes[f"p{index}"] = (value, 128)
        found = health.near_pairs(hashes, health.HAMMING_THRESHOLD)
        names = sorted(hashes)
        brute = {
            (a, b)
            for i, a in enumerate(names)
            for b in names[i + 1 :]
            if bin(hashes[a][0] ^ hashes[b][0]).count("1") <= health.HAMMING_THRESHOLD
        }
        self.assertEqual({tuple(sorted(pair)) for pair in found}, brute)
        self.assertGreater(len(brute), 20)  # the test has something to find

    def test_brightness_guard_blocks_equal_hashes_of_very_different_pictures(self) -> None:
        pairs = health.near_pairs({"a": (0, 5), "b": (0, 250)}, 6)
        self.assertEqual(list(pairs), [])
        self.assertEqual(len(list(health.near_pairs({"a": (0, 5), "b": (0, 8)}, 6))), 1)


class SpeedTests(HealthBase):
    def test_hashing_many_pictures_is_fast(self) -> None:
        count = int(os.environ.get("HEALTH_PERF_N", "120"))
        rng = random.Random(1)
        base = scene(1, (640, 480))
        for n in range(count):
            image = scene(n, (640, 480)) if n % 5 else noisy(base)
            asset = self.make_asset(data=encode(image, "JPEG", quality=85), size=(640, 480))
            self.make_box(asset, rng.choice(self.LABELS))
        started = time.perf_counter()
        body = self.health()
        elapsed = time.perf_counter() - started
        print(f"\n[health] {count} generated 640x480 JPEGs checked in {elapsed:.2f}s", flush=True)
        self.assertEqual(body["near_duplicates"]["checked"], count)
        self.assertLess(elapsed, 15.0)  # generous ceiling for a loaded machine; the measured figure is printed above


class ExampleResponseTests(HealthBase):
    def test_a_mixed_project_answers_with_plain_json(self) -> None:
        """Builds a small mixed project (few, enough, none; a near-copy; an unreadable file). Set HEALTH_SHOW_JSON=1 to print the answer."""
        import json

        for n in range(14):
            self.picture(2000 + n, "Scratch", "Scratch")
        for n in range(4):
            self.picture(2100 + n, "Dent")
        base = scene(2200)
        self.store(base, "Scratch")
        self.store(noisy(base), "Scratch", fmt="JPEG", quality=70)
        self.make_suggestion(self.make_asset(data=encode(scene(2300)), size=SIZE), "Rust")
        junk = self.make_asset(data=b"not a picture", size=SIZE)
        self.make_box(junk, "Dent")
        body = self.health()
        text = json.dumps(body, indent=2)
        if os.environ.get("HEALTH_SHOW_JSON"):
            print("\n" + text, flush=True)
        self.assertEqual(json.loads(text), body)
        self.assertEqual(len(body["near_duplicates"]["groups"]), 1)
        self.assertTrue(body["ready_to_teach"])


# -- what a snapshot would refuse (mirrors datasets/service.analyse) -----------------------------------
class SnapshotRefusalMirrorTests(HealthBase):
    """ready_to_teach must not say yes where /datasets/readiness says no, with the same plain sentences."""

    def healthy(self, count: int = 8) -> list[Asset]:
        return [self.picture(400 + n, "Dent") for n in range(count)]

    def readiness(self) -> dict:
        return self.client.get(self.url("/readiness")).json()

    def assert_mirrors_readiness(self, body: dict, fragment: str) -> None:
        ready = self.readiness()
        self.assertFalse(ready["ready"])
        wanted = [line for line in ready["blockers"] if fragment in line]
        self.assertEqual(len(wanted), 1, ready["blockers"])
        self.assertFalse(body["ready_to_teach"], body["blockers"])
        self.assertIn(wanted[0], body["blockers"])  # the very same sentence
        self.assertEqual(body["reasons"], body["blockers"] + body["advice"])

    def test_the_healthy_project_agrees_with_readiness(self) -> None:
        self.healthy()
        body = self.health()
        self.assertTrue(self.readiness()["ready"])
        self.assertTrue(body["ready_to_teach"], body["blockers"])

    def test_labels_with_line_breaks_or_control_characters_block(self) -> None:
        self.healthy()
        self.project.labels = ["x\nbreak", "tab\there", "Dent"]
        self.project.save()
        self.assert_mirrors_readiness(self.health(), "contain line breaks or control characters")

    def test_a_file_changed_on_disk_since_upload_blocks(self) -> None:
        assets = self.healthy()
        with open(default_storage.path(assets[0].storage_key), "wb") as handle:
            handle.write(encode(scene(999, (200, 150))))  # a different picture under the recorded name
        self.assert_mirrors_readiness(self.health(), "changed on disk since upload")

    def test_a_file_changed_after_a_first_check_is_noticed_on_the_next_one(self) -> None:
        assets = self.healthy()
        self.assertTrue(self.health()["ready_to_teach"])  # warms any verification cache
        with open(default_storage.path(assets[1].storage_key), "ab") as handle:
            handle.write(b"tampered")
        self.assert_mirrors_readiness(self.health(), "changed on disk since upload")

    def test_more_pictures_than_a_version_may_hold_block(self) -> None:
        self.healthy(6)
        with override_settings(DATASET_MAX_IMAGES=3):
            body = self.health()
            self.assert_mirrors_readiness(body, "more images than one dataset version may hold")

    def test_a_missing_file_is_still_reported_once(self) -> None:
        assets = self.healthy()
        default_storage.delete(assets[0].storage_key)
        body = self.health()
        self.assertFalse(body["ready_to_teach"])
        self.assertEqual(sum("missing" in line for line in body["blockers"]), 1, body["blockers"])
        self.assertFalse(any("changed on disk" in line for line in body["blockers"]))

    def test_existing_blocker_sentences_are_unchanged(self) -> None:
        self.picture(1, "Dent")
        self.make_box(self.project.assets.first(), "Nope")
        body = self.health()
        self.assertTrue(any("not in the project's label list" in line for line in body["blockers"]))


# -- nothing to learn from ---------------------------------------------------------------------------------
class NothingToLearnTests(HealthBase):
    def test_one_picture_leaves_nothing_to_learn_from_and_blocks(self) -> None:
        self.picture(1, "Dent", "Scratch", "Rust")
        body = self.health()
        self.assertEqual(body["held_out"]["expected_counts"]["train"], 0)
        self.assertEqual({row["verdict"] for row in body["labels"]}, {"none"})
        self.assertFalse(body["ready_to_teach"])
        self.assertTrue(any("more pictures" in line and "learn from" in line for line in body["blockers"]), body["blockers"])
        self.assertEqual(body["reasons"], body["blockers"] + body["advice"])

    def test_a_split_with_no_learning_share_blocks(self) -> None:
        for seed in range(12):
            self.picture(20 + seed, "Dent")
        body = self.health("?train=0&val=0.5&test=0.5")
        self.assertEqual(body["held_out"]["expected_counts"]["train"], 0)
        self.assertFalse(body["ready_to_teach"])
        self.assertTrue(any("learn from" in line for line in body["blockers"]), body["blockers"])

    def test_enough_pictures_to_learn_from_do_not_trigger_it(self) -> None:
        for seed in range(8):
            self.picture(300 + seed, "Dent")
        body = self.health()
        self.assertGreater(body["held_out"]["expected_counts"]["train"], 0)
        self.assertTrue(body["ready_to_teach"], body["blockers"])
        self.assertFalse(any("learn from" in line for line in body["blockers"]))

    def test_it_is_not_added_on_top_of_the_no_boxes_blocker(self) -> None:
        body = self.health()
        self.assertEqual(len(body["blockers"]), 1)


# -- 16-bit and floating point pictures ---------------------------------------------------------------------
def wide_scene(seed: int, low: int = 256, high: int = 65535, size: tuple[int, int] = SIZE, mode: str = "I;16") -> Image.Image:
    """A repeatable picture with real structure whose values run from ``low`` to ``high`` (needs 16 bits)."""
    rng = random.Random(seed)
    width, height = size
    blocks = [[rng.randrange(low, high + 1) for _ in range(8)] for _ in range(8)]
    image = Image.new(mode, size)
    image.putdata([blocks[y * 8 // height][x * 8 // width] for y in range(height) for x in range(width)])
    return image


class WidePictureTests(HealthBase):
    def test_a_bright_16_bit_picture_does_not_clip_to_white(self) -> None:
        bits, grey = health.dhash(io.BytesIO(encode(wide_scene(1, 40000, 65000))))
        self.assertGreater(grey, 150)
        self.assertLess(grey, 255)
        self.assertGreater(bits.bit_count(), 5)  # it has structure; a clipped copy hashes to zero

    def test_16_bit_values_are_scaled_to_8_bit_not_clipped(self) -> None:
        for value, expected in ((256, 1), (32768, 128), (65535, 255)):
            flat = Image.new("I;16", (40, 30), value)
            self.assertEqual(health.dhash(io.BytesIO(encode(flat)))[1], expected)

    def test_a_16_bit_picture_matches_its_8_bit_rendering(self) -> None:
        wide = wide_scene(3, 0, 65535)
        narrow = Image.new("L", wide.size)
        narrow.putdata([v >> 8 for v in wide.convert("I").getdata()])
        a, b = health.dhash(io.BytesIO(encode(wide))), health.dhash(io.BytesIO(encode(narrow)))
        self.assertLessEqual((a[0] ^ b[0]).bit_count(), health.HAMMING_THRESHOLD)
        self.assertLessEqual(abs(a[1] - b[1]), 1)

    def test_different_16_bit_pictures_are_not_grouped(self) -> None:
        for seed in range(4):
            self.store(wide_scene(10 + seed, 30000, 65000), "Dent")
        near = self.health()["near_duplicates"]
        self.assertEqual(near["groups"], [])
        self.assertEqual(near["checked"], 4)

    def test_dark_and_bright_16_bit_pictures_are_not_grouped(self) -> None:
        self.store(wide_scene(20, 300, 6000), "Dent")
        self.store(wide_scene(21, 50000, 65000), "Dent")
        self.assertEqual(self.health()["near_duplicates"]["groups"], [])

    def test_the_same_16_bit_content_saved_twice_is_grouped(self) -> None:
        image = wide_scene(30, 20000, 65000)
        first = self.store(image, "Dent")
        again = self.make_asset(data=encode(image, "PNG", compress_level=0), size=SIZE)  # different bytes, same pixels
        self.make_box(again, "Dent")
        self.assertNotEqual(first.sha256, again.sha256)
        near = self.health()["near_duplicates"]
        self.assertEqual(near["groups"], [sorted([str(first.id), str(again.id)])])

    def test_32_bit_integer_and_float_pictures_are_not_clipped_either(self) -> None:
        for mode in ("I", "F"):
            a = wide_scene(40, 20000, 65000, mode=mode)
            b = wide_scene(41, 20000, 65000, mode=mode)
            bits_a, grey_a = health.dhash(io.BytesIO(encode(a, "TIFF")))
            bits_b, grey_b = health.dhash(io.BytesIO(encode(b, "TIFF")))
            self.assertNotEqual((bits_a, grey_a), (255, 255), mode)
            self.assertGreater((bits_a ^ bits_b).bit_count(), health.HAMMING_THRESHOLD, mode)
            again = health.dhash(io.BytesIO(encode(a, "TIFF", compression="tiff_adobe_deflate")))
            self.assertEqual(again, (bits_a, grey_a), mode)

    def test_ordinary_pictures_hash_exactly_as_before(self) -> None:
        self.assertEqual(health.dhash(io.BytesIO(encode(Image.new("L", SIZE, 77)))), (0, 77))
        self.assertEqual(health.dhash(io.BytesIO(encode(Image.new("RGB", SIZE, (10, 10, 10))))), (0, 10))


# -- time budget -----------------------------------------------------------------------------------------------
class TimeBudgetTests(HealthBase):
    class Clock:
        def __init__(self) -> None:
            self.now = 0.0

        def __call__(self) -> float:
            return self.now

    def run_with_slow_hashing(self, seconds_each: float = 1.0) -> dict:
        clock = self.Clock()
        real = health._hash_of_stored

        def slow(key):
            clock.now += seconds_each
            return real(key)

        with mock.patch.object(health, "_clock", clock), mock.patch.object(health, "_hash_of_stored", slow):
            return self.health()

    def test_the_budget_is_a_named_constant_and_is_echoed(self) -> None:
        self.assertIsInstance(health.MAX_HASH_SECONDS, (int, float))
        self.picture(1, "Dent")
        self.assertEqual(self.health()["thresholds"]["max_hash_seconds"], health.MAX_HASH_SECONDS)

    def test_pictures_after_the_budget_are_listed_as_not_compared(self) -> None:
        assets = [self.picture(500 + n, "Dent") for n in range(10)]
        with mock.patch.object(health, "MAX_HASH_SECONDS", 4):
            near = self.run_with_slow_hashing()["near_duplicates"]
        order = sorted(str(asset.id) for asset in assets)
        self.assertEqual(near["checked"], 4)
        self.assertEqual(near["not_checked_count"], 6)
        self.assertEqual([row["asset_id"] for row in near["not_checked"]], order[4:])
        self.assertEqual({row["reason"] for row in near["not_checked"]}, {"time"})
        self.assertIn("not compared", near["message"])
        self.assertIn("time", near["message"])

    def test_the_time_note_reaches_the_advice_but_is_not_a_blocker(self) -> None:
        for n in range(10):
            self.picture(520 + n, "Dent")
        with mock.patch.object(health, "MAX_HASH_SECONDS", 4):
            body = self.run_with_slow_hashing()
        self.assertTrue(any("not compared" in line for line in body["advice"]), body["advice"])
        self.assertFalse(any("not compared" in line for line in body["blockers"]))
        self.assertTrue(body["ready_to_teach"], body["blockers"])

    def test_grouping_of_the_pictures_that_were_hashed_is_the_same_every_time(self) -> None:
        base = scene(540)
        assets = [self.store(base, "Dent"), self.store(noisy(base), "Dent", fmt="JPEG", quality=70)]
        assets += [self.picture(550 + n, "Dent") for n in range(8)]
        order = sorted(str(asset.id) for asset in assets)
        with mock.patch.object(health, "MAX_HASH_SECONDS", 5):
            first = self.run_with_slow_hashing()["near_duplicates"]
            second = self.run_with_slow_hashing()["near_duplicates"]
        self.assertEqual(first, second)
        pair = {str(assets[0].id), str(assets[1].id)}
        expected = [sorted(pair)] if pair <= set(order[:5]) else []
        self.assertEqual(first["groups"], expected)

    def test_a_fast_check_hashes_everything(self) -> None:
        for n in range(6):
            self.picture(570 + n, "Dent")
        near = self.health()["near_duplicates"]
        self.assertEqual((near["checked"], near["not_checked_count"]), (6, 0))

    def test_the_default_budget_is_a_few_seconds(self) -> None:
        self.assertGreaterEqual(health.MAX_HASH_SECONDS, 1)
        self.assertLessEqual(health.MAX_HASH_SECONDS, 10)

    def test_a_missing_file_still_blocks_when_the_comparison_ran_out_of_time(self) -> None:
        assets = [self.picture(600 + n, "Dent") for n in range(8)]
        last = max(assets, key=lambda asset: str(asset.id))
        default_storage.delete(last.storage_key)
        with mock.patch.object(health, "MAX_HASH_SECONDS", 2):
            body = self.run_with_slow_hashing()
        self.assertEqual({row["reason"] for row in body["near_duplicates"]["not_checked"]}, {"time"})
        self.assertEqual(sum("missing from storage" in line for line in body["blockers"]), 1, body["blockers"])
        self.assertFalse(body["ready_to_teach"])

    def test_the_checksum_re_check_has_the_same_budget_and_says_when_it_stopped(self) -> None:
        for n in range(8):
            self.picture(620 + n, "Dent")
        clock = self.Clock()
        real = health.validation.file_problem

        def slow(*args, **kwargs):
            clock.now += 1.0
            return real(*args, **kwargs)

        with (
            mock.patch.object(health, "_clock", clock),
            mock.patch.object(health, "MAX_HASH_SECONDS", 3),
            mock.patch.object(health.validation, "file_problem", slow),
        ):
            body = self.health()
        note = [line for line in body["advice"] if "not re-checked" in line]
        self.assertEqual(len(note), 1, body["advice"])
        self.assertIn("5 pictures were not re-checked", note[0])
        self.assertTrue(body["ready_to_teach"])  # an unfinished re-check is advice, not a blocker


# -- worst-case grouping work (round 2) -----------------------------------------------------------------------
def unsaved_assets(count: int, width: int = 10, height: int = 10) -> dict:
    """Picture rows that were never saved (no files): ``count`` pictures with different bytes."""
    import uuid

    rows = {}
    for index in range(count):
        asset = Asset(
            id=uuid.uuid4(), filename=f"{index}.png", storage_key=f"synthetic/{index}.png", content_type="image/png",
            sha256=f"{index:064x}", size_bytes=1, width=width, height=height,
        )
        rows[asset.id] = asset
    return rows


class WorstCaseGroupingTests(SimpleTestCase):
    def components_by_pairs(self, hashes) -> list[list[str]]:
        return health._components(hashes, health.near_pairs(hashes, health.HAMMING_THRESHOLD))

    def test_groups_without_listing_pairs_equal_the_pair_based_answer(self) -> None:
        rng = random.Random(11)
        for case in range(60):
            bases = [rng.getrandbits(64) for _ in range(rng.randint(1, 8))]
            hashes = {}
            for index in range(rng.randint(2, 120)):
                value = rng.choice(bases)
                for bit in rng.sample(range(64), rng.choice([0, 0, 1, 2, 4, 6, 7, 9])):
                    value ^= 1 << bit
                hashes[f"p{index:03d}"] = (value, rng.choice([rng.randint(0, 255), rng.randint(100, 130)]))
            with self.subTest(case=case):
                self.assertEqual(health.near_groups(hashes, health.HAMMING_THRESHOLD), self.components_by_pairs(hashes))

    def test_identical_hashes_with_far_apart_brightness_form_separate_groups(self) -> None:
        hashes = {"a": (0, 0), "b": (0, 20), "c": (0, 50), "d": (0, 80), "e": (0, 250)}  # a..d chain; a-d are 80 apart
        self.assertEqual(health.near_groups(hashes, 6), [["a", "b", "c", "d"]])
        self.assertEqual(health.near_groups(hashes, 6), self.components_by_pairs(hashes))
        apart = {"a": (0, 0), "b": (0, 20), "c": (0, 60), "d": (0, 90)}  # a gap of 40 splits the run
        self.assertEqual(health.near_groups(apart, 6), [["a", "b"], ["c", "d"]])
        self.assertEqual(health.near_groups(apart, 6), self.components_by_pairs(apart))

    def test_two_thousand_identical_hashes_are_grouped_in_linear_time(self) -> None:
        hashes = {f"p{index:04d}": (0x123456789ABCDEF, 100) for index in range(2000)}
        started = time.perf_counter()
        groups = health.near_groups(hashes, health.HAMMING_THRESHOLD)
        elapsed = time.perf_counter() - started
        print(f"\n[health] 2000 identical hashes grouped in {elapsed:.3f}s", flush=True)
        self.assertEqual(groups, [sorted(hashes)])
        self.assertLess(elapsed, 0.25)

    def test_two_thousand_pictures_with_one_hash_do_not_make_the_request_slow(self) -> None:
        assets = unsaved_assets(2000)
        split = {asset_id: "train" for asset_id in assets}
        started = time.perf_counter()
        with mock.patch.object(health, "_hash_of_stored", return_value=(0x123456789ABCDEF, 100)):
            near, _, _ = health._near_duplicates(assets, split)
        elapsed = time.perf_counter() - started
        print(f"\n[health] _near_duplicates over 2000 identical hashes: {elapsed:.3f}s", flush=True)
        self.assertEqual((near["checked"], near["group_count"], near["pictures_involved"]), (2000, 1, 2000))
        self.assertLess(elapsed, 0.5)

    def test_many_pictures_in_one_dense_cluster_are_grouped_quickly(self) -> None:
        rng = random.Random(5)
        base = rng.getrandbits(64)
        hashes = {}
        for index in range(1500):  # every hash within 3 flipped bits of the base: all pairwise within 6
            value = base
            for bit in rng.sample(range(64), rng.randint(0, 3)):
                value ^= 1 << bit
            hashes[f"p{index:04d}"] = (value, 100)
        started = time.perf_counter()
        groups = health.near_groups(hashes, health.HAMMING_THRESHOLD)
        self.assertLess(time.perf_counter() - started, 1.0)
        self.assertEqual(groups, [sorted(hashes)])


# -- one budget, and a pixel guard (round 2) -----------------------------------------------------------------------
class SharedBudgetTests(HealthBase):
    class Clock:
        def __init__(self) -> None:
            self.now = 0.0

        def __call__(self) -> float:
            return self.now

    def test_the_checksum_re_check_and_the_hashing_share_one_budget(self) -> None:
        for n in range(8):
            self.picture(700 + n, "Dent")
        clock = self.Clock()
        real_problem, real_hash = health.validation.file_problem, health._hash_of_stored
        calls = {"checksum": 0, "hash": 0}

        def slow_problem(*args, **kwargs):
            clock.now += 1.0
            calls["checksum"] += 1
            return real_problem(*args, **kwargs)

        def slow_hash(key):
            clock.now += 1.0
            calls["hash"] += 1
            return real_hash(key)

        with (
            mock.patch.object(health, "_clock", clock),
            mock.patch.object(health, "MAX_HASH_SECONDS", 4),
            mock.patch.object(health.validation, "file_problem", slow_problem),
            mock.patch.object(health, "_hash_of_stored", slow_hash),
        ):
            body = self.health()
        self.assertEqual(calls["checksum"] + calls["hash"], 4, calls)  # not 4 + 4
        near = body["near_duplicates"]
        self.assertEqual(near["checked"] + near["not_checked_count"], 8)
        self.assertEqual({row["reason"] for row in near["not_checked"]}, {"time"})
        self.assertTrue(body["ready_to_teach"])

    def test_the_whole_request_is_bounded_by_one_named_constant(self) -> None:
        self.assertIsInstance(health.MAX_HASH_SECONDS, (int, float))
        self.assertEqual(health.thresholds()["max_hash_seconds"], health.MAX_HASH_SECONDS)


class PixelGuardTests(HealthBase):
    def test_the_limit_is_the_upload_limit(self) -> None:
        from . import views

        self.assertEqual(health.MAX_PICTURE_PIXELS, views.MAX_IMAGE_PIXELS)
        self.assertEqual(health.thresholds()["max_picture_pixels"], health.MAX_PICTURE_PIXELS)

    def asset_declared_as(self, width: int, height: int, seed: int) -> Asset:
        asset = self.picture(seed, "Dent")
        Asset.objects.filter(pk=asset.pk).update(width=width, height=height)
        return asset

    def test_a_picture_over_the_limit_is_not_decoded_and_says_why(self) -> None:
        big = self.asset_declared_as(6000, 5000, 800)  # 30 million pixels
        fine = [self.picture(801 + n, "Dent") for n in range(2)]
        decoded = []
        real = health._hash_of_stored
        with mock.patch.object(health, "_hash_of_stored", lambda key: decoded.append(key) or real(key)):
            body = self.health()
        near = body["near_duplicates"]
        self.assertNotIn(big.storage_key, decoded)
        self.assertEqual(len(decoded), 2)
        self.assertEqual(near["not_checked"], [{"asset_id": str(big.id), "reason": "too_large"}])
        self.assertEqual(near["checked"], 2)
        self.assertIn("too large", near["message"])
        self.assertTrue(any("too large" in line for line in body["advice"]))
        self.assertTrue(body["ready_to_teach"])  # advice, not a blocker
        del fine

    def test_exactly_the_limit_is_still_decoded(self) -> None:
        self.asset_declared_as(5000, 5000, 810)
        near = self.health()["near_duplicates"]
        self.assertEqual((near["checked"], near["not_checked_count"]), (1, 0))

    def test_a_decompression_bomb_is_still_reported_as_unreadable(self) -> None:
        asset = self.picture(820, "Dent")
        with mock.patch.object(health, "dhash", side_effect=Image.DecompressionBombError("too many pixels")):
            near = self.health()["near_duplicates"]
        self.assertEqual(near["not_checked"], [{"asset_id": str(asset.id), "reason": "unreadable"}])


# -- the notes that must not hide under a green headline (round 2) ----------------------------------------------------
class PriorityNoteTests(HealthBase):
    def crossing_project(self) -> list[Asset]:
        base = scene(21)
        copies = [self.store(noisy(base, 2 + n), "Dent", fmt="JPEG", quality=95 - n) for n in range(12)]
        for n in range(6):
            self.picture(1100 + n, "Dent")
        return copies

    def test_the_keys_are_additive(self) -> None:
        self.picture(1, "Dent")
        body = self.health()
        self.assertIsInstance(body["priority_notes"], list)
        self.assertEqual(set(body["headline"]), {"level", "text"})
        self.assertIn(body["headline"]["level"], ("ready", "ready_with_warnings", "not_ready"))
        self.assertTrue(body["headline"]["text"])

    def test_a_project_with_nothing_to_flag_is_plain_ready(self) -> None:
        for n in range(40):
            for label in self.LABELS:
                self.picture(2000 + n * 3 + self.LABELS.index(label), label, label)
        body = self.health()
        self.assertEqual(body["priority_notes"], [])
        self.assertTrue(body["ready_to_teach"], body["blockers"])
        self.assertEqual(body["headline"]["level"], "ready")

    def test_crossing_near_duplicates_zero_box_labels_and_the_fewest_label_come_first_in_that_order(self) -> None:
        self.crossing_project()
        body = self.health()
        self.assertTrue(body["ready_to_teach"], body["blockers"])
        self.assertEqual(body["near_duplicates"]["groups_crossing_splits"], 1)
        notes = body["priority_notes"]
        self.assertEqual(len(notes), 3, notes)
        self.assertIn("score", notes[0])
        self.assertIn("split", notes[0])
        self.assertTrue("Scratch" in notes[1] and "Rust" in notes[1], notes[1])
        self.assertIn("Dent", notes[2])
        self.assertEqual(body["headline"]["level"], "ready_with_warnings")
        # every note is also still in the existing lists (nothing was moved out)
        self.assertTrue(any("nearly identical" in line for line in body["advice"]))

    def test_a_zero_box_label_alone_makes_ready_with_warnings(self) -> None:
        for n in range(6):
            self.picture(70 + n, "Dent")
        body = self.health()
        self.assertTrue(body["ready_to_teach"])
        self.assertEqual(len(body["priority_notes"]), 2)  # zero-box Scratch+Rust, then the fewest-examples Dent
        self.assertIn("Scratch", body["priority_notes"][0])
        self.assertEqual(body["headline"]["level"], "ready_with_warnings")

    def test_the_fewest_examples_label_is_named_once_and_only_when_it_is_short(self) -> None:
        for n in range(8):
            self.picture(3000 + n, "Dent", "Dent", "Dent", "Dent")
        for n in range(3):
            self.picture(3100 + n, "Scratch")
        for n in range(15):
            self.picture(3200 + n, "Rust", "Rust")
        notes = self.health()["priority_notes"]
        fewest = [line for line in notes if "fewest" in line]
        self.assertEqual(len(fewest), 1, notes)
        self.assertIn("Scratch", fewest[0])

    def test_never_more_than_three(self) -> None:
        self.crossing_project()
        self.assertLessEqual(len(self.health()["priority_notes"]), 3)

    def test_a_blocked_project_is_not_ready_whatever_the_notes(self) -> None:
        body = self.health()  # no pictures
        self.assertFalse(body["ready_to_teach"])
        self.assertEqual(body["headline"]["level"], "not_ready")
        self.assertTrue(body["headline"]["text"])

    # -- no pictures held back (final integration review I1) ------------------------------------------------
    def no_test_project(self) -> None:
        """Two different pictures: the default split cannot give the final test any."""
        chosen = [  # pictures whose own hash does not fall in the test group
            n for n in range(1, 60)
            if splits.assign_split(hashlib.sha256(encode(scene(n))).hexdigest(), splits.SplitConfig.default()) != "test"
        ][:2]
        for n in chosen:
            self.picture(n, "Dent")

    def test_no_held_out_pictures_is_a_priority_note_and_the_headline_warns(self) -> None:
        self.no_test_project()
        body = self.health()
        self.assertTrue(body["ready_to_teach"], body["blockers"])
        self.assertFalse(body["held_out"]["would_have_test_pictures"])
        held = [line for line in body["priority_notes"] if "held back" in line]
        self.assertEqual(len(held), 1, body["priority_notes"])
        self.assertIn("approve", held[0])
        self.assertEqual(body["headline"]["level"], "ready_with_warnings")
        # the sentence is still where it was (nothing moved out of advice/reasons)
        self.assertIn(body["held_out"]["message"], body["advice"])

    def test_the_held_out_note_comes_before_zero_box_and_fewest_examples_notes(self) -> None:
        self.no_test_project()  # Dent only: Scratch and Rust have no boxes
        notes = self.health()["priority_notes"]
        self.assertEqual(len(notes), 3, notes)
        self.assertIn("held back", notes[0])
        self.assertTrue("Scratch" in notes[1] and "Rust" in notes[1], notes[1])
        self.assertIn("fewest", notes[2])

    def test_a_split_that_keeps_nothing_for_the_test_is_also_a_priority_note(self) -> None:
        for seed in range(12):
            self.picture(20 + seed, "Dent", "Scratch", "Rust")
        body = self.health("?train=0.9&val=0.1&test=0")
        self.assertTrue(any("held back" in line or "final test" in line for line in body["priority_notes"]), body["priority_notes"])
        self.assertEqual(body["headline"]["level"], "ready_with_warnings")

    def test_a_project_with_test_pictures_has_no_held_out_note(self) -> None:
        self.crossing_project()
        body = self.health()
        self.assertTrue(body["held_out"]["would_have_test_pictures"])
        self.assertFalse(any("approve" in line for line in body["priority_notes"]), body["priority_notes"])

    def test_priority_order_is_crossing_then_held_out_then_zero_box_then_fewest_and_capped_at_three(self) -> None:
        labels = [
            {"label": "Dent", "accepted_boxes": 2, "verdict": "few", "expected_boxes": {"train": 1}},
            {"label": "Rust", "accepted_boxes": 0, "verdict": "none", "expected_boxes": {"train": 0}},
        ]
        near = {"groups_crossing_splits": 1}
        notes = health._priority_notes(labels, near, "NOHELD")
        self.assertEqual(len(notes), 3)
        self.assertIn("nearly identical", notes[0])
        self.assertIn("NOHELD", notes[1])
        self.assertIn("Rust", notes[2])  # the fewest-examples note is the one that falls off the end
        without = health._priority_notes(labels, near)
        self.assertEqual(len(without), 3)
        self.assertNotIn("NOHELD", " ".join(without))

    def test_priority_notes_are_plain_sentences_with_label_names_untouched(self) -> None:
        self.project.labels = ["<b>Odd</b> & co", "Dent"]
        self.project.save()
        self.picture(1, "Dent")
        notes = self.health()["priority_notes"]
        self.assertTrue(any("<b>Odd</b> & co" in line for line in notes), notes)
