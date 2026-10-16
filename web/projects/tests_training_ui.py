"""Server-side checks for the plain-language "Teach the computer" panel.

The browser behaviour (readiness, a real training run, progress, results, trial, approval, rollback,
keyboard use, four widths, axe) is verified in a real Chromium run against a real train_worker. These tests
pin what that run cannot keep honest on every commit: the markup the script depends on, the wording rules,
the safety wording around approval, the data the checklist and the panel read, and the exact sentences the
server produces that the script re-words.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.urls import NoReverseMatch, reverse

from .tests_dataset_ui import CASE_SENSITIVE, JARGON, STATIC, user_facing_strings, visible_text
from .tests_training import TrainingBase
from .training import evaluation as ev

SECTION_START = "// --- Teach the computer (training, results and approval)"
SECTION_END = "// --- end of teaching ---"

# Words an operator should not have to learn, on top of the training-set rules.
TRAINING_JARGON = JARGON + (
    r"precision", r"recall", r"\bIoU\b", r"\bAP50\b", r"\bmAP", r"backend", r"threshold", r"inference",
    r"artifact", r"checkpoint", r"\bbatch\b", r"learning rate", r"\bGPU\b", r"\btensor", r"\bstaged?\b",
    r"\btrain_worker\b(?<!manage\.py train_worker)",
)
TRAINING_CASE_SENSITIVE = CASE_SENSITIVE | {r"\bIoU\b", r"\bAP50\b", r"\bGPU\b", r"\bmAP"}


def script_section() -> str:
    js = (STATIC / "console.js").read_text()
    return js[js.index(SECTION_START) : js.index(SECTION_END)]


def panel_and_dialog_html(html: str) -> str:
    panel = html[html.index('id="train-panel"') : html.index("</section>\n      </section>", html.index('id="train-panel"'))]
    dialog = html[html.index('<dialog id="decision-dialog"') : html.index("</dialog>", html.index('<dialog id="decision-dialog"'))]
    return panel + dialog


class TeachPanelMarkupTests(TrainingBase):
    def setUp(self) -> None:
        super().setUp()
        self.html = self.client.get("/").content.decode()

    def test_panel_has_every_control_the_script_needs(self) -> None:
        for element_id in (
            "train-panel", "train-heading", "tr-headline", "tr-facts", "tr-licence", "tr-form", "tr-snapshot", "tr-tool",
            "tr-advanced", "tr-rounds", "tr-batch", "tr-size", "tr-reset", "tr-error", "tr-start", "tr-start-help",
            "tr-run", "tr-run-card", "tr-bar", "tr-cancel", "tr-locked", "tr-log", "tr-announce", "tr-model-list",
            "tr-models-status", "tr-history", "decision-dialog", "decision-form", "decision-note", "decision-confirm",
            "decision-cancel", "decision-close", "decision-error",
        ):
            with self.subTest(element_id=element_id):
                self.assertIn(f'id="{element_id}"', self.html)

    def test_panel_is_hidden_until_a_picture_project_is_open(self) -> None:
        self.assertRegex(self.html, r'<section class="panel train-panel" id="train-panel"[^>]*\shidden>')
        self.assertRegex(self.html, r'<section class="train-step" id="tr-run"[^>]*\shidden>')

    def test_advanced_settings_are_collapsed_with_safe_limits(self) -> None:
        details = re.search(r'<details class="dataset-advanced" id="tr-advanced"([^>]*)>', self.html)
        self.assertIsNotNone(details)
        self.assertNotIn("open", details.group(1))
        self.assertRegex(self.html, r'id="tr-rounds" type="number"[^>]*min="1" max="100"')
        self.assertRegex(self.html, r'id="tr-batch" type="number"[^>]*min="1" max="16"')

    def test_progress_and_errors_are_announced_to_assistive_technology(self) -> None:
        self.assertRegex(self.html, r'id="tr-headline" role="status" aria-live="polite"')
        self.assertRegex(self.html, r'id="tr-announce" role="status" aria-live="polite"')
        self.assertRegex(self.html, r'id="tr-models-status" role="status" aria-live="polite"')
        self.assertRegex(self.html, r'id="tr-error" role="alert"')
        self.assertRegex(self.html, r'id="decision-error" role="alert"')
        self.assertRegex(self.html, r'<progress id="tr-bar"[^>]*aria-labelledby=')
        for key in ("rounds", "batch", "size"):
            self.assertRegex(self.html, rf'id="tr-{key}-msg" aria-live="polite"')

    def test_decision_dialog_is_a_labelled_modal_with_a_note_field(self) -> None:
        self.assertRegex(self.html, r'<dialog id="decision-dialog" aria-labelledby="decision-title" aria-describedby="decision-lead"')
        self.assertRegex(self.html, r'<textarea id="decision-note"[^>]*maxlength="2000"')
        self.assertIn("Cancel, change nothing", self.html)

    def test_page_says_finishing_never_approves_a_model(self) -> None:
        text = visible_text(self.html)
        self.assertIn("Finishing never switches a model on by itself", text)
        self.assertIn("A model is used only after a person approves it", text)
        self.assertNotIn("Training a model and the check screen", text)

    def test_panel_and_dialog_wording_has_no_jargon(self) -> None:
        text = visible_text(panel_and_dialog_html(self.html))
        for term in TRAINING_JARGON:
            with self.subTest(term=term):
                flags = 0 if term in TRAINING_CASE_SENSITIVE else re.I
                self.assertNotRegex(text, re.compile(term, flags))

    def test_current_operator_marker_matches_the_signed_in_user(self) -> None:
        self.assertIn(f'data-me="u{self.user.pk}"', self.html)


class TeachScriptWordingTests(TrainingBase):
    def test_section_markers_exist(self) -> None:
        section = script_section()
        for name in ("trStart", "trPoll", "trFillResults", "trDecisionConfig", "trLoadHistory"):
            self.assertIn(name, section)

    def test_sentences_the_script_shows_have_no_jargon(self) -> None:
        strings = user_facing_strings(script_section())
        self.assertGreater(len(strings), 80)
        for term in TRAINING_JARGON:
            flags = 0 if term in TRAINING_CASE_SENSITIVE else re.I
            pattern = re.compile(term, flags)
            for sentence in strings:
                if "manage.py train_worker" in sentence:
                    continue  # the one command an administrator types
                with self.subTest(term=term, sentence=sentence[:50]):
                    self.assertIsNone(pattern.search(sentence), sentence)

    def test_script_never_uses_innerhtml(self) -> None:
        self.assertNotRegex(script_section(), r"innerHTML|insertAdjacentHTML|outerHTML|document\.write")

    def test_stage_badges_have_matching_words_and_colours(self) -> None:
        section = script_section()
        for stage, word in (("candidate", "Candidate"), ("staged", "Ready for trial"), ("approved", "Approved"), ("retired", "Retired")):
            with self.subTest(stage=stage):
                self.assertRegex(section, rf'{stage}: \{{ label: "{word}"')
                css = (STATIC / "console.css").read_text()
                self.assertRegex(css, rf'\.stage-badge\[data-stage="{stage}"\] \{{[^}}]*color:')
                self.assertRegex(css, rf'\.model-card\[data-stage="{stage}"\] \{{')

    def test_approval_wording_protects_the_previous_model_and_everything_else(self) -> None:
        section = script_section()
        for sentence in (
            "will be marked Retired. It is not deleted",
            "brings it back",
            "Approving changes nothing else",
            "keep using the exact model they were set up with",
            "(required)",
            "Go back to previous model",
            "Move to trial",
            "Approve for use",
        ):
            with self.subTest(sentence=sentence):
                self.assertIn(sentence, section)

    def test_approve_button_needs_a_note_and_trial_needs_results(self) -> None:
        section = script_section()
        self.assertIn("trDialogConfirm.disabled = config.required", section)
        self.assertIn("const canStage = Boolean(model.evaluation?.available);", section)
        self.assertIn("A note is required", section)

    def test_results_are_worded_with_limits_and_no_guarantee(self) -> None:
        section = script_section()
        self.assertIn("They are not a guarantee about real-world use", section)
        self.assertIn("Limits: how far to trust this", section)
        self.assertIn("Results are not available for", section)
        self.assertIn("cannot be moved to trial or approved", section)

    def test_script_does_not_show_the_servers_own_report_text(self) -> None:
        # The server's report text uses precision/recall/IoU; the panel words the numbers itself.
        self.assertNotIn("report.text", script_section())

    def test_server_limit_sentences_still_match_what_the_script_rewords(self) -> None:
        limits = ev.data_limits(5, {"knife": 3, "laptop": 0, "bottle": 40})
        self.assertTrue(any(re.match(r"^Only \d+ test image", text) for text in limits))
        self.assertTrue(any(re.match(r"""^Only (\d+) test box\(es\) for ['"](.*)['"]""", text) for text in limits))
        self.assertTrue(any(re.match(r"""^No test boxes for ['"](.*)['"]""", text) for text in limits))
        section = script_section()
        self.assertIn(r"""/^Only (\d+) test box\(es\) for ['"](.*)['"]/""", section)
        self.assertIn(r"""/^No test boxes for ['"](.*)['"]/""", section)
        self.assertIn(r"/^Only \d+ test image/", section)


