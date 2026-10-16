"""Server-side checks for the plain-language "Make a training set" panel.

The browser behaviour (readiness, snapshots, downloads, accessibility, four widths) is verified in a
real Chromium run; these tests pin the things that run cannot keep honest on every commit: the panel
markup, the wording rules, the data the checklist depends on, and the step order.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.test import TestCase

from .tests_datasets import DatasetBase

STATIC = Path(__file__).resolve().parent / "static" / "projects"
SECTION_START = "// --- Training sets (snapshots)"
SECTION_END = "// --- end of training sets ---"

# Words an operator should not have to learn. COCO and YOLO are allowed: they are the two format names.
JARGON = (
    r"modalit", r"bounding", r"\bSAM\b", r"\bCLIP\b", r"\basset", r"\bjob\b", r"confidence",
    r"SHA-?256", r"ground truth", r"\bplugin", r"\bworker\b", r"manifest", r"checksum", r"\bhash",
    r"\bsplit", r"\bseed\b", r"\bAPI\b", r"\bJSON\b", r"\bdataset", r"\bepoch", r"annotation",
    r"\bschema", r"\bUUID",
)
CASE_SENSITIVE = {r"\bSAM\b", r"\bCLIP\b", r"\bAPI\b", r"\bJSON\b", r"\bUUID"}


def visible_text(html: str) -> str:
    html = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S)
    return re.sub(r"<[^>]+>", " ", html)


def panel_html(html: str) -> str:
    start = html.index('id="dataset-panel"')
    return html[start : html.index('id="train-panel"', start)]


def training_section() -> str:
    js = (STATIC / "console.js").read_text()
    return js[js.index(SECTION_START) : js.index(SECTION_END)]


def user_facing_strings(source: str) -> list[str]:
    """Quoted and template strings with at least two words: the sentences a person reads."""
    found = re.findall(r'"((?:[^"\\\n]|\\.)*)"|`((?:[^`\\]|\\.)*)`', source)
    strings = []
    for plain, template in found:
        text = re.sub(r"\$\{[^}]*\}", " ", plain or template)
        if text.lstrip().startswith(("/", ".", "#")) or "=" in text:  # URLs, selectors, attributes
            continue
        if len(re.findall(r"[A-Za-z]{2,}", text)) >= 2 and " " in text.strip():
            strings.append(text)
    return strings


class DatasetPanelMarkupTests(TestCase):
    def setUp(self) -> None:
        from django.contrib.auth import get_user_model

        self.client.force_login(get_user_model().objects.create_user("operator"))
        self.html = self.client.get("/").content.decode()

    def test_panel_has_every_control_the_script_needs(self) -> None:
        for element_id in (
            "dataset-panel", "dataset-heading", "dataset-headline", "dataset-facts", "dataset-problem-list",
            "dataset-recheck", "dataset-form", "dataset-advanced", "split-train", "split-val", "split-test",
            "split-seed", "split-total", "split-reset", "dataset-save", "dataset-error", "snapshot-list",
            "snapshots-status", "dataset-save-help",
        ):
            with self.subTest(element_id=element_id):
                self.assertIn(f'id="{element_id}"', self.html)

    def test_panel_is_hidden_until_a_picture_project_is_open(self) -> None:
        self.assertRegex(self.html, r'<section class="panel dataset-panel" id="dataset-panel"[^>]*\shidden>')

    def test_advanced_split_controls_are_collapsed_with_80_10_10_defaults(self) -> None:
        details = re.search(r'<details class="dataset-advanced" id="dataset-advanced"([^>]*)>', self.html)
        self.assertIsNotNone(details)
        self.assertNotIn("open", details.group(1))
        for field, value in (("split-train", "80"), ("split-val", "10"), ("split-test", "10"), ("split-seed", "0")):
            with self.subTest(field=field):
                self.assertRegex(self.html, rf'id="{field}"[^>]*value="{value}"')

    def test_inputs_are_labelled_and_status_regions_are_live(self) -> None:
        for field in ("split-train", "split-val", "split-test", "split-seed"):
            with self.subTest(field=field):
                self.assertRegex(self.html, rf'<label for="{field}">')
        self.assertRegex(self.html, r'id="dataset-headline" role="status" aria-live="polite"')
        self.assertRegex(self.html, r'id="split-total" role="status" aria-live="polite"')
        self.assertRegex(self.html, r'id="snapshots-status" role="status" aria-live="polite"')
        self.assertRegex(self.html, r'id="dataset-error" role="alert"')

    def test_panel_explains_that_a_snapshot_never_changes(self) -> None:
        text = visible_text(panel_html(self.html))
        self.assertRegex(text, r"frozen copy that never changes")
        self.assertRegex(text, r"next snapshot")
        self.assertRegex(text, r"Needs your review")
        self.assertRegex(text, r"does not check whether you have the right")

    def test_page_text_has_no_jargon_and_the_old_unavailable_claim_is_gone(self) -> None:
        text = visible_text(self.html)
        for term in JARGON:
            with self.subTest(term=term):
                flags = 0 if term in CASE_SENSITIVE else re.I
                self.assertNotRegex(text, re.compile(term, flags))
        self.assertNotIn("dataset export", text)
        self.assertNotIn("Export and training", text)
        self.assertRegex(text, r"Training sets")
        self.assertRegex(text, r"Coming soon")


class DatasetScriptWordingTests(TestCase):
    def test_section_markers_exist(self) -> None:
        section = training_section()
        self.assertIn("downloadSnapshot", section)
        self.assertIn("describeProblem", section)

    def test_sentences_the_script_shows_have_no_jargon(self) -> None:
        strings = user_facing_strings(training_section())
        self.assertGreater(len(strings), 40)
        for term in JARGON:
            flags = 0 if term in CASE_SENSITIVE else re.I
            pattern = re.compile(term, flags)
            for sentence in strings:
                with self.subTest(term=term, sentence=sentence[:50]):
                    self.assertIsNone(pattern.search(sentence), sentence)

    def test_download_formats_are_plain_choices_for_both_exports(self) -> None:
        section = training_section()
        self.assertIn("COCO — most tools", section)
        self.assertIn("YOLO — YOLO-style tools", section)
        self.assertIn("Download for training", section)
        self.assertIn("/export?format=", section)

    def test_export_errors_have_plain_messages(self) -> None:
        section = training_section()
        for text in ("cannot be downloaded because some of its pictures are missing", "too big to download", "was not saved because some things need fixing"):
            with self.subTest(text=text):
                self.assertIn(text, section)

    def test_every_problem_code_has_a_fix_in_plain_words(self) -> None:
        section = training_section()
        for code in (
            "file_missing", "hash_mismatch", "label_not_in_project", "geometry_out_of_range",
            "non_positive_size", "label_unsupported_characters", "too_many_images",
        ):
            with self.subTest(code=code):
                self.assertIn(f'case "{code}"', section)
        self.assertIn("Fix:", section)

    def test_script_builds_the_panel_without_innerhtml(self) -> None:
        self.assertNotIn("innerHTML", training_section())


class ChecklistStepFiveTests(DatasetBase):
    """Step 5 ticks only from ``dataset_count``; steps 6 and 7 stay "Coming soon"."""

    def project_row(self) -> dict:
        rows = self.client.get("/api/projects").json()["projects"]
        return next(row for row in rows if row["id"] == str(self.project.id))

    def test_project_reports_zero_snapshots_until_one_is_saved(self) -> None:
        self.labelled_asset()
        self.assertEqual(self.project_row()["dataset_count"], 0)
        self.assertEqual(self.create_version().status_code, 201)
        self.assertEqual(self.project_row()["dataset_count"], 1)
        self.assertEqual(self.create_version().status_code, 201)
        self.assertEqual(self.project_row()["dataset_count"], 2)

    def test_a_refused_snapshot_does_not_count(self) -> None:
        self.make_asset()  # no accepted boxes
        self.assertEqual(self.create_version().status_code, 400)
        self.assertEqual(self.project_row()["dataset_count"], 0)

    def test_suggested_boxes_alone_do_not_tick_step_five(self) -> None:
        self.make_suggestion(self.make_asset())
        self.assertEqual(self.create_version().status_code, 400)
        self.assertEqual(self.project_row()["dataset_count"], 0)

    def test_new_and_edited_projects_report_dataset_count(self) -> None:
        created = self.client.post(
            "/api/projects",
            data='{"name": "Fresh", "modality": "image", "task": "Find items", "labels": ["a"]}',
            content_type="application/json",
        ).json()["project"]
        self.assertEqual(created["dataset_count"], 0)

    def test_checklist_script_ties_step_five_to_snapshots_and_step_six_to_approval(self) -> None:
        js = (STATIC / "console.js").read_text()
        self.assertIn('title: "Make a training set"', js)
        self.assertIn('state: snapshots ? "done" : accepted ? "todo" : "blocked"', js)
        # Step 6 ticks only when a person has approved a model; step 7 stays "Coming soon" unless a check screen exists.
        self.assertIn('state: approvedModels ? "done" : snapshots ? "todo" : "blocked"', js)
        self.assertRegex(js, r'title: "Set up the check screen", state: "soon"')
        self.assertIn("steps done", js)
        self.assertNotIn("Export your labelled pictures or train a model", js)
