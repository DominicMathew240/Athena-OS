"""T12: view suitability and source health for recorded sources.

What is under test, honestly: brightness is measured on synthetic bright and
dark clips (ffmpeg color sources, like the other recording tests); gap
statistics from the T20 timeline feed the timeline verdict; each dimension's
OK/Review/Unsuitable boundary holds; the user-entered object-size estimate
persists, validates, and is shown as an estimate, never a measurement; a
source with no recording gets a plain "record a test clip" answer, not a
crash; and the API shape stays the readiness-card pattern (one verdict, one
measurement, one limit, one next step per dimension).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings

from .models import OperationSource, Site, SourceViewEstimate
from .source_time import build_timeline
from .tests import grant
from .view_health import (
    DARK_SAMPLE_MINIMUM,
    DARK_YAVG,
    MAX_HEIGHT,
    MAX_WIDTH,
    ViewHealthError,
    coverage_dimension,
    day_night_dimension,
    object_size_dimension,
    obstruction_dimension,
    plain_recording_missing,
    recording_health,
    sample_frame_brightness,
    timeline_dimension,
)

REQUIRE_FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")


def _run(command, *, timeout=60):
    subprocess.run(command, check=True, capture_output=True, timeout=timeout)


def make_clip(directory, *, seconds=2, brightness="bright", drop_between=None):
    """A synthetic H.264 MP4 with the requested brightness.

    ``brightness`` is "bright" (near-white), "dark" (near-black) or "mixed"
    (the first half dark, the second bright). ``drop_between`` (start, end)
    removes that span of source frames while keeping the remaining frames'
    original presentation timestamps — a genuine timestamp gap.
    """
    if brightness == "mixed":
        # Each lavfi input needs its own duration, or the first never ends and
        # concat never reaches the bright half. The dark half oscillates: a real
        # night scene's brightness varies frame to frame, while the obstruction
        # proxy looks for dark AND near-motionless — a flat dark color would
        # wrongly read as a covered lens. (hue's time variable is lowercase t.)
        sources = ["-f", "lavfi", "-i", f"color=c=0x181818:s=64x48:r=5:d={seconds / 2}",
                   "-f", "lavfi", "-i", f"color=c=white:s=64x48:r=5:d={seconds / 2}"]
        filters = ["-filter_complex",
                   "[0:v:0]hue=b='0.4+0.4*sin(12*t)'[d];"
                   "[d][1:v:0]concat=n=2:v=1"]
    else:
        color = "white" if brightness == "bright" else "0x101010"
        sources = ["-f", "lavfi", "-i", f"color=c={color}:s=64x48:r=5"]
        filters = []
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", *sources, *filters,
        "-t", str(seconds), "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
    ]
    if drop_between is not None:
        start, end = drop_between
        command += ["-vf", f"select='not(between(t,{start},{end}))'"]
    path = Path(directory) / f"clip-{next(_counter)}.mp4"
    _run(command + ["-y", str(path)])
    data = path.read_bytes()
    path.unlink()
    return data


def _make_counter():
    value = 0
    while True:
        value += 1
        yield value


_counter = _make_counter()


class _NamedBytes:
    """A minimal file-like object the storage backend can read twice."""

    def __init__(self, data: bytes):
        from io import BytesIO

        self._stream = BytesIO(data)

    def read(self, *args):
        return self._stream.read(*args)

    def seek(self, *args):
        return self._stream.seek(*args)

    def tell(self):
        return self._stream.tell()

    def seekable(self):
        return True

    @property
    def size(self):
        position = self._stream.tell()
        size = self._stream.seek(0, 2)
        self._stream.seek(position)
        return size

    def chunks(self):
        yield self._stream.getvalue()

    def multiple_chunks(self):
        return False


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="t12-media-"))
@skipUnless(REQUIRE_FFMPEG, "ffmpeg and ffprobe required for view-health tests")
class ViewHealthBase(TestCase):
    """A source with recordings in private storage, like T09 leaves behind."""

    def setUp(self):
        # Files survive DB rollback between tests; start every test with empty storage.
        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)
        Path(settings.MEDIA_ROOT).mkdir(parents=True, exist_ok=True)
        self.user = get_user_model().objects.create_user("t12-owner")
        grant(self.user, "approver")
        self.client.force_login(self.user)
        site = Site.objects.create(created_by=self.user, name="Depot")
        self.source = OperationSource.objects.create(site=site, name="Gate", kind="recorded_video")
        self.tmp = tempfile.mkdtemp(prefix="t12-fixture-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def make_recording(self, body: bytes, *, width=64, height=48, duration=2.0):
        from .models import OperationSourceRecording

        key = default_storage.save(f"operation-source-recordings/{self.source.id}/rec.mp4", _NamedBytes(body))
        self.addCleanup(default_storage.delete, key)
        return OperationSourceRecording.objects.create(
            source=self.source,
            created_by=self.user,
            storage_key=key,
            filename="rec.mp4",
            sha256=hashlib.sha256(body).hexdigest(),
            size_bytes=len(body),
            width=width,
            height=height,
            duration_seconds=duration,
        )


# -- real measurements on synthetic clips ---------------------------------------------------------
class BrightnessTests(ViewHealthBase):
    def test_bright_and_dark_clips_measure_apart(self):
        bright = make_clip(self.tmp, seconds=2, brightness="bright")
        dark = make_clip(self.tmp, seconds=2, brightness="dark")
        from .view_health import _write_recording_copy

        for body, expected in ((bright, "high"), (dark, "low")):
            recording = self.make_recording(body)
            path = _write_recording_copy(recording)
            self.addCleanup(path.unlink, missing_ok=True)
            samples = sample_frame_brightness(path, 2.0)
            self.assertGreaterEqual(len(samples), 2)
            average = sum(samples) / len(samples)
            if expected == "high":
                self.assertGreater(average, DARK_YAVG * 2)
            else:
                self.assertLess(average, DARK_YAVG)

    def test_mixed_clip_reports_both_dark_and_bright_samples(self):
        body = make_clip(self.tmp, seconds=4, brightness="mixed")
        recording = self.make_recording(body, duration=4.0)
        from .view_health import _write_recording_copy

        path = _write_recording_copy(recording)
        self.addCleanup(path.unlink, missing_ok=True)
        samples = sample_frame_brightness(path, 4.0)
        dark = [value for value in samples if value <= DARK_YAVG]
        bright = [value for value in samples if value > DARK_YAVG]
        self.assertGreaterEqual(len(dark), DARK_SAMPLE_MINIMUM, samples)
        self.assertGreaterEqual(len(bright), 1, samples)


# -- gap statistics feed the verdict ----------------------------------------------------------------
class TimelineVerdictTests(ViewHealthBase):
    def test_a_clean_clip_is_ok_and_reports_its_real_numbers(self):
        body = make_clip(self.tmp, seconds=2, brightness="bright")
        recording = self.make_recording(body)
        dimension = timeline_dimension(build_timeline(recording))
        self.assertEqual(dimension["verdict"], "ok")
        self.assertIn("10 frames over 2 s", dimension["detail"])
        self.assertEqual(dimension["next_step"], "")

    def test_a_timestamp_gap_makes_the_view_unsuitable_with_an_installer_step(self):
        # Frames between 0.5 s and 1.5 s are removed; the remaining frames keep
        # their original timestamps, so a real ~1 s gap exists in the middle.
        body = make_clip(self.tmp, seconds=2, brightness="bright", drop_between=(0.5, 1.5))
        recording = self.make_recording(body)
        timeline = build_timeline(recording)
        self.assertTrue(timeline.gaps)
        dimension = timeline_dimension(timeline)
        self.assertEqual(dimension["verdict"], "unsuitable")
        self.assertIn("missing", dimension["detail"])
        self.assertIn("installer", dimension["next_step"])


# -- the verdict matrix: each dimension's boundaries -------------------------------------------------
class VerdictMatrixTests(ViewHealthBase):
    def test_coverage_boundaries(self):
        from .models import OperationSourceRecording

        def row(width, height):
            return OperationSourceRecording(
                source=self.source, created_by=self.user, storage_key="k", filename="f",
                sha256="0" * 64, size_bytes=1, width=width, height=height, duration_seconds=1,
            )

        self.assertEqual(coverage_dimension(row(MAX_WIDTH, MAX_HEIGHT))["verdict"], "ok")
        self.assertEqual(coverage_dimension(row(640, 480))["verdict"], "ok")
        too_wide = coverage_dimension(row(MAX_WIDTH + 1, MAX_HEIGHT))
        self.assertEqual(too_wide["verdict"], "unsuitable")
        self.assertIn(f"{MAX_WIDTH}×{MAX_HEIGHT}", too_wide["detail"])
        self.assertEqual(too_wide["next_step"], "Record a test clip from this source and check it here.")
        self.assertEqual(coverage_dimension(row(MAX_WIDTH, MAX_HEIGHT + 1))["verdict"], "unsuitable")

    def test_day_night_boundaries(self):
        bright_samples = [235.0] * 10
        mixed_samples = [30.0] * DARK_SAMPLE_MINIMUM + [235.0] * 8
        bright = day_night_dimension(bright_samples, 10.0)
        self.assertEqual(bright["verdict"], "review")
        self.assertIn("darker hours", bright["next_step"])
        mixed = day_night_dimension(mixed_samples, 10.0)
        self.assertEqual(mixed["verdict"], "ok")
        self.assertEqual(mixed["next_step"], "")
        self.assertIn("average brightness", mixed["detail"])

    def test_obstruction_proxy_boundaries(self):
        # A covered lens looks like: dark samples that barely change.
        covered = [30.0, 30.0, 30.0, 30.0, 235.0, 235.0]
        self.assertEqual(obstruction_dimension(covered, 6.0)["verdict"], "unsuitable")
        borderline = [235.0, 30.0, 30.0, 235.0, 235.0, 235.0]
        self.assertEqual(obstruction_dimension(borderline, 6.0)["verdict"], "review")
        # Dark but changing (a real night scene with movement) is not flagged.
        noisy_night = [30.0, 34.0, 28.0, 36.0, 29.0, 33.0]
        self.assertEqual(obstruction_dimension(noisy_night, 6.0)["verdict"], "ok")
        bright = [235.0] * 6
        self.assertEqual(obstruction_dimension(bright, 6.0)["verdict"], "ok")
        # The proxy says it is a proxy, in plain words.
        self.assertIn("proxy, not a detection", obstruction_dimension(covered, 6.0)["detail"])

    def test_object_size_boundaries_and_that_it_is_never_measured(self):
        none = object_size_dimension(None)
        self.assertEqual(none["verdict"], "review")
        self.assertIn("cannot measure", none["detail"])
        self.assertIn("guidance", none["next_step"])

        class _Estimate:
            person_height_percent = 50

        self.assertEqual(object_size_dimension(_Estimate())["verdict"], "ok")
        small = _Estimate()
        small.person_height_percent = 20
        self.assertEqual(object_size_dimension(small)["verdict"], "review")
        tiny = _Estimate()
        tiny.person_height_percent = 10
        tiny_verdict = object_size_dimension(tiny)
        self.assertEqual(tiny_verdict["verdict"], "unsuitable")
        self.assertIn("entered by you", tiny_verdict["detail"])
        self.assertIn("installer", tiny_verdict["next_step"])


# -- the assembled health of one recording -----------------------------------------------------------
class RecordingHealthTests(ViewHealthBase):
    def test_a_clean_day_clip_assembles_every_dimension(self):
        body = make_clip(self.tmp, seconds=2, brightness="bright")
        recording = self.make_recording(body)
        health = recording_health(recording)
        by_id = {dimension["id"]: dimension for dimension in health["dimensions"]}
        self.assertEqual(
            set(by_id), {"coverage", "day_night", "timeline", "obstruction", "object_size"}
        )
        self.assertEqual(health["verdict"], "review")  # no dark samples, no estimate
        self.assertEqual(health["measurements"]["frame_count"], 10)
        self.assertEqual(health["measurements"]["gap_count"], 0)
        self.assertGreaterEqual(health["measurements"]["brightness_sample_count"], 2)
        self.assertIn("dark_sample_count", health["measurements"])

    def test_a_mixed_clip_with_a_good_estimate_is_fully_ok(self):
        body = make_clip(self.tmp, seconds=4, brightness="mixed")
        recording = self.make_recording(body, duration=4.0)
        estimate = SourceViewEstimate.objects.create(
            source=self.source, created_by=self.user, person_height_percent=60
        )
        health = recording_health(recording, estimate=estimate)
        self.assertEqual(health["verdict"], "ok")
        self.assertEqual(health["unsuitable"], [])
        self.assertGreaterEqual(health["measurements"]["dark_sample_count"], DARK_SAMPLE_MINIMUM)

    def test_a_changed_file_is_refused_not_halved(self):
        body = make_clip(self.tmp, seconds=1, brightness="bright")
        recording = self.make_recording(body)
        # Corrupt the stored bytes so the digest no longer matches.
        with default_storage.open(recording.storage_key, "wb") as stored:
            stored.write(b"changed on disk")
        with self.assertRaises(ViewHealthError) as raised:
            recording_health(recording)
        self.assertIn("changed on disk", str(raised.exception))


# -- plain refusals and the API shape -----------------------------------------------------------------
class ViewHealthAPITests(ViewHealthBase):
    def test_no_recording_is_a_plain_record_a_test_clip_answer(self):
        answer = plain_recording_missing()
        self.assertEqual(answer["verdict"], "review")
        self.assertEqual(answer["measurements"], None)
        self.assertEqual(len(answer["dimensions"]), 1)
        self.assertIn("record", answer["dimensions"][0]["next_step"].lower())

        response = self.client.get(f"/api/operation-sources/{self.source.id}/recordings")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["recordings"], [])
        self.assertEqual(body["view_health"]["dimensions"][0]["next_step"],
                         answer["dimensions"][0]["next_step"])
        self.assertIsNone(body["object_size_estimate"])

    def test_the_api_shape_follows_the_readiness_card_pattern(self):
        body = make_clip(self.tmp, seconds=2, brightness="bright")
        self.make_recording(body)
        response = self.client.get(f"/api/operation-sources/{self.source.id}/recordings")
        self.assertEqual(response.status_code, 200, response.content)
        health = response.json()["view_health"]
        for key in ("recording_id", "recording_sha256", "verdict", "dimensions", "unsuitable", "measurements"):
            self.assertIn(key, health)
        for dimension in health["dimensions"]:
            self.assertEqual(
                set(dimension), {"id", "title", "verdict", "detail", "next_step"}, dimension
            )
            self.assertIn(dimension["verdict"], ("ok", "review", "unsuitable"))
            # Review and unsuitable always name one next step; ok may rest.
            if dimension["verdict"] != "ok":
                self.assertTrue(dimension["next_step"], dimension)
        self.assertEqual(len(health["recording_sha256"]), 64)

    def test_another_users_source_is_not_visible(self):
        stranger = get_user_model().objects.create_user("t12-stranger")
        stranger_client = self.client.__class__()
        stranger_client.force_login(stranger)
        self.assertEqual(
            stranger_client.get(f"/api/operation-sources/{self.source.id}/recordings").status_code, 404
        )


class EstimateAPITests(ViewHealthBase):
    def test_estimate_round_trip_persistence_and_replacement(self):
        url = f"/api/operation-sources/{self.source.id}/view-estimate"
        guidance = self.client.get(url)
        self.assertEqual(guidance.status_code, 200, guidance.content)
        self.assertIn("cannot measure", guidance.json()["guidance"])
        self.assertIsNone(guidance.json()["object_size_estimate"])

        saved = self.client.put(url, json.dumps({"person_height_percent": 55}),
                                content_type="application/json")
        self.assertEqual(saved.status_code, 201, saved.content)
        self.assertEqual(saved.json()["object_size_estimate"]["person_height_percent"], 55.0)
        self.assertEqual(SourceViewEstimate.objects.get().person_height_percent, 55.0)

        replaced = self.client.put(url, json.dumps({"person_height_percent": 70, "notes": "winter coats"}),
                                   content_type="application/json")
        self.assertEqual(replaced.status_code, 201, replaced.content)
        self.assertEqual(SourceViewEstimate.objects.count(), 1)
        self.assertEqual(SourceViewEstimate.objects.get().person_height_percent, 70.0)
        self.assertEqual(SourceViewEstimate.objects.get().notes, "winter coats")

        cleared = self.client.delete(url)
        self.assertEqual(cleared.status_code, 200, cleared.content)
        self.assertIsNone(cleared.json()["object_size_estimate"])
        self.assertFalse(SourceViewEstimate.objects.exists())

    def test_estimate_validation_refuses_plainly(self):
        url = f"/api/operation-sources/{self.source.id}/view-estimate"
        cases = [
            {"person_height_percent": 0},
            {"person_height_percent": 101},
            {"person_height_percent": -5},
            {"person_height_percent": "half"},
            {"person_height_percent": True},
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                response = self.client.put(url, json.dumps(payload), content_type="application/json")
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn("error", response.json())
        self.assertFalse(SourceViewEstimate.objects.exists())
        missing = self.client.put(url, json.dumps({"notes": "no number"}),
                                  content_type="application/json")
        self.assertEqual(missing.status_code, 400)
        unknown = self.client.put(url, json.dumps({"person_height_percent": 50, "surprise": 1}),
                                  content_type="application/json")
        self.assertEqual(unknown.status_code, 400)

    def test_estimate_feeds_the_view_health_verdict(self):
        body = make_clip(self.tmp, seconds=2, brightness="bright")
        self.make_recording(body)
        url = f"/api/operation-sources/{self.source.id}/view-estimate"
        self.client.put(url, json.dumps({"person_height_percent": 60}), content_type="application/json")
        health = self.client.get(f"/api/operation-sources/{self.source.id}/recordings").json()["view_health"]
        object_size = next(d for d in health["dimensions"] if d["id"] == "object_size")
        self.assertEqual(object_size["verdict"], "ok")
        self.assertIn("60", object_size["detail"])
        self.assertIn("entered by you", object_size["detail"])