class ChecklistStepSixTests(TrainingBase):
    def project_row(self) -> dict:
        rows = self.client.get("/api/projects").json()["projects"]
        return next(row for row in rows if row["id"] == str(self.project.id))

    def stage(self, model):
        return self.post(self.api(f"/models/{model.id}/stage"), {})

    def approve(self, model, note="Read the report."):
        return self.post(self.api(f"/models/{model.id}/approve"), {"note": note})

    def test_a_new_project_has_no_approved_model(self) -> None:
        self.assertEqual(self.project_row()["approved_model_count"], 0)

    def test_training_alone_never_ticks_step_six(self) -> None:
        self.train_candidate()
        self.assertEqual(self.project_row()["approved_model_count"], 0)

    def test_moving_to_trial_does_not_tick_step_six(self) -> None:
        model = self.train_candidate()
        self.assertEqual(self.stage(model).status_code, 200)
        self.assertEqual(self.project_row()["approved_model_count"], 0)

    def test_approval_ticks_it_and_replacement_keeps_it_at_one(self) -> None:
        first = self.train_candidate()
        self.stage(first)
        self.assertEqual(self.approve(first).status_code, 200)
        self.assertEqual(self.project_row()["approved_model_count"], 1)
        second = self.train_candidate()
        self.stage(second)
        self.assertEqual(self.approve(second).status_code, 200)
        self.assertEqual(self.project_row()["approved_model_count"], 1)

    def test_rollback_keeps_exactly_one_approved_model(self) -> None:
        first = self.train_candidate()
        self.stage(first)
        self.approve(first)
        second = self.train_candidate()
        self.stage(second)
        self.approve(second)
        self.assertEqual(self.post(self.api(f"/models/{second.id}/rollback"), {"note": "Worse on night shift."}).status_code, 200)
        self.assertEqual(self.project_row()["approved_model_count"], 1)

    def test_script_ticks_step_six_only_for_an_approved_model(self) -> None:
        js = (STATIC / "console.js").read_text()
        self.assertIn('state: approvedModels ? "done" : snapshots ? "todo" : "blocked"', js)
        self.assertIn("const approvedModels = project.approved_model_count || 0;", js)

    def test_step_seven_is_a_link_only_when_the_check_screen_exists(self) -> None:
        html = self.client.get("/").content.decode()
        js = (STATIC / "console.js").read_text()
        self.assertIn('const template = checklistEl.dataset.checkUrlTemplate || "";', js)
        self.assertIn("Coming soon. Not available yet; this step is planned for a later version.", js)
        try:
            url = reverse("checkscreen-setup", args=["00000000-0000-0000-0000-000000000000"])
        except NoReverseMatch:
            url = ""
        self.assertIn(f'data-check-url-template="{url}"', html)
        if url:
            self.assertIn("/check-screen/setup", url)
            self.assertIn("Set up the check screen", js)
            self.assertNotIn("The check screen</strong><p>A screen where people check what an approved model finds is not available yet.", html)


class PanelDataContractTests(TrainingBase):
    """The panel reads these exact fields; a rename on the server must fail a test, not the operator."""

    def test_backends_and_readiness_carry_what_the_readiness_card_shows(self) -> None:
        backends = self.client.get(self.api("/training/backends")).json()
        self.assertIn("default", backends)
        self.assertEqual(set(backends["worker"]), {"online", "loading"})
        tool = next(item for item in backends["backends"] if item["name"] == "fake-backend")
        for key in ("license_note", "default_config", "available_in_web_process", "worker_reports_available"):
            self.assertIn(key, tool)
        self.make_dataset()
        readiness = self.client.get(self.api("/training/readiness?backend=fake-backend")).json()
        for key in ("ready", "worker", "backend", "dataset", "active_run_id"):
            self.assertIn(key, readiness)
        self.assertIn("test", readiness["dataset"]["split_counts"])

    def test_run_and_model_payloads_carry_what_the_cards_show(self) -> None:
        model = self.train_candidate()
        run = self.client.get(self.api("/training/runs")).json()["runs"][0]
        for key in ("status", "progress", "error", "config", "dataset", "created_at", "model_version_ids", "cancel_requested"):
            self.assertIn(key, run)
        listed = self.client.get(self.api("/models")).json()
        self.assertEqual(listed["models"][0]["id"], str(model.id))
        card = listed["models"][0]
        for key in ("version", "stage", "evaluation", "lineage", "previous_approved_id", "in_use_by_apps", "staged_by", "approved_by", "approval_note"):
            self.assertIn(key, card)
        self.assertIn("dataset_version", card["lineage"])
        detail = self.client.get(self.api(f"/models/{model.id}")).json()["model"]
        self.assertTrue(detail["events"])
        for key in ("seq", "kind", "by", "at", "note"):
            self.assertIn(key, detail["events"][0])
        self.assertEqual(detail["events"][0]["kind"], "candidate_created")

    def test_evaluation_report_carries_every_number_the_results_view_reads(self) -> None:
        model = self.train_candidate()
        body = self.client.get(self.api(f"/models/{model.id}/evaluation")).json()
        self.assertTrue(body["report"]["available"])
        numbers = body["report"]["numbers"]
        for key in ("data", "overall", "per_label", "confidence_threshold", "limits"):
            self.assertIn(key, numbers)
        for key in ("true_positives", "false_positives", "false_negatives", "precision", "recall"):
            self.assertIn(key, numbers["overall"])
        row = next(iter(numbers["per_label"].values()))
        for key in ("ground_truth_boxes", "true_positives", "false_positives", "false_negatives", "precision", "recall"):
            self.assertIn(key, row)
        self.assertIn("lineage", body["model"])
        self.assertIn("artifact_sha256", body["model"])

    def test_empty_held_out_group_gives_the_reason_the_panel_rewords(self) -> None:
        model = self.train_candidate(self.make_dataset(with_test=False))
        body = self.client.get(self.api(f"/models/{model.id}/evaluation")).json()
        self.assertFalse(body["report"]["available"])
        self.assertFalse(body["can_stage"])
        self.assertRegex(body["report"]["numbers"]["reason"], r"test split has no images")
        self.assertIn("test split has no images", (Path(STATIC / "console.js")).read_text())
        refused = self.post(self.api(f"/models/{model.id}/stage"), {})
        self.assertEqual(refused.status_code, 422)

    def test_event_kinds_the_history_names_are_the_ones_the_server_writes(self) -> None:
        first = self.train_candidate()
        self.post(self.api(f"/models/{first.id}/stage"), {})
        self.post(self.api(f"/models/{first.id}/approve"), {"note": "ok"})
        second = self.train_candidate()
        self.post(self.api(f"/models/{second.id}/stage"), {})
        self.post(self.api(f"/models/{second.id}/approve"), {"note": "better"})
        self.post(self.api(f"/models/{second.id}/rollback"), {"note": "worse"})
        from .models import ModelEvent

        kinds = set(ModelEvent.objects.exclude(model_version=None).values_list("kind", flat=True))
        section = script_section()
        for kind in kinds:
            with self.subTest(kind=kind):
                self.assertIn(f'case "{kind}"', section)
