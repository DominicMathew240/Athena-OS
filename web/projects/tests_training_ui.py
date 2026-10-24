"""Server-side checks for the plain-language "Teach the computer" panel.

The browser behaviour (readiness, a real training run, progress, results, trial, approval, rollback,
keyboard use, four widths, axe) is verified in a real Chromium run against a real train_worker. These tests
pin what that run cannot keep honest on every commit: the markup the script depends on, the wording rules,
the safety wording around approval, the data the checklist and the panel read, and the exact sentences the
server produces that the script re-words.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from unittest import skipUnless

from django.urls import NoReverseMatch, reverse

from .models import ModelVersion
from .tests_dataset_ui import CASE_SENSITIVE, JARGON, STATIC, user_facing_strings, visible_text
from .tests_results_plain import synthetic_evaluation
from .tests_training import TrainingBase
from .tests_training_auto import AutoBase, BACKEND
from .training import evaluation as ev, report as training_report

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
            # the one-button flow and the administrator fold
            "tr-quality", "tr-quality-quick", "tr-quality-better", "tr-quality-note", "tr-ack-box", "tr-ack", "tr-ack-body",
            "tr-recheck", "tr-admin", "tr-admin-note", "tr-admin-fields", "tr-exact-form", "tr-start-exact", "tr-exact-help",
            "tr-exact-error", "tr-start-from", "tr-pre-ack",
            # the administrator steps under the button, the "Check again" answer and the lost-run button
            "tr-steps", "tr-recheck-note", "tr-run-again", "annotation-mark-note",
        ):
            with self.subTest(element_id=element_id):
                self.assertIn(f'id="{element_id}"', self.html)

    def test_panel_is_hidden_until_a_picture_project_is_open(self) -> None:
        self.assertRegex(self.html, r'<section class="panel train-panel" id="train-panel"[^>]*\shidden>')
        self.assertRegex(self.html, r'<section class="train-step" id="tr-run"[^>]*\shidden>')

    def test_advanced_settings_are_collapsed_with_safe_limits(self) -> None:
        details = re.search(r'<details class="admin-fold train-admin" id="tr-admin"([^>]*)>', self.html)
        self.assertIsNotNone(details)
        self.assertNotIn("open", details.group(1))
        fold = self.fold()
        for element_id in ("tr-rounds", "tr-batch", "tr-snapshot", "tr-tool", "tr-start-from", "tr-reset", "tr-licence"):
            with self.subTest(element_id=element_id):
                self.assertIn(f'id="{element_id}"', fold)
        self.assertRegex(self.html, r'id="tr-rounds" type="number"[^>]*min="1" max="100"')
        self.assertRegex(self.html, r'id="tr-batch" type="number"[^>]*min="1" max="16"')

    def fold(self) -> str:
        """The markup of the one closed administrator fold under the Teach button."""
        start = self.html.index('id="tr-admin"')
        return self.html[start : self.html.index('<section class="train-step" id="tr-run"', start)]

    def part_above_the_fold(self) -> str:
        panel = panel_and_dialog_html(self.html)
        return panel[: panel.index('<details class="admin-fold train-admin" id="tr-admin"')] + panel[panel.index('<section class="train-step" id="tr-run"') :]

    def test_there_is_one_yellow_button_and_it_teaches(self) -> None:
        panel = panel_and_dialog_html(self.html)
        primaries = re.findall(r'<button class="primary"[^>]*>([^<]*)</button>', panel[: panel.index('id="decision-dialog"')])
        self.assertEqual(primaries, ["Teach the computer"])
        self.assertRegex(self.html, r'<button class="primary" id="tr-start" type="submit" aria-describedby="tr-start-help">Teach the computer</button>')

    def test_the_old_form_is_gone_from_the_part(self) -> None:
        text = visible_text(self.part_above_the_fold())
        for gone in ("Most people can leave this closed", "Start teaching", "Which snapshot should it learn from", "Which teaching tool", "Is everything ready?"):
            with self.subTest(gone=gone):
                self.assertNotIn(gone, text)
        # the four-step "it always goes in this order" list was replaced by one sentence
        self.assertNotIn('class="train-flow"', self.html)

    def test_snapshot_is_only_named_inside_the_administrator_fold(self) -> None:
        # The first thing a person reads about it is "a saved copy of your labelled pictures" (the plan sentence, in
        # the script); the word itself stays out of the visible Teach text, except in the fold.
        self.assertNotRegex(visible_text(self.part_above_the_fold()), r"(?i)snapshot")
        self.assertRegex(visible_text(self.fold()), r"(?i)snapshot")

    def test_quick_better_is_a_plain_two_option_choice_and_the_tick_starts_hidden(self) -> None:
        self.assertRegex(self.html, r'<fieldset class="train-quality" id="tr-quality" hidden>\s*<legend>How long should it learn\?</legend>')
        self.assertRegex(self.html, r'<input id="tr-quality-quick" type="radio" name="tr-quality" value="quick" checked')
        self.assertRegex(self.html, r'<input id="tr-quality-better" type="radio" name="tr-quality" value="better"')
        self.assertRegex(self.html, r'<div class="train-ack" id="tr-ack-box" hidden>')
        self.assertRegex(self.html, r'<input id="tr-ack" type="checkbox" autocomplete="off">')
        self.assertIn("<summary>Read the details</summary>", self.html)

    def test_the_tick_is_one_sentence_and_the_licence_text_sits_in_the_script_not_the_page(self) -> None:
        self.assertIn("This ready-made model has its own terms of use. I accept them, and I will ask a lawyer before using the result commercially.", self.html)
        section = script_section()
        self.assertIn("The teaching tool’s own licence note, word for word", section)
        self.assertIn("Ask a lawyer before commercial use.", section)
        self.assertIn("licence_plain", section)

    def test_a_browser_may_not_restore_a_tick_or_a_choice_the_page_has_reset(self) -> None:
        for element_id in ("tr-quality-quick", "tr-quality-better", "tr-ack", "tr-pre-none", "tr-pre-imagenet_backbone", "tr-pre-coco", "tr-pre-ack"):
            with self.subTest(element_id=element_id):
                self.assertRegex(self.html, rf'<input id="{element_id}"[^>]*autocomplete="off"')

    def test_the_reason_under_the_button_is_read_out_and_the_administrator_steps_say_where_to_run_them(self) -> None:
        self.assertRegex(self.html, r'<p class="train-plan" id="tr-start-help" aria-live="polite">')
        self.assertRegex(self.html, r'<button class="primary" id="tr-start" type="submit" aria-describedby="tr-start-help">')
        steps = self.html[self.html.index('id="tr-steps"') : self.html.index('id="tr-recheck"')]
        self.assertIn("Show the administrator steps", steps)
        self.assertIn("Run on the computer that hosts Athena, not in this browser.", steps)
        self.assertIn("web/DEPLOYMENT.md", steps)
        self.assertRegex(self.html[: self.html.index('id="tr-steps"')], r'id="tr-start-help"')  # under the button, not above it
        self.assertRegex(self.html, r'id="tr-steps"[^>]*\shidden>')  # nothing to say about the administrator: nothing shown
        self.assertIn("web/DEPLOYMENT.md", self.fold())  # the fold names the guide too
        self.assertRegex(self.html, r'id="tr-recheck-note" role="status" aria-live="polite"')

    def test_ids_nothing_uses_are_gone(self) -> None:
        for element_id in ("tr-ack-details", "tr-log-box"):
            with self.subTest(element_id=element_id):
                self.assertNotIn(f'id="{element_id}"', self.html)
        self.assertNotIn("trUrl", (STATIC / "console.js").read_text())
        self.assertNotIn("model-results-toggle-note", (STATIC / "console.css").read_text())

    def test_progress_and_errors_are_announced_to_assistive_technology(self) -> None:
        self.assertRegex(self.html, r'id="tr-headline" role="status" aria-live="polite"')
        self.assertRegex(self.html, r'id="tr-announce" role="status" aria-live="polite"')
        self.assertRegex(self.html, r'id="tr-models-status" role="status" aria-live="polite"')
        self.assertRegex(self.html, r'id="tr-error" role="alert"')
        self.assertRegex(self.html, r'id="tr-exact-error" role="alert"')
        self.assertEqual(self.html.count('id="tr-announce"'), 1)  # one live line for the run
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
            "approving changes nothing else",
            "keep using it",
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

    def test_the_one_click_start_sends_exactly_what_the_server_expects(self) -> None:
        section = script_section()
        self.assertIn('const payload = { snapshot: "latest", backend: tool.name, config: { quality: trQualityChoice() } };', section)
        self.assertIn("payload.acknowledge_pretrained_terms = true;", section)
        self.assertIn("/datasets/health", section)
        self.assertIn("one_click", section)
        # a double click must be one request: the busy flag is set before the first await
        self.assertRegex(section, r"if \(!one \|\| !tool \|\| trState\.busy\) return;[\s\S]*?trState\.busy = true;\s*trUpdateStart\(\);\s*try \{")

    def test_the_button_is_off_with_a_reason_when_nothing_can_start(self) -> None:
        section = script_section()
        self.assertIn("reason_if_none", section)
        self.assertIn("Also: ", section)  # a data reason and an administrator reason are both shown, data first
        self.assertIn("ready_to_teach === false", section)
        self.assertIn("trStartButton.disabled = Boolean(why) || trState.busy;", section)
        # the technical step goes in the administrator fold, never into the visible reason
        self.assertIn("how_to_enable", section)
        self.assertIn("trAdminNote.replaceChildren", section)

    def test_nothing_is_sent_for_a_project_the_page_no_longer_shows(self) -> None:
        # The behaviour is proved in a real browser (scripts/browser_smoke.mjs, "switching project"); these pin the three
        # places it lives so a refactor cannot drop one without that step noticing.
        js = (STATIC / "console.js").read_text()
        select = js[js.index("async function selectProject") : js.index("function render()")]
        self.assertIn("resetTraining(", select)
        self.assertLess(select.index("resetTraining("), select.index("void loadAssets("))  # before anything is waited for
        section = script_section()
        for name in ("trStartOneClick", "trStartExactSettings", "trCancelRun"):
            body = section[section.index(f"function {name}(") :]
            body = body[: body.index("\n}\n")]
            with self.subTest(name=name):
                self.assertIn("trIsCurrent(projectId)", body)
        self.assertIn("encodeURIComponent(projectId)}/training/runs/${encodeURIComponent(run.id)}/cancel", section)  # the stop goes where it was asked

    def test_every_verdict_carries_the_not_a_final_decision_line_from_one_constant(self) -> None:
        section = script_section()
        self.assertEqual(section.count("const TR_NOT_FINAL = "), 1)
        self.assertEqual(section.count("A person decides."), 1)  # one sentence, written once
        fill = section[section.index("function trFillResults(") :]
        self.assertGreaterEqual(fill.count("trNotFinalNode()"), 1)  # the fallback path
        self.assertIn("trNotFinalNode()", section[section.index("function trPlainBlock(") : section.index("function trFillResults(")])  # the plain path

    def test_the_plan_sentence_names_the_start_the_copy_and_the_time(self) -> None:
        section = script_section()
        for fragment in ("It will start from", "a saved copy of your labelled pictures", "on a computer like our test machine (an estimate)", "cannot be estimated"):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, section)
        self.assertIn("estimate_note", section)

    def test_the_word_snapshot_is_not_in_what_the_script_shows_outside_the_fold(self) -> None:
        # Allowed only for the administrator's own picker and the settings rows: the label "Snapshot 1 …" and "Choose another snapshot".
        allowed = ("Choose another snapshot", "snapshot (saved copy)")
        for sentence in user_facing_strings(script_section()):
            if re.search(r"(?i)snapshot", sentence) and not sentence.lstrip().startswith("Snapshot") and not any(item in sentence for item in allowed):
                self.fail(f"visible sentence still says snapshot: {sentence[:90]}")

    def test_results_show_the_plain_answer_with_a_next_step_and_the_pictures_it_got_wrong(self) -> None:
        section = script_section()
        for fragment in ("results/plain", "plain.overall.headline", "label.sentence", "Open picture", "the computer did not find this",
                         "the computer drew a box here that did not match one you drew", "Per-picture details are not stored for this model.",
                         "This picture is no longer in the project.", "openAnnotation(trState.projectId"):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, section)
        for verdict in training_report.VERDICTS:
            with self.subTest(verdict=verdict):
                self.assertRegex(section, rf"\b{verdict}: ")
        # the table of numbers is under the administrator fold, the limits are not
        self.assertIn('dsNode("details", "result-admin")', section)
        self.assertIn("Limits: how far to trust this", section)

    def test_no_claim_of_accuracy_certification_or_safety_in_what_a_person_reads(self) -> None:
        for sentence in user_facing_strings(script_section()) + [visible_text(panel_and_dialog_html(self.client.get("/").content.decode()))]:
            with self.subTest(sentence=sentence[:50]):
                self.assertNotRegex(sentence, r"(?i)\baccurat|\bcertif|\bsafe(ly)?\b")

    def test_approve_for_use_is_never_a_yellow_button(self) -> None:
        section = script_section()
        self.assertNotRegex(section, r'trActionButton\("Approve for use"[^;]*, true, "approve"\)')

    def test_server_limit_sentences_still_match_what_the_script_rewords(self) -> None:
        limits = ev.data_limits(5, {"knife": 3, "laptop": 0, "bottle": 40})
        self.assertTrue(any(re.match(r"^Only \d+ test image", text) for text in limits))
        self.assertTrue(any(re.match(r"""^Only (\d+) test box\(es\) for ['"](.*)['"]""", text) for text in limits))
        self.assertTrue(any(re.match(r"""^No test boxes for ['"](.*)['"]""", text) for text in limits))
        section = script_section()
        self.assertIn(r"""/^Only (\d+) test box\(es\) for ['"](.*)['"]/""", section)
        self.assertIn(r"""/^No test boxes for ['"](.*)['"]/""", section)
        self.assertIn(r"/^Only \d+ test image/", section)


class WeakApprovalDialogTests(TrainingBase):
    def test_dialog_has_a_required_acknowledgement_checkbox_that_sends_the_flag(self) -> None:
        html = self.client.get("/").content.decode()
        dialog = html[html.index('<dialog id="decision-dialog"') :]
        self.assertRegex(dialog, r'<input id="decision-ack" type="checkbox">')
        self.assertRegex(dialog, r'id="decision-ack-row"[^>]*\shidden>')  # only shown when the model is weak
        section = script_section()
        self.assertIn("I understand this model found none of the test boxes and I still want to use it", section)
        self.assertIn("acknowledge_weak_results: true", section)
        # the confirm button stays off until the box is ticked
        self.assertIn("needsTick", section)

    def test_the_server_names_the_rule_in_plain_words(self) -> None:
        from .training import lifecycle

        decided, _ = synthetic_evaluation({"dent": (40, 40, 0)})  # enough test boxes, all found: nothing to tick
        self.assertIsNone(lifecycle.weak_results_reason(decided))
        thin, _ = synthetic_evaluation({"dent": (4, 3, 0)})  # found most, but four boxes cannot tell
        self.assertIn("too thin or too weak", lifecycle.weak_results_reason(thin))
        self.assertIn("found none", lifecycle.weak_results_reason(
            {"available": True, "overall": {"recall": 0.0, "true_positives": 0}, "data": {"boxes": 4}}))
        self.assertIn("no results", lifecycle.weak_results_reason({"available": False}))
        self.assertIn("no results", lifecycle.weak_results_reason(None))


class ChecklistStepSixTests(TrainingBase):
    def project_row(self) -> dict:
        rows = self.client.get("/api/projects").json()["projects"]
        return next(row for row in rows if row["id"] == str(self.project.id))

    def stage(self, model):
        return self.post(self.api(f"/models/{model.id}/stage"), {})

    def approve(self, model, note="Read the report."):
        # The fabricated model's tiny test set reads "cannot tell", which needs the deliberate tick (the server's rule).
        return self.post(self.api(f"/models/{model.id}/approve"), {"note": note, "acknowledge_weak_results": True})

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
        self.post(self.api(f"/models/{first.id}/approve"), {"note": "ok", "acknowledge_weak_results": True})
        second = self.train_candidate()
        self.post(self.api(f"/models/{second.id}/stage"), {})
        self.post(self.api(f"/models/{second.id}/approve"), {"note": "better", "acknowledge_weak_results": True})
        self.post(self.api(f"/models/{second.id}/rollback"), {"note": "worse"})
        from .models import ModelEvent

        kinds = set(ModelEvent.objects.exclude(model_version=None).values_list("kind", flat=True))
        section = script_section()
        for kind in kinds:
            with self.subTest(kind=kind):
                self.assertIn(f'case "{kind}"', section)


class OneClickPanelContractTests(AutoBase):
    """The fields the one-button panel reads exist in the real answers and mean what the script assumes."""

    def test_readiness_carries_what_the_choice_and_the_plan_sentence_read(self) -> None:
        self.cache("coco", "imagenet_backbone")
        self.add_pictures(12)
        body = self.readiness()
        start = body["start_from"]
        self.assertEqual(start["resolved"], "coco")
        for option in start["available"]:
            for key in ("value", "licence_plain", "needs_acknowledgement"):
                self.assertIn(key, option)
        for quality in ("quick", "better"):
            self.assertIn("estimated_minutes", body["derived"][quality])
        for key in ("estimate_note", "based_on", "train_pictures"):
            self.assertIn(key, body["derived"])
        self.assertEqual(set(body["one_click"]) - {"warnings", "notes", "files_rechecked"}, {"supported", "ready", "blockers", "snapshot"})  # warnings, notes, files_rechecked: optional, additive
        for option in start["available"]:
            for key in ("ready", "size_note"):  # the plan sentence says when a first-time download is still to come
                self.assertIn(key, option)
        self.assertTrue(body["one_click"]["ready"], body["one_click"])
        self.assertEqual(body["derived"]["based_on"], "new_snapshot")  # nothing frozen yet: the plan says a saved copy is made

    def test_when_nothing_ready_made_can_run_the_reason_is_plain_and_the_steps_are_separate(self) -> None:
        self.add_pictures(12)
        body = self.readiness()
        start = body["start_from"]
        self.assertIsNone(start["resolved"])
        self.assertFalse(body["one_click"]["ready"])
        # The page shows these two texts as the server words them (the reason under the button, the steps under it), so the
        # contract is the keys and the split between them, not the sentences.
        for key in ("reason_if_none", "how_to_enable"):
            with self.subTest(key=key):
                self.assertIsInstance(start[key], str)
                self.assertTrue(start[key].strip())
        self.assertNotEqual(start["reason_if_none"], start["how_to_enable"])
        self.assertNotIn("ATHENA_TRAINING_ALLOW_DOWNLOAD", start["reason_if_none"])  # the visible reason carries no setting name
        self.assertIsNone(body["derived"]["quick"]["estimated_minutes"])               # so the page says it cannot be estimated
        refused = self.post_run(snapshot="latest", acknowledge_pretrained_terms=True)
        self.assertEqual(refused.status_code, 409)
        self.assertIsNone(refused.json()["start_from"]["resolved"])

    def test_the_post_the_button_sends_is_accepted_and_the_tick_is_enforced(self) -> None:
        self.cache("coco")
        self.add_pictures(12)
        body = {"snapshot": "latest", "backend": BACKEND, "config": {"quality": "better"}}
        without = self.post_run(**{k: v for k, v in body.items() if k != "backend"})
        self.assertEqual(without.status_code, 400)
        self.assertTrue(without.json()["needs_acknowledgement"])
        accepted = self.post_run(**{k: v for k, v in body.items() if k != "backend"}, acknowledge_pretrained_terms=True)
        self.assertEqual(accepted.status_code, 202, accepted.content)
        reply = accepted.json()
        self.assertEqual(reply["run"]["config"]["quality"], "better")
        for key in ("newly_created", "image_count"):
            self.assertIn(key, reply["snapshot"])
        self.assertIn("worker_online", reply)

    def test_health_carries_what_the_checklist_reads(self) -> None:
        self.add_pictures(12)
        health = self.client.get(self.api("/datasets/health")).json()
        for key in ("ready_to_teach", "blockers", "advice"):
            self.assertIn(key, health)
        self.assertIsInstance(health["advice"], list)

    def test_no_accepted_boxes_blocks_with_a_sentence_the_checklist_shows(self) -> None:
        health = self.client.get(self.api("/datasets/health")).json()
        self.assertFalse(health["ready_to_teach"])
        self.assertTrue(health["blockers"] and all(isinstance(text, str) and text for text in health["blockers"]))

    def test_the_verdicts_the_script_has_a_next_step_for_are_the_servers_verdicts(self) -> None:
        names = set(re.findall(r"^\s+(\w+): \"[A-Z]", re.search(r"const TR_NEXT_STEPS = \{(.*?)\n\};", script_section(), re.S).group(1), re.M))
        self.assertEqual(names, set(training_report.VERDICTS))


NODE = shutil.which("node")


def js_function(name: str, kind: str = "function") -> str:
    """The source of one top-level helper in the Teach section, so a test can run it without a browser."""
    section = script_section()
    if kind == "function":
        start = section.index(f"function {name}(")
        return section[start : section.index("\n}\n", start) + 3]
    start = section.index(f"const {name} =")
    return section[start : section.index(";\n", start) + 2]


def run_js(source: str, expression: str):
    result = subprocess.run([NODE, "-e", f"{source}\nprocess.stdout.write(JSON.stringify({expression}));"],
                            capture_output=True, text=True, check=True, timeout=30)
    return json.loads(result.stdout)


class ReviewSeamTests(TrainingBase):
    """Small seams from the final review: one sentence kept in three places, one threshold, two server texts."""

    def js_constant(self, name: str) -> str:
        return json.loads(re.search(rf"const {name} = (\"[^\n]*\");", script_section()).group(1))

    def test_the_not_final_sentence_is_the_same_in_the_script_the_report_and_the_check_screen(self) -> None:
        self.assertEqual(self.js_constant("TR_NOT_FINAL"), training_report.NOT_FINAL_DECISION)
        self.assertEqual(self.js_constant("TR_NOT_FINAL_LEAD"), training_report.NOT_FINAL_DECISION.split(". ")[0] + ".")
        page = (Path(__file__).resolve().parent / "templates" / "projects" / "checkscreen.html").read_text()
        self.assertIn(training_report.NOT_FINAL_DECISION, re.sub(r"<[^>]+>", "", page))
        # ...and as the server really sends it inside the plain results the card and the approve dialog read
        evaluation, ids = synthetic_evaluation({"dent": (10, 9, 1)})
        model = ModelVersion.objects.create(
            project=self.project, version=1, backend="fake-backend", artifact_path="models/x/model.bin", artifact_sha256="0" * 64,
            artifact_size_bytes=1, label_names=["dent"], evaluation=evaluation,
        )
        headline = self.client.get(self.api(f"/models/{model.id}/results/plain")).json()["overall"]["headline"]
        self.assertTrue(headline.endswith(training_report.NOT_FINAL_DECISION), headline)
        self.assertIn(training_report.NOT_FINAL_DECISION, headline)

    @skipUnless(NODE, "Node is not installed")
    def test_the_script_takes_the_servers_trailing_sentence_off_whatever_its_wording(self) -> None:
        strip = js_function("trWithoutNotFinal", "const")
        evaluation, _ = synthetic_evaluation({"dent": (10, 9, 1)})
        server = training_report.plain_results(evaluation)["overall"]["headline"]
        lead = server[: server.index(training_report.NOT_FINAL_DECISION)].strip()
        self.assertTrue(lead)
        self.assertEqual(run_js(strip, f"trWithoutNotFinal({json.dumps(server)})"), lead)
        reworded = f"{lead} Not a final decision, a person decides this."
        self.assertEqual(run_js(strip, f"trWithoutNotFinal({json.dumps(reworded)})"), lead)
        self.assertEqual(run_js(strip, f"trWithoutNotFinal({json.dumps(lead)})"), lead)

    def test_the_small_sample_line_uses_the_servers_threshold(self) -> None:
        self.assertEqual(int(re.search(r"const TR_MIN_TEST_PICTURES = (\d+);", script_section()).group(1)), ev.MIN_IMAGES_FOR_CONFIDENCE)
        self.assertIn("thresholds?.min_test_pictures", script_section())
        self.assertNotRegex(script_section(), r"test_images < \d")
        self.make_dataset()
        health = self.client.get(self.api("/datasets/health")).json()
        self.assertEqual(health["thresholds"]["min_test_pictures"], ev.MIN_IMAGES_FOR_CONFIDENCE)

    @skipUnless(NODE, "Node is not installed")
    def test_the_most_common_failure_text_gets_the_helper_advice(self) -> None:
        advice = js_function("trFailureAdvice")
        runner_text = "The training worker stopped before this run finished. Start it again and queue a new run."
        steps = run_js(advice, f"trFailureAdvice({json.dumps(runner_text)})")
        self.assertEqual(steps[0], "The teaching helper stopped. Ask your administrator to start it again, then press Teach the computer.")
        self.assertIn("worker stopped before", runner_text.lower())
        other = run_js(advice, 'trFailureAdvice("something unusual")')
        self.assertNotIn("teaching helper stopped", other[0].lower())

    def test_the_cannot_tell_next_step_names_labels_not_pictures(self) -> None:
        steps = re.search(r"const TR_NEXT_STEPS = \{(.*?)\n\};", script_section(), re.S).group(1)
        self.assertIn("The check used too few examples of at least one label to be a fair test.", steps)
        self.assertNotIn("too few test pictures", steps)

    def test_a_viewer_is_told_what_a_viewer_lacks(self) -> None:
        section = script_section()
        self.assertIn("missing.map(need => Athena.reasonFor(need))", section)  # not map(Athena.reasonFor): the index is its 2nd argument
        self.assertNotRegex(section, r"\.map\(Athena\.reasonFor\)")

    def test_results_say_what_the_server_says_about_missing_pictures(self) -> None:
        section = script_section()
        self.assertIn("misses?.reason", section)
        self.assertNotIn("item.no_longer_in_project", section)  # the server sends only the count, at the top level
        self.assertIn("misses.no_longer_in_project", section)

    def test_the_approve_dialog_shows_the_verdict_and_asks_for_the_tick_when_it_is_weak(self) -> None:
        section = script_section()
        self.assertRegex(section, r'const TR_WEAK_VERDICTS = \["not_ready", "cannot_tell"\];')
        self.assertEqual(set(training_report.VERDICTS) - {"not_ready", "cannot_tell"}, {"good_enough_to_try", "needs_more_examples"})
        self.assertIn("the results are too thin or too weak to rely on; I understand and still want to approve", section)
        self.assertIn("error.body?.needs_acknowledgement", section)  # the server's 409 reveals the tick instead of a raw error
        self.assertIn("results/plain", section[section.index("async function trFetchPlain") :])

    def test_people_who_cannot_teach_do_not_see_the_choices(self) -> None:
        section = script_section()
        self.assertIn('Athena.can("train")', section[section.index("function trRenderChoices") : section.index("const derived = one.derived")])


FAKE_DOM = """
function fake(tag) { return { tag, children: [], dataset: {}, className: '', textContent: '', append(...kids) { this.children.push(...kids); } }; }
function dsNode(tag, cls, text) { const n = fake(tag); n.className = cls || ''; if (text !== undefined) n.textContent = text; return n; }
function flat(n) { return typeof n === 'string' ? [n] : [`${n.tag}|${n.className}|${n.textContent}`].concat(...n.children.map(flat)); }
"""


@skipUnless(NODE, "Node is not installed")
class TrustDisclosureTests(TrainingBase):
    """\"How far to trust the scores\": the closed disclosure under the per-label lines, run in Node on the server's own words."""

    def helpers(self) -> str:
        consts = "".join(js_function(name, "const") for name in ("TR_TRUST_NOT_MEASURED", "TR_TRUST_CHOOSES", "trWithoutChooses", "TR_ONE_SETTING", "trText"))
        return FAKE_DOM + consts + js_function("trList") + js_function("trPrecisionRangeSentence") + js_function("trTrustBlock")

    def block(self, plain: dict) -> dict:
        out = run_js(self.helpers(), f"(() => {{ const d = trTrustBlock({json.dumps(plain)}); return {{ flat: flat(d), measured: d.dataset.measured, open: d.open === true, items: d.children.filter(c => c.tag === 'ul').map(u => u.children.map(li => ({{ text: li.textContent, rec: li.dataset.recommended }}))) }}; }})()")
        return out

    def plain(self, evaluation: dict) -> dict:
        from .training import operating_points as op

        return {"operating_points": op.plain_operating_points(evaluation), "wording": op.wording_table(evaluation)}

    def measured_evaluation(self) -> dict:
        from .tests_operating_points import synthetic_operating_points

        return {"operating_points": synthetic_operating_points()}

    def test_measured_model_lists_the_servers_sentences_once_and_ends_with_one_person_line(self) -> None:
        evaluation = self.measured_evaluation()
        plain = self.plain(evaluation)
        got = self.block(plain)
        self.assertFalse(got["open"])  # closed
        self.assertEqual(got["flat"][0], "details|result-trust|")
        self.assertEqual(got["flat"][1], "summary||How far to trust the scores")
        enough = [row["sentence"] for row in plain["wording"]["buckets"] if row["enough"]]
        self.assertTrue(enough)
        self.assertEqual([item["text"] for item in got["items"][0]], enough)
        suggestions = plain["operating_points"]["suggestions"]
        # Each row starts with the server's own sentence, as it is (a range line may follow it, see the tests below).
        for s, item in zip(suggestions, got["items"][1]):
            self.assertTrue(item["text"].startswith(s["plain"].removesuffix(" A person chooses the setting.")), item["text"])
        self.assertEqual(len(got["items"][1]), len(suggestions))
        self.assertEqual([item["rec"] for item in got["items"][1]], [("true" if s["recommended"] else "false") for s in suggestions])
        joined = "\n".join(got["flat"])
        self.assertEqual(joined.count("A person chooses the setting."), 1)
        self.assertNotIn("Not measured for this model", joined)

    def test_a_thin_range_is_not_listed_and_an_unreachable_target_is_not_a_recommendation(self) -> None:
        evaluation = self.measured_evaluation()
        plain = self.plain(evaluation)
        thin = [row for row in plain["wording"]["buckets"] if not row["enough"]]
        for row in plain["wording"]["buckets"][:2]:  # make two ranges thin, as the server would say it
            row["enough"] = False
        got = self.block(plain)
        listed = [item["text"] for item in got["items"][0]]
        for row in plain["wording"]["buckets"][:2]:
            self.assertNotIn(row["sentence"], listed)
        for s, item in zip(plain["operating_points"]["suggestions"], got["items"][1]):
            if not s["reachable"] or not s["recommended"]:
                self.assertEqual(item["rec"], "false")
        self.assertIsInstance(thin, list)

    def test_an_older_model_says_not_measured_with_the_servers_reason(self) -> None:
        plain = self.plain({"available": True})  # no operating_points stored at all
        self.assertFalse(plain["operating_points"]["available"])
        got = self.block(plain)
        text = "\n".join(got["flat"])
        self.assertIn(f"Not measured for this model: {plain['operating_points']['reason']}", text)
        self.assertNotIn("A cut-off", text)
        self.assertEqual(got["measured"], "false")
        self.assertEqual(got["items"], [])

    def test_a_server_that_sends_neither_block_gets_no_disclosure(self) -> None:
        self.assertIsNone(run_js(self.helpers(), "trTrustBlock({ overall: {} })"))

    def test_no_range_with_enough_boxes_says_so_instead_of_listing_nothing(self) -> None:
        plain = self.plain(self.measured_evaluation())
        for row in plain["wording"]["buckets"]:
            row["enough"] = False
        text = "\n".join(self.block(plain)["flat"])
        self.assertIn("No score range had enough boxes to say how often it was right.", text)

    def one_cut_off(self, plain_text: str, interval=None, **extra) -> list:
        item = {"plain": plain_text, "recommended": True, **({"interval90": interval} if interval is not False else {}), **extra}
        plain = {"operating_points": {"available": True, "suggestions": [item]}, "wording": {"available": False, "reason": "none"}}
        return self.block(plain)

    def test_the_precision_range_is_worded_with_the_low_end_down_and_the_high_end_up(self) -> None:
        got = self.one_cut_off("A cut-off of 0.4 found 90%. A person chooses the setting.", {"precision": [0.6129, 0.8801], "recall": [0.5, 0.9]})
        self.assertEqual(got["items"][0][0]["text"], "A cut-off of 0.4 found 90%. Of the boxes it drew, about 61% to 89% were right.")
        again = self.one_cut_off("x.", {"precision": [0.29, 0.58]})  # 0.29 * 100 is 28.999999999999996 in floating point
        self.assertEqual(again["items"][0][0]["text"], "x. Of the boxes it drew, about 29% to 58% were right.")
        edge = self.one_cut_off("x.", {"precision": [0, 1]})
        self.assertIn("about 0% to 100% were right", edge["items"][0][0]["text"])

    def test_the_precision_range_is_left_out_when_the_server_says_it_or_sends_none(self) -> None:
        said = "Of the boxes it drew, about 61% to 89% were right."
        got = self.one_cut_off(f"A cut-off of 0.4 found 90%; {said} A person chooses the setting.", {"precision": [0.6129, 0.8801]})
        self.assertEqual(got["items"][0][0]["text"], f"A cut-off of 0.4 found 90%; {said}")  # said once
        for missing in (False, None, {}, {"precision": None}, {"precision": [0.5]}, {"precision": ["a", "b"]}, {"precision": [0.9, 0.2]}, {"precision": [-0.1, 0.5]}, {"recall": [0.1, 0.2]}):
            self.assertEqual(self.one_cut_off("Only this.", missing)["items"][0][0]["text"], "Only this.", missing)

    def test_one_setting_for_all_labels_is_said_once_and_only_when_the_server_does_not(self) -> None:
        line = "This is one setting for all labels."
        self.assertEqual(self.one_cut_off("x.")["flat"].count(f"p||{line}"), 1)
        self.assertEqual(self.one_cut_off(f"x. {line}")["flat"].count(f"p||{line}"), 0)  # the server said it in its own sentence
        self.assertEqual(self.one_cut_off("x. It applies to ALL LABELS together.")["flat"].count(f"p||{line}"), 0)
        none = self.block({"operating_points": {"available": True, "suggestions": []}, "wording": {"available": False, "reason": "r"}})
        self.assertNotIn(f"p||{line}", none["flat"])  # no cut-off listed, so nothing to say it about

    def test_the_text_is_never_put_in_as_markup(self) -> None:
        for name in ("access.js", "checkscreen_core.js", "checkscreen.js", "checkscreen_builder.js"):
            self.assertNotIn("innerHTML", (STATIC / name).read_text(), name)
        self.assertNotIn("innerHTML", js_function("trTrustBlock") + js_function("trAccessLink"))


class DownloadSwitchPageTests(TrainingBase):
    """The Access screen section the server's own text points to, and the Teach panel's one link to it."""

    def test_the_section_name_the_server_uses_is_the_section_on_the_access_screen(self) -> None:
        from .training import dfine, torchvision_frcnn as tv

        page = (Path(__file__).resolve().parent / "templates" / "projects" / "access.html").read_text()
        for text in (tv.ENABLE_STEPS, tv.AUTO_NONE_STEPS):
            self.assertIn("under “Access”, in “Ready-made model downloads”", text)
        self.assertIn("in “Ready-made model downloads”", dfine.DFineBackend().start_options({"online": False, "downloads_allowed": False})[0]["how_to_enable"])
        self.assertIn('<h2 id="downloads-heading">Ready-made model downloads</h2>', page)
        self.assertIn('id="ready-made-model-downloads"', page)
        self.assertIn(">Access</a>", page)

    def test_the_teach_link_points_at_that_section_and_only_an_administrator_gets_it(self) -> None:
        from .tests_roles import client_for, make_user

        self.assertIn('link.href = "/access#ready-made-model-downloads"', (STATIC / "console.js").read_text())
        self.assertIn('trIsAdmin() && trState.backends?.downloads?.allowed === false', script_section())
        admin = client_for(make_user("root-ui", is_superuser=True))
        trainer = client_for(make_user("tina-ui", role="trainer"))
        self.assertIn('data-admin="1"', admin.get("/").content.decode())
        self.assertIn('data-admin="0"', trainer.get("/").content.decode())
        self.assertEqual(admin.get("/access").status_code, 200)
        self.assertEqual(trainer.get("/access").status_code, 403)  # so the section is for administrators; others read the Teach panel's sentence

    def test_a_refusal_is_shown_as_the_server_wrote_it(self) -> None:
        js = (STATIC / "access.js").read_text()
        refusal = js[js.index("error.status === 403") :]
        self.assertTrue(refusal.startswith("error.status === 403) {\n      dl.error.textContent = error.message;"))  # no prefix, no rewording

    def test_the_script_sends_what_the_server_asks_for(self) -> None:
        js = (STATIC / "access.js").read_text()
        for needle in ("based_on: downloads.allow", "payload.acknowledge_terms = true", "error.status === 409", "downloadsSaving", "if (allow) payload"):
            self.assertIn(needle, js)
        self.assertIn('"/api/console/training-downloads"', js)
        self.assertEqual(js.count("DOWNLOADS_MIN_NOTE = 3"), 1)  # the server's own minimum (console_settings.MIN_NOTE_CHARS)
        from . import console_settings

        self.assertEqual(console_settings.MIN_NOTE_CHARS, 3)


class MainButtonToolTests(TrainingBase):
    """Choosing a tool under \"Details for your administrator\" never changes the main button's tool."""

    def test_one_click_always_uses_the_default_tool(self) -> None:
        section = script_section()
        start = section[section.index("async function trStartOneClick") : section.index("async function trStartExactSettings")]
        self.assertIn("trMainTool()", start)
        self.assertNotIn("trSelectedTool()", start)
        reasons = section[section.index("function trOneClickReasons") : section.index("function trOneClickPlan")]
        self.assertNotIn("trSelectedTool()", reasons)
        load = section[section.index("async function trLoadReadiness") : section.index("function resetTraining")]
        self.assertIn("trMainTool()", load)
        self.assertNotIn("trTool.value", load)

    def test_the_exact_path_only_sends_keys_the_tool_lists(self) -> None:
        from .training import dfine

        section = script_section()
        exact = section[section.index("async function trStartExactSettings") : section.index("// The main button: one click")]
        self.assertIn("config: { ...settings.config }", exact)
        validate = section[section.index("function trValidateSettings") : section.index("// --- Advanced: start from a ready-made model")]
        self.assertIn("if (trModelSize.dataset.default) config.size", validate)  # size only for a tool that lists it
        self.assertEqual(set(dfine.DEFAULTS) - {"size"}, {"epochs", "batch_size", "learning_rate", "seed", "threads", "pretrained"})
        self.assertEqual(sorted(dfine.SIZES), sorted(re.search(r"const TR_MODEL_SIZES = (\[[^\]]*\]);", section).group(1).replace('"', "").strip("[]").replace(" ", "").split(",")))


@skipUnless(NODE, "Node is not installed")
class FallbackToolAndReadOnlyDownloadsTests(TrainingBase):
    """The main button never falls back to the optional second tool; a trainer reads the switch but cannot change it."""

    def main_tool(self, data: dict | None) -> str | None:
        source = js_function("TR_DEFAULT_TOOL", "const") + "const trState = { backends: %s };\n" % json.dumps(data) + js_function("trMainTool")
        return run_js(source, "(() => { const t = trMainTool(); return t && t.name; })()")

    def tools(self, **usable) -> list:
        return [{"name": name, "available_in_web_process": ok} for name, ok in usable.items()]

    def test_the_fallback_name_is_the_servers_default_backend(self) -> None:
        from . import training

        self.assertEqual(json.loads(re.search(r"const TR_DEFAULT_TOOL = (\"[^\n]*\");", script_section()).group(1)), training.DEFAULT_BACKEND)

    def test_an_unusable_default_is_still_the_main_tool_and_never_the_second_tool(self) -> None:
        both = self.tools(**{"dfine-coco": True, "torchvision-fasterrcnn": False})
        self.assertEqual(self.main_tool({"default": "torchvision-fasterrcnn", "backends": both}), "torchvision-fasterrcnn")
        self.assertEqual(self.main_tool({"default": "no-such-tool", "backends": both}), "torchvision-fasterrcnn")  # the named constant
        self.assertEqual(self.main_tool({"backends": both}), "torchvision-fasterrcnn")
        self.assertIsNone(self.main_tool({"default": "no-such-tool", "backends": self.tools(**{"dfine-coco": True})}))  # never the second tool
        self.assertIsNone(self.main_tool(None))

    def test_the_tool_picker_does_not_choose_the_second_tool_for_anyone(self) -> None:
        render = js_function("trRenderTools")
        self.assertNotIn("options.find(option => !option.disabled)", render)
        self.assertIn("TR_DEFAULT_TOOL", render)

    def test_the_downloads_state_is_the_servers_sentence_or_the_same_facts_in_words(self) -> None:
        source = js_function("TR_DOWNLOADS_SOURCE", "const") + js_function("trDownloadsSentence")
        said = "Downloading ready-made models is switched off, the built-in default. No one has chosen yet."
        self.assertEqual(run_js(source, f"trDownloadsSentence({json.dumps({'allow': False, 'source': 'default', 'summary': said})})"), said)
        self.assertEqual(run_js(source, "trDownloadsSentence({ allow: false, source: 'default' })"), "Downloads are switched off (set by default).")
        self.assertEqual(run_js(source, "trDownloadsSentence({ allow: true, source: 'console', summary: '  ' })"), "Downloads are switched on (set by an administrator).")
        self.assertEqual(run_js(source, "trDownloadsSentence({ allow: true, source: 'later-source' })"), "Downloads are switched on.")

    def test_a_trainer_is_told_who_can_change_it_and_where_without_a_link_or_a_control(self) -> None:
        from .tests_roles import client_for, make_user

        ask = js_function("TR_ASK_ADMIN_DOWNLOADS", "const")
        self.assertEqual(run_js(ask, "TR_ASK_ADMIN_DOWNLOADS"), "Only an administrator can change this. Ask an administrator to open Access > Ready-made model downloads.")
        html = client_for(make_user("tina-fu", role="trainer")).get("/").content.decode()
        note = html[html.index('id="tr-downloads-note"') : html.index("</p>", html.index('id="tr-downloads-note"'))]
        self.assertNotIn("<a ", note)
        self.assertNotIn("<input", note)
        render = js_function("trRenderDownloadsNote")
        self.assertIn("trIsAdmin()", render)
        loader = script_section()
        loader = loader[loader.index("async function trLoadDownloadsSetting") : loader.index("function trDownloadsSentence")]
        self.assertIn('Athena.can("train")', loader)  # a viewer is not sent to a 403
        self.assertIn("!trIsAdmin()", loader)


# --- W9-L3: the improve loop in the console (corrections, origin badge, comparison with the model in use) ---------------

IMPROVE_FAKE_DOM = FAKE_DOM + """
const Athena = { ICONS: { done: '\\u2713', close: '\\u00d7', warn: '!', unknown: '?', busy: '\\u2026' } };
function fake(tag) { return { tag, children: [], dataset: {}, className: '', textContent: '', scope: '', attrs: {},
  append(...kids) { this.children.push(...kids); }, prepend(...kids) { this.children.unshift(...kids); },
  setAttribute(k, v) { this.attrs[k] = v; } }; }
function dsNode(tag, cls, text) { const n = fake(tag); n.classList = { add: (c) => { n.className = (n.className + ' ' + c).trim(); } }; n.className = cls || ''; if (text !== undefined) n.textContent = text; return n; }
const plural = (count, one, many) => `${count.toLocaleString()} ${count === 1 ? one : many}`;
"""


def whole_js_function(name: str, kind: str = "function") -> str:
    """One top-level helper from anywhere in console.js (js_function only reads the Teach section)."""
    js = (STATIC / "console.js").read_text()
    if kind == "function":
        start = js.index(f"function {name}(")
        return js[start : js.index("\n}\n", start) + 3]
    start = js.index(f"const {name} =")
    return js[start : js.index(";\n", start) + 2]


def comparison_block(**over) -> dict:
    base = {
        "available": True, "verdict": "better", "fair": True, "unfair_reason": "",
        "plain": "Better on these test pictures: the new model found 70 of 90 test boxes. Not a final decision. This is the computer’s opinion. A person decides.",
        "baseline": {"version": 3}, "candidate_result": {"found": 70, "of": 90, "wrong_boxes": 8}, "baseline_result": {"found": 55, "of": 90, "wrong_boxes": 12},
    }
    base.update(over)
    return base


@skipUnless(NODE, "Node is not installed")
class ImproveLoopUiTests(TrainingBase):
    """The comparison block, the corrections outcome sentence and the auto-suggest reason, run in Node on the server's own words."""

    def block(self, comparison) -> dict | None:
        source = IMPROVE_FAKE_DOM + js_function("trWithoutNotFinal", "const") + js_function("TR_COMPARE_VERDICTS", "const") + js_function("trComparisonBlock")
        return run_js(source, f"(() => {{ const b = trComparisonBlock({json.dumps(comparison)}); return b && {{ flat: flat(b), verdict: b.dataset.verdict }}; }})()")

    def test_the_block_shows_the_servers_sentence_verbatim_a_two_row_table_and_a_verdict_word(self) -> None:
        got = self.block(comparison_block())
        lines = got["flat"]
        self.assertIn("h6||Compared with the model in use", lines)
        # the server's sentence word for word, minus its closing "not a final decision" lines (the card says them once)
        self.assertIn("p|result-compare-plain|Better on these test pictures: the new model found 70 of 90 test boxes.", lines)
        self.assertFalse(any("Not a final decision" in line for line in lines))
        self.assertIn("th||New model", lines)
        self.assertIn("th||Model in use (version 3)", lines)
        self.assertIn("td|num|70 of 90", lines)
        self.assertIn("td|num|55 of 90", lines)
        self.assertEqual(got["verdict"], "better")
        badge = [line for line in lines if line.startswith("span|compare-verdict|")]
        self.assertEqual(len(badge), 1)  # the word is a child text node; the mark is aria-hidden
        self.assertIn("Better", lines)

    def test_the_closing_not_a_final_decision_lines_are_cut_in_any_case_and_everything_else_is_kept(self) -> None:
        for ending in ("Not a final decision. This is the computer’s opinion. A person decides.", "NOT A FINAL DECISION. THIS IS THE COMPUTER’S OPINION. A PERSON DECIDES.", "not a final decision. This is the computer’s opinion. A person decides."):
            got = self.block(comparison_block(plain=f"Worse on these test pictures: 3 of 9 found. {ending}"))
            self.assertIn("p|result-compare-plain|Worse on these test pictures: 3 of 9 found.", got["flat"], ending)
            self.assertNotRegex("\n".join(got["flat"]), r"(?i)not a final decision")
        untouched = self.block(comparison_block(plain="Better on these test pictures: 5 of 6 found."))
        self.assertIn("p|result-compare-plain|Better on these test pictures: 5 of 6 found.", untouched["flat"])
        only_the_line = self.block(comparison_block(plain="Not a final decision. This is the computer’s opinion. A person decides."))
        self.assertFalse(any("result-compare-plain" in line for line in only_the_line["flat"]))  # nothing left to say: no empty line

    def test_each_verdict_has_its_own_word_and_an_unknown_one_says_cannot_tell(self) -> None:
        words = {"better": "Better", "about_the_same": "About the same", "worse": "Worse", "cannot_tell": "Cannot tell", "surprise": "Cannot tell"}
        for verdict, word in words.items():
            got = self.block(comparison_block(verdict=verdict))
            self.assertIn(word, got["flat"], verdict)
            self.assertEqual(got["verdict"], "cannot_tell" if verdict == "surprise" else verdict)

    def test_no_table_without_both_results_and_nothing_at_all_when_the_key_is_absent(self) -> None:
        got = self.block(comparison_block(candidate_result=None))
        self.assertFalse(any(line.startswith("table|") for line in got["flat"]))
        self.assertIsNone(self.block(None))
        self.assertIsNone(self.block("text"))

    def test_unavailable_shows_the_reason_once_without_saying_no_comparison_twice(self) -> None:
        said = self.block({"available": False, "reason": "No comparison with the model in use: no model is in use yet."})
        self.assertEqual(said["flat"], ["p|result-compare-none asset-help|No comparison with the model in use: no model is in use yet."])
        plain = self.block({"available": False, "reason": "its file is missing."})
        self.assertEqual(plain["flat"], ["p|result-compare-none asset-help|No comparison: its file is missing."])
        self.assertIn("No comparison: none was saved", self.block({"available": False})["flat"][0])

    def test_the_unfair_reason_is_shown_once_a_line_of_its_own_only_when_the_sentence_leaves_it_out(self) -> None:
        reason = "6 of the 40 test pictures were also used to teach the model in use."
        inside = self.block(comparison_block(fair=False, unfair_reason=reason, plain=f"Cannot tell, and this comparison is not fair: {reason} Not a final decision."))
        self.assertEqual("\n".join(inside["flat"]).count(reason), 1)
        outside = self.block(comparison_block(fair=False, unfair_reason=reason))
        self.assertIn(f"p|result-compare-unfair|Not a fair comparison: {reason}", outside["flat"])
        fair = self.block(comparison_block(unfair_reason=reason))  # a fair comparison never shows a reason
        self.assertFalse(any("result-compare-unfair" in line for line in fair["flat"]))

    def test_nothing_is_put_in_as_markup_and_no_word_the_rules_ban(self) -> None:
        self.assertNotIn("innerHTML", js_function("trComparisonBlock"))
        for name in ("correctionsOutcome", "renderCorrections", "describeJob"):
            self.assertNotIn("innerHTML", whole_js_function(name), name)
        shown = " ".join(user_facing_strings(js_function("trComparisonBlock") + js_function("TR_COMPARE_VERDICTS", "const")))
        self.assertNotRegex(shown, r"(?i)accurate|certified|\bsafe\b|observation|artifact|SHA")

    def test_the_dialog_sentence_is_information_only_and_comes_first(self) -> None:
        config = js_function("trDecisionConfig")
        approve = config[config.index('if (kind === "approve")') :]
        self.assertLess(approve.index("compareSentence"), approve.index("const verdictShown"))
        self.assertIn("plain.comparison.available === true", approve)
        self.assertIn("body.push(compare)", approve)
        # the same cut as the results card; the one line is kept for the case where no verdict line carries it
        self.assertIn("trWithoutNotFinal(plain.comparison.plain)", approve)
        self.assertIn("if (!verdictShown) body.push(trNotFinalNode())", approve)
        # a sentence, never a control or a gate: the tick-box rule still keys on the verdict and the results only
        self.assertNotIn("comparison", approve[approve.index("acknowledge:") :])

    def left_out(self, item) -> str:
        source = IMPROVE_FAKE_DOM + whole_js_function("LEFT_OUT_PHRASES", "const") + whole_js_function("leftOutSentence")
        return run_js(source, f"leftOutSentence({json.dumps(item)})")

    def test_boxes_left_out_are_said_in_plain_words_and_only_when_the_server_sends_them(self) -> None:
        self.assertEqual(self.left_out({"boxes_skipped": 1, "skipped_reasons": ["label_not_in_project"]}), "1 box was left out: its label is not in this project.")
        self.assertEqual(self.left_out({"boxes_skipped": 2, "skipped_reasons": ["label_not_in_project", "label_not_in_project", "box_not_valid"]}),
                         "2 boxes were left out: their labels are not in this project; they have no usable size.")
        # a reason as the server's own sentence is shown as given; a count alone still says something
        sentence = "The detection this verdict is about is not in the stored result, so no box was made."
        self.assertEqual(self.left_out({"boxes_skipped": 1, "skipped_reasons": [{"reason": "unheard_of", "message": sentence}]}), f"1 box was left out. {sentence}")
        self.assertEqual(self.left_out({"boxes_skipped": 3}), "3 boxes were left out.")
        # the import answer sends the list itself, and its own sentence is shown as written after the short lead
        self.assertEqual(self.left_out({"skipped_boxes": [{"reason": "label_not_in_project", "message": "m"}]}), "1 box was left out. m")
        self.assertEqual(self.left_out({"skipped_boxes": [{"reason": "label_not_in_project", "message": "m"}, {"reason": "box_not_valid", "message": "n"}, {"reason": "label_not_in_project", "message": "m"}]}), "3 boxes were left out. m n")
        self.assertEqual(self.left_out({"skipped_boxes": [{"reason": "label_not_in_project"}]}), "1 box was left out: its label is not in this project.")  # no sentence sent: our short phrase
        for nothing in ({}, {"boxes_skipped": 0, "skipped_reasons": []}, {"skipped_boxes": []}, None, "x", {"boxes_skipped": "1"}):
            self.assertEqual(self.left_out(nothing), "", nothing)

    def test_the_cut_off_sentence_is_only_for_a_list_the_server_says_was_cut(self) -> None:
        js = (STATIC / "console.js").read_text()
        self.assertIn("data.truncated === true", js)
        self.assertIn("Showing the newest ${", js)
        self.assertIn("; add these first, then press again for more.", js)
        self.assertNotIn("Only the newest 100 are looked at.", js)  # the old line named a number the server does not use

    def outcome(self, body: dict) -> dict:
        source = IMPROVE_FAKE_DOM + whole_js_function("LEFT_OUT_PHRASES", "const") + whole_js_function("leftOutSentence") + whole_js_function("correctionsOutcome")
        return run_js(source, f"correctionsOutcome({json.dumps(body)})")

    def test_the_import_outcome_lists_the_pictures_that_came_in_with_boxes_left_out(self) -> None:
        body = {"imported": [{"observation_id": "a", "boxes_created": 1, "skipped_boxes": [{"reason": "label_not_in_project", "message": "m"}]}, {"observation_id": "b", "boxes_created": 2, "skipped_boxes": []}],
                "skipped": [], "totals": {}}
        self.assertEqual(self.outcome(body)["leftOut"], [["a", "1 box was left out. m"]])
        self.assertEqual(self.outcome({"imported": [{"observation_id": "a"}], "skipped": [], "totals": {}})["leftOut"], [])  # an older answer

    def test_the_import_outcome_is_plain_words_with_every_skipped_picture_kept(self) -> None:
        body = {
            "imported": [{"observation_id": "a", "boxes_created": 2, "missed_without_box": 1, "false_alarms": 1}, {"observation_id": "b", "boxes_created": 0, "missed_without_box": 0, "false_alarms": 1}],
            "skipped": [{"observation_id": "c", "reason": "picture_file_missing", "message": "The picture file from the check screen is no longer in storage."}],
            "totals": {"requested": 3, "imported": 2, "skipped": 1, "boxes_created": 2, "missed_without_box": 1, "remaining_pending": 4},
        }
        got = self.outcome(body)
        self.assertEqual(got["text"], "Added 2 pictures for review, with 2 boxes marked Needs review. 1 item the computer missed has no box yet; open the picture to draw it. 2 false alarms were noted but not turned into a box. 1 picture has no boxes, so there is nothing to teach from yet. 1 picture was not added:")
        self.assertEqual(got["skipped"], body["skipped"])
        self.assertEqual(got["more"], "4 pictures are still waiting. Press the button again to add them.")

    def test_a_second_import_that_adds_nothing_says_so_instead_of_reporting_success(self) -> None:
        got = self.outcome({"imported": [], "skipped": [], "totals": {"requested": 0, "imported": 0, "skipped": 0, "remaining_pending": 0}})
        self.assertEqual(got["text"], "Nothing new to add. These corrections were already added.")
        self.assertEqual(got["more"], "")
        stale = self.outcome({"imported": [], "skipped": [{"observation_id": "c", "message": "m"}], "totals": {"remaining_pending": 0}})
        self.assertTrue(stale["text"].startswith("No new pictures were added."))
        for odd in ({}, {"imported": "x", "skipped": None, "totals": []}):
            self.assertTrue(self.outcome(odd)["text"])  # a damaged answer still gives a sentence

    def test_the_auto_suggest_reason_is_added_once_and_only_when_the_server_sends_one(self) -> None:
        source = IMPROVE_FAKE_DOM + whole_js_function("describeJob") + "const describeJobState = (job) => `Finished: 2 boxes.${job.error ? ' ' + job.error : ''}`;"
        reason = "kept for review: this picture came from operator feedback"

        def say(job):
            return run_js(source, f"describeJob({json.dumps(job)})")

        self.assertEqual(say({"error": ""}), "Finished: 2 boxes.")  # an older server
        self.assertEqual(say({"error": "", "kept_for_review": 0, "kept_for_review_reason": ""}), "Finished: 2 boxes.")
        self.assertEqual(say({"error": "", "kept_for_review": 2, "kept_for_review_reason": reason}), f"Finished: 2 boxes. 2 pictures left out ({reason}).")
        said = f"2 pictures left out ({reason})."
        self.assertEqual(say({"error": said, "kept_for_review": 2, "kept_for_review_reason": reason}), f"Finished: 2 boxes. {said}")  # not twice


class ImproveLoopMarkupTests(TrainingBase):
    """The Pictures part keeps one yellow action; the corrections card is outline only and gated like every write."""

    def panel(self) -> str:
        html = (Path(__file__).parent / "templates/projects/console.html").read_text()
        start = html.index('id="image-assets-panel"')
        return html[start : html.index("</section>\n\n        <section", start)]

    def test_the_corrections_card_is_one_outline_button_and_the_part_keeps_one_yellow_action(self) -> None:
        panel = self.panel()
        self.assertEqual(len(re.findall(r'class="primary"', panel)), 1)
        self.assertIn("Add pictures</button>", panel[panel.index('class="primary"') :])
        button = re.search(r'<button[^>]*id="corrections-import"[^>]*>', panel).group(0)
        self.assertIn('class="secondary"', button)
        self.assertIn('data-needs="label"', button)
        self.assertIn("Corrections from check screens", panel)
        card = panel[panel.index('id="corrections-panel"') :]
        self.assertIn('data-gate-note="label"', card[: card.index("</section>")])  # a viewer is told why, beside the button
        self.assertRegex(card, r'id="corrections-result"[^>]*role="status"[^>]*aria-live="polite"')

    def test_the_picture_list_has_one_text_toggle_for_pictures_from_check_screens(self) -> None:
        panel = self.panel()
        button = re.search(r'<button[^>]*id="asset-filter-feedback"[^>]*>([^<]*)</button>', panel)
        self.assertIsNotNone(button)
        self.assertEqual(button.group(1), "Show only pictures from check screens")
        self.assertIn('aria-pressed="false"', button.group(0))
        self.assertIn('type="button"', button.group(0))
        self.assertLess(panel.index('id="asset-filter"'), panel.index('id="asset-list"'))
        self.assertNotIn('class="filter"', button.group(0))  # that class belongs to the project-list filters and their click handler
        self.assertIn('id="autolabel-feedback-note"', panel)
        self.assertIn('id="corrections-leftout"', panel)

    def test_bulk_accept_asks_first_when_boxes_from_check_screens_wait_and_the_safe_answer_shows_them(self) -> None:
        js = (STATIC / "console.js").read_text()
        click = js[js.index('document.getElementById("autolabel-accept").addEventListener'):js.index('document.getElementById("autolabel-reject")')]
        self.assertLess(click.index("feedbackPendingBoxes()"), click.index("Athena.askConfirm"))
        self.assertLess(click.index("Athena.askConfirm"), click.index("reviewProposals("))  # asked before anything is sent
        self.assertIn('keepLabel: "Check them first"', click)
        self.assertIn("if (!goOn)", click)
        self.assertIn("assetFeedbackOnly = true", click)
        server = click[click.index("reviewProposals("):]
        self.assertIn('action: "accept"', server)  # the server action is the one it always was

    def test_the_warning_counts_only_waiting_boxes_in_pictures_from_check_screens(self) -> None:
        source = (IMPROVE_FAKE_DOM + whole_js_function("isFromCheckScreen", "const") + whole_js_function("feedbackPendingBoxes", "const")
                  + whole_js_function("feedbackBoxesWarning", "const"))
        assets = [{"origin": "operator_feedback", "pending_boxes": 2}, {"origin": "operator_feedback", "pending_boxes": 1}, {"origin": "upload", "pending_boxes": 9}, {"origin": "operator_feedback"}, {"pending_boxes": 4}]
        got = run_js(f"let currentAssets = {json.dumps(assets)};\n{source}", "[feedbackPendingBoxes(), feedbackBoxesWarning(3), feedbackBoxesWarning(1)]")
        self.assertEqual(got[0], 3)
        self.assertEqual(got[1], "3 of these boxes come from check-screen corrections. Check them first: the computer’s boxes there were confirmed or added by an operator, not found by the auto-suggest.")
        self.assertEqual(got[2], "1 of these boxes comes from a check-screen correction. Check it first: the computer’s box there was confirmed or added by an operator, not found by the auto-suggest.")
        self.assertEqual(run_js("let currentAssets = [];\n" + source, "feedbackPendingBoxes()"), 0)

    def test_the_explanation_is_the_one_the_design_asks_for(self) -> None:
        self.assertIn("These pictures come in with the computer’s boxes marked Needs review. Nothing is used for teaching until a person accepts the boxes. The model in use is not changed.", self.panel())

    def test_the_script_sends_the_default_body_once_and_guards_a_double_press(self) -> None:
        js = (STATIC / "console.js").read_text()
        click = js[js.index('correctionsImport.addEventListener("click"') : js.index("function annotationEndpoint")]
        self.assertEqual(js.count("/feedback/corrections/import"), 1)
        self.assertIn('body: "{}"', click)
        self.assertRegex(click.split("await api")[0], r"if \(corrections\.busy \|\|")  # refused before any request goes out
        self.assertIn('Athena.can("label")', click)
        self.assertIn("correctionsResult.focus()", click)
        self.assertIn("corrections.projectId !== projectId", click)  # a project change while waiting

    def test_the_origin_badge_and_the_nothing_to_teach_sentence_follow_the_servers_origin_and_counts(self) -> None:
        js = (STATIC / "console.js").read_text()
        rows = js[js.index("async function loadAssets") : js.index("// --- Corrections from check screens")]
        self.assertIn('asset.origin === "operator_feedback"', rows)
        self.assertIn('"From a check screen"', rows)
        self.assertIn("!asset.accepted_boxes && !asset.pending_boxes", rows)  # only a picture with nothing saved and nothing waiting
        self.assertIn('const CORRECTIONS_NOTHING = "This picture was marked wrong but nothing was marked as missed, so there is no box to teach from. Draw a box if something should be found here, or leave it.";', js)
        css = (STATIC / "console.css").read_text()
        self.assertIn(".origin-mark", css)  # a drawn mark next to the words: never colour alone

    def test_readiness_notes_go_under_the_plan_sentence_and_before_the_warnings(self) -> None:
        update = js_function("trUpdateStart")
        plan = update.index("trStartHelp.textContent = trState.busy")
        notes = update.index("Array.isArray(one.one_click.notes)")
        warnings = update.index("trOneClickWarnings(one).forEach")
        self.assertLess(plan, notes)
        self.assertLess(notes, warnings)
        self.assertIn('"train-plan-note"', update)
        self.assertNotIn("innerHTML", update)


# --- W12-G3: corrections findable, honest captions, guarded bulk actions, switching check screens, a worse model -------

def pull(*names: str) -> str:
    """Top-level functions and constants from console.js, in the order asked, so Node can run the real code."""
    js = (STATIC / "console.js").read_text()
    out = []
    for name in names:
        if f"function {name}(" in js:
            start = js.index(f"function {name}(")
            if js[max(0, start - 6) : start] == "async ":
                start -= 6
            out.append(js[start : js.index("\n}\n", start) + 3])
        else:
            start = js.index(f"const {name} =")
            out.append(js[start : js.index(";\n", start) + 2])
    return "\n".join(out)


def run_js_async(source: str, expression: str):
    script = f"{source}\n(async () => {{ const value = await ({expression}); process.stdout.write(JSON.stringify(value)); }})().catch(error => {{ console.error(error); process.exit(1); }});"
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True, timeout=30)
    return json.loads(result.stdout)


APPROVE_HARNESS = IMPROVE_FAKE_DOM + """
const trState = { plain: new Map(), models: [], approvedId: null, health: null };
const trMinTestPictures = () => 30;
""" + pull("TR_NOT_FINAL", "TR_NOT_FINAL_LEAD", "trWithoutNotFinal", "trPct", "trModelName", "trText",
           "trNotFinalNode", "trResultSummary", "TR_WEAK_VERDICTS", "TR_WEAK_ACK", "TR_VERDICT_STATES", "trIsWorse", "TR_WORSE_LINE", "TR_WORSE_ACK",
           "trScreensOn", "trScreenWord", "trDecisionConfig")


@skipUnless(NODE, "Node is not installed")
class ImproveLoopG3Tests(TrainingBase):
    """Run the real functions in Node: who made a box, what a bulk press says, which screens approving switches, a worse model."""

    # U2: the caption names who made the box
    def test_a_box_from_a_check_screen_is_never_captioned_as_the_computers(self) -> None:
        source = IMPROVE_FAKE_DOM + pull("isProposal", "isOperatorBox", "OPERATOR_BOX_WAITING", "OPERATOR_BOX_DONE", "boxCaption")
        operator = "Marked on a check screen by an operator, waiting for your review"
        done = "Marked on a check screen by an operator, accepted by you"
        cases = [
            ({"origin": "model", "status": "suggested", "model_name": "operator feedback (model v1)"}, operator),
            ({"origin": "model", "status": "accepted", "model_name": "operator feedback (model v1)"}, done),
            ({"origin": "model", "status": "suggested", "model_name": "Operator feedback (model v2)"}, operator),
            ({"origin": "model", "status": "suggested", "model_name": "yolo auto"}, "Needs your review"),
            ({"origin": "model", "status": "accepted", "model_name": "yolo auto"}, "Suggested by computer, accepted by you"),
            ({"origin": "model", "status": "suggested", "model_name": "an operator feedback imitation"}, "Needs your review"),
            ({"origin": "model", "status": "suggested"}, "Needs your review"),
        ]
        for box, expected in cases:
            with self.subTest(box=box):
                self.assertEqual(run_js(source, f"boxCaption({json.dumps(box)})"), expected)
        for name in ("loadBoxAnnotations", "renderBoxAnnotationList"):
            self.assertNotIn("Suggested by computer, accepted by you", whole_js_function(name))  # one caption function, used by both places

    # U3: the outcome of a bulk accept
    def test_the_bulk_accept_outcome_says_what_was_left_and_why(self) -> None:
        source = IMPROVE_FAKE_DOM + pull("wholeCount", "acceptOutcomeText")
        left = {"total": 4, "without_score": 4, "from_operator_feedback": 4}
        self.assertEqual(run_js(source, f"acceptOutcomeText({{ updated: 3, pending_boxes: 4, left_for_review: {json.dumps(left)} }}, {{ fromScreens: 4 }})"),
                         "Accepted 3. Left 4 for you to check one by one: 4 have no score (they were marked on check screens).")
        one = {"total": 1, "without_score": 1}
        self.assertEqual(run_js(source, f"acceptOutcomeText({{ updated: 2, left_for_review: {json.dumps(one)} }}, {{ fromScreens: 1 }})"),
                         "Accepted 2. Left 1 for you to check one by one: 1 has no score (it was marked on check screens).")
        self.assertEqual(run_js(source, "acceptOutcomeText({ updated: 5, left_for_review: { total: 2, without_score: 0 } }, {})"), "Accepted 5. Left 2 for you to check one by one.")
        self.assertEqual(run_js(source, "acceptOutcomeText({ updated: 5, left_for_review: { total: 0, without_score: 0 } }, {})"), "Accepted 5. Nothing is waiting for review.")
        # an older server: the count after the press is all there is
        self.assertEqual(run_js(source, "acceptOutcomeText({ updated: 3, pending_boxes: 4 }, { fromScreens: 4 })"),
                         "Accepted 3. Left 4 for you to check one by one. Boxes marked on check screens may have no score, and this button leaves those for you.")
        self.assertEqual(run_js(source, "acceptOutcomeText({ updated: 3, pending_boxes: 4 }, { fromScreens: 0 })"), "Accepted 3. Left 4 for you to check one by one.")
        self.assertEqual(run_js(source, "acceptOutcomeText({ updated: 0, pending_boxes: 0 }, {})"), "Accepted 0. Nothing is waiting for review.")

    def test_a_preview_changes_nothing_and_only_counts_when_the_server_says_it_was_one(self) -> None:
        source = IMPROVE_FAKE_DOM + pull("previewReview")
        run = lambda stub: run_js_async(f"let sent = [];\n{source}\nasync function reviewProposals(projectId, body, options) {{ sent.push({{ body, options }}); {stub} }}",
                                        "(async () => ({ got: await previewReview('p', { action: 'reject' }), sent }))()")
        said = run("return { preview: true, would_update: 3, from_operator_feedback: 2, left_for_review: { total: 0 } };")
        self.assertEqual(said["got"]["from_operator_feedback"], 2)
        self.assertEqual(said["sent"][0]["body"], {"action": "reject", "preview": True})
        self.assertEqual(said["sent"][0]["options"], {"expectStatus": [400]})  # a refusal from an older server is not a failed save
        self.assertIsNone(run("return { updated: 3, pending_boxes: 0 };")["got"])  # not an answer to a preview: nothing to rely on
        self.assertIsNone(run("throw new Error('This request contains an unsupported field.');")["got"])

    def test_both_bulk_buttons_look_before_they_ask_and_ask_before_they_act(self) -> None:
        js = (STATIC / "console.js").read_text()
        accept = js[js.index('document.getElementById("autolabel-accept").addEventListener') : js.index('document.getElementById("autolabel-reject")')]
        self.assertLess(accept.index("previewReview("), accept.index("Athena.askConfirm"))
        self.assertLess(accept.index("Athena.askConfirm"), accept.index('reviewProposals(projectId, { action: "accept"'))
        reject = js[js.index('document.getElementById("autolabel-reject").addEventListener') : js.index("annotationAutolabel.addEventListener")]
        self.assertLess(reject.index("previewReview("), reject.index("Athena.askConfirm"))
        self.assertLess(reject.index("Athena.askConfirm"), reject.index('reviewProposals(projectId, { action: "reject" }'))
        self.assertIn("Rejecting them removes them for good, and those corrections cannot be added again.", reject)
        self.assertIn("Rejecting it removes it for good, and that correction cannot be added again.", reject)
        self.assertIn('keepLabel: "Keep them"', reject)
        self.assertIn('confirmLabel: "Reject anyway"', reject)
        self.assertEqual(js.count("preview: true"), 1)  # only previewReview ever sends it
        # the answer is written after the list reloads, to its own live line, and only while the same project is open
        for click in (accept, reject):
            self.assertLess(click.index("await loadAssets(projectId)"), click.index("showReviewOutcome(outcome)"))
            self.assertIn("selectedId === projectId", click[click.index("showReviewOutcome(outcome)") - 60 :])
        self.assertIn('id="autolabel-outcome" role="status" aria-live="polite"', (Path(__file__).parent / "templates/projects/console.html").read_text())

    # U4: what a picture from a decision says, and what the import answer names
    def test_a_rejected_or_second_look_picture_names_the_decision_and_shows_the_note_as_text(self) -> None:
        source = IMPROVE_FAKE_DOM + pull("DECISION_SENTENCES", "DECISION_NOTE_SHOWN", "decorateFeedbackRows")
        hostile = "<img src=x onerror=alert(1)> " + "x" * 400
        script = f"""
        const slots = {{}}; const mk = id => {{ const slot = dsNode('div', 'asset-decision'); slot.replaceChildren = () => {{ slot.children = []; }}; slots[id] = slot; return {{ dataset: {{ assetId: id }}, querySelector: () => slot }}; }};
        const rows = ['a1', 'a2', 'a3', 'a4'].map(mk);
        const assetList = {{ querySelectorAll: () => rows }};
        const corrections = {{ data: {{ items: [
          {{ asset_id: 'a1', decision: 'reject', decision_note: {json.dumps(hostile)} }},
          {{ asset_id: 'a2', decision: 'second_look', decision_note: '' }},
          {{ asset_id: 'a3', decision: null, decision_note: 'ignored' }}] }} }};
        {source}
        decorateFeedbackRows();
        const said = id => slots[id].children.map(n => n.className + '|' + n.textContent);
        """
        got = run_js(script, "({ a1: said('a1'), a2: said('a2'), a3: said('a3'), a4: said('a4') })")
        self.assertEqual(got["a1"][0], "asset-nothing|An operator rejected the whole result for this picture.")
        note = got["a1"][1]
        self.assertTrue(note.startswith("asset-decision-note|Operator’s note: <img src=x onerror=alert(1)>"))  # text, not markup
        self.assertTrue(note.endswith("…"))
        self.assertLessEqual(len(note.split("|", 1)[1]), len("Operator’s note: ") + 301)
        self.assertEqual(got["a2"], ["asset-nothing|An operator asked for a second look at the result for this picture."])  # no note: no note line
        self.assertEqual(got["a3"], [])  # the latest decision is neither: nothing to add
        self.assertEqual(got["a4"], [])  # a picture the list does not hold

    def test_the_import_answer_names_pictures_that_came_in_only_because_of_a_decision(self) -> None:
        source = IMPROVE_FAKE_DOM + pull("LEFT_OUT_PHRASES", "CORRECTIONS_LEFT_OUT_SHOWN", "leftOutSentence", "correctionsOutcome")
        body = {"imported": [{"observation_id": "a", "boxes_created": 0, "decision": "reject"}, {"observation_id": "b", "boxes_created": 0, "false_alarms": 1}, {"observation_id": "c", "boxes_created": 2}],
                "skipped": [], "totals": {"boxes_created": 2, "pictures_from_decisions": 1, "remaining_pending": 0}}
        text = run_js(source, f"correctionsOutcome({json.dumps(body)}).text")
        self.assertIn("1 picture was added only because an operator rejected the whole result or asked for a second look. They have no boxes, so there is nothing to teach from yet.", text)
        self.assertIn("1 picture has no boxes, so there is nothing to teach from yet.", text)  # the false-alarm one, counted once
        plain = run_js(source, 'correctionsOutcome({ imported: [{ observation_id: "b", boxes_created: 0 }], skipped: [], totals: {} }).text')  # an older server
        self.assertIn("1 picture has no boxes, so there is nothing to teach from yet.", plain)
        self.assertNotIn("rejected the whole result", plain)

    def test_the_check_screen_says_one_plain_line_and_the_wrong_box_line_only_for_wrong_answers(self) -> None:
        js = (STATIC / "checkscreen.js").read_text()
        self.assertIn('const SAVED_FOR_SUPERVISOR = "Saved for your supervisor. It does not change the computer by itself.";', js)
        self.assertIn('const WRONG_CANNOT_TEACH = "A wrong-box answer cannot teach the computer anything on its own; drawing a box where something was missed can.";', js)
        right = js[js.index('verdict === "true_positive"') : js.index('"ok");', js.index('verdict === "true_positive"'))]
        self.assertIn("SAVED_FOR_SUPERVISOR", right)
        self.assertEqual(right.count("WRONG_CANNOT_TEACH"), 1)  # in the "wrong" branch only
        self.assertLess(right.index("this is right"), right.index("this is wrong"))
        self.assertNotIn("WRONG_CANNOT_TEACH", right[: right.index("this is wrong")])
        missed = js[js.index("Saved: the computer missed this.") : js.index("Saved: the computer missed this.") + 140]
        self.assertIn("SAVED_FOR_SUPERVISOR", missed)
        self.assertNotIn("WRONG_CANNOT_TEACH", missed)
        decide = js[js.index("const toSupervisor"): js.index("announce(`Decision recorded")]
        self.assertIn('key === "reject" ? `${SAVED_FOR_SUPERVISOR} ${WRONG_CANNOT_TEACH}`', decide)
        self.assertIn('key === "needs_second_look" ? SAVED_FOR_SUPERVISOR : ""', decide)
        self.assertNotIn("does not change the model", js)  # the old, only-negative wording is gone
        self.assertIn("not</strong> change the computer model", (Path(__file__).parent / "templates/projects/checkscreen.html").read_text())  # the page note stays

    # U1: the count, the chip, the checklist row
    def test_the_corrections_count_is_worded_for_one_and_many(self) -> None:
        source = IMPROVE_FAKE_DOM + pull("correctionsWord")
        self.assertEqual(run_js(source, "[correctionsWord(1), correctionsWord(11), correctionsWord(1200)]"), ["1 correction", "11 corrections", "1,200 corrections"])

    def test_the_checklist_row_names_the_count_and_opens_the_card(self) -> None:
        source = IMPROVE_FAKE_DOM + """
        const checklistEl = dsNode('ol'); let opened = 0; const openCorrections = () => { opened += 1; };
        """ + pull("correctionsWord", "addCorrectionsItem")
        script = source + "\n{ const plain = dsNode; dsNode = (...args) => { const n = plain(...args); n.addEventListener = (type, fn) => { n.handlers = { ...(n.handlers || {}), [type]: fn }; }; return n; }; }\n"
        got = run_js(script, "(() => { addCorrectionsItem(11); const li = checklistEl.children[0]; const button = li.children[2]; button.handlers.click(); return { flat: flat(li), state: li.dataset.state, cls: li.className, button: button.attrs['aria-label'], opened }; })()")
        self.assertEqual(got["state"], "attention")
        self.assertIn("checklist-corrections", got["cls"])
        self.assertIn("strong||Corrections waiting", got["flat"])
        self.assertIn("span|step-badge|11 waiting", got["flat"])
        self.assertIn("button|secondary|Open the corrections", got["flat"])
        self.assertEqual(got["button"], "Open the corrections (11 corrections waiting)")
        self.assertEqual(got["opened"], 1)

    def test_the_chip_card_and_checklist_follow_the_count_without_polling(self) -> None:
        js = (STATIC / "console.js").read_text()
        signals = js[js.index("function renderCorrectionSignals()") : js.index("function resetCorrections")]
        self.assertIn("jumpCorrections.textContent", signals)
        self.assertIn("placeCorrectionsCard(waiting)", signals)
        self.assertIn("renderChecklist(project)", signals)
        place = js[js.index("function placeCorrectionsCard") : js.index("// A person who is looking")]
        self.assertIn("form.after(correctionsPanel)", place)  # above the auto-suggest area and the list when something waits
        self.assertIn("home.before(correctionsPanel)", place)  # the old place when nothing does
        self.assertEqual(js.count("/feedback/corrections`"), 1)  # still the one request per project open
        self.assertNotIn("setInterval", js[js.index("// --- Corrections from check screens") : js.index("function annotationEndpoint")])
        html = (Path(__file__).parent / "templates/projects/console.html").read_text()
        self.assertIn('<span class="jump-label">Pictures</span><span class="jump-count" id="jump-corrections" hidden></span>', html)
        self.assertIn('.querySelector(".jump-label")', whole_js_function("panelName"))  # Back/Next buttons name the part, not the count

    # U5: switching the check screens that use the model being replaced
    def approve(self, extras: dict | None, approved_apps=None, plain=None, replaced: bool = True) -> dict:
        extras_js = json.dumps(extras or {})
        state = f"trState.models = [{{ id: 'old', version: 1, in_use_by_apps: {json.dumps(approved_apps)} }}]; trState.approvedId = {json.dumps('old' if replaced else None)};"
        if plain is not None:
            state += f" trState.plain.set('new', {json.dumps(plain)});"
        model = {"id": "new", "version": 2, "evaluation": {"available": True, "recall": 0.5, "precision": 0.5, "test_images": 40, "test_boxes": 60}}
        script = f"{APPROVE_HARNESS}\n{state}\n"
        expression = (f"(() => {{ const c = trDecisionConfig('approve', {json.dumps(model)}, {extras_js}); "
                      "return { flat: [].concat(...c.body.map(flat)), repoint: c.repoint, acknowledge: c.acknowledge, "
                      "states: c.body.map(n => [n.className, n.dataset && n.dataset.state, n.dataset && n.dataset.verdict]) }; })()")
        return run_js(script, expression)

    def test_one_screen_that_can_be_switched_is_named_and_the_tick_starts_on(self) -> None:
        got = self.approve({"wouldRepoint": [{"app_id": "a", "name": "Line 4", "can_repoint": True}]})
        self.assertIn("p|dataset-note decision-screens|1 check screen uses the model being replaced: Line 4. If you do nothing it stops working until someone switches it.", got["flat"])
        self.assertEqual(got["repoint"], {"text": "Switch this check screen to the new model now", "defaultOn": True})

    def test_several_screens_and_one_that_cannot_be_switched_are_listed_and_the_tick_starts_off(self) -> None:
        both = self.approve({"wouldRepoint": [{"name": "Line 4", "can_repoint": True}, {"name": "Line 5", "can_repoint": True}]})
        self.assertIn("p|dataset-note decision-screens|2 check screens use the model being replaced: Line 4, Line 5. If you do nothing they stop working until someone switches them.", both["flat"])
        self.assertEqual(both["repoint"], {"text": "Switch these 2 check screens to the new model now", "defaultOn": True})
        mixed = self.approve({"wouldRepoint": [{"name": "Line 4", "can_repoint": True}, {"name": "Line 5", "can_repoint": False, "reason": "It checks different labels."}]})
        self.assertIn("li||Line 5: It checks different labels. It will not be switched.", mixed["flat"])
        self.assertEqual(mixed["repoint"], {"text": "Switch this check screen to the new model now", "defaultOn": False})
        none = self.approve({"wouldRepoint": [{"name": "Line 5", "can_repoint": False, "reason": "Its model file is missing."}]})
        self.assertIsNone(none["repoint"])  # nothing to tick: the list and the reason say it

    def test_without_the_servers_list_the_screens_are_named_but_nothing_is_offered(self) -> None:
        got = self.approve(None, approved_apps=[{"app_id": "a", "name": "Line 4"}])
        self.assertTrue(any(line.startswith("p|dataset-note decision-screens|1 check screen uses the model being replaced: Line 4.") for line in got["flat"]))
        self.assertIsNone(got["repoint"])  # no list from the server, so no switch that might not exist
        self.assertTrue(any("Switching is not done from here." in line for line in got["flat"]))

    def test_the_dialog_never_says_screens_keep_using_a_model_that_is_being_retired(self) -> None:
        cases = {
            "listed": self.approve({"wouldRepoint": [{"name": "Line 4", "can_repoint": True}]}),
            "unlisted": self.approve(None, approved_apps=[{"name": "Line 4"}]),
            "none": self.approve(None, approved_apps=[]),
            "unknown": self.approve(None, approved_apps=None),
            "no model in use": self.approve(None, approved_apps=None, replaced=False),
        }
        for name, got in cases.items():
            with self.subTest(case=name):
                self.assertFalse(any("exact model they were set up with" in line for line in got["flat"]))
        self.assertTrue(any("No check screen uses the model being replaced" in line for line in cases["none"]["flat"]))
        self.assertFalse(any("decision-screens" in line for line in cases["none"]["flat"]))
        self.assertTrue(any("No model is in use yet" in line for line in cases["no model in use"]["flat"]))
        self.assertTrue(any(line.startswith("li||Apart from that, approving changes nothing else") for line in cases["listed"]["flat"]))

    def test_names_from_the_server_are_text_never_markup(self) -> None:
        hostile = "<img src=x onerror=alert(1)>"
        got = self.approve({"wouldRepoint": [{"name": hostile, "can_repoint": False, "reason": "<b>no</b>"}]})
        self.assertTrue(any(hostile in line for line in got["flat"]))
        self.assertNotIn("innerHTML", pull("trDecisionConfig"))

    def test_the_answer_after_approval_says_what_was_switched_and_what_was_left(self) -> None:
        source = IMPROVE_FAKE_DOM + pull("trRepointText")
        self.assertEqual(run_js(source, "trRepointText({ repointed: [{ app_id: 'a', name: 'A' }, { app_id: 'b', name: 'B' }], repoint_skipped: [{ app_id: 'c', name: 'Line 5', reason: 'It checks different labels.' }] })"),
                         "Switched 2 check screens. Left alone: Line 5 (It checks different labels.).")
        self.assertEqual(run_js(source, "trRepointText({ repointed: [{ name: 'A' }], repoint_skipped: [] })"), "Switched 1 check screen.")
        self.assertEqual(run_js(source, "trRepointText({ repointed: [], repoint_skipped: [{ name: 'X', reason: 'r' }, { name: 'Y' }] })"), "Left alone: X (r); Y.")
        self.assertEqual(run_js(source, "trRepointText({})"), "")  # an older server
        self.assertEqual(run_js(source, "trRepointText({ repointed: 'x', repoint_skipped: null })"), "")

    def test_the_switch_is_asked_for_only_when_ticked_and_only_on_approval(self) -> None:
        submit = js_function("trSubmitDecision")
        self.assertIn("config.repoint && trDialogRepoint.checked ? { repoint_apps: true } : {}", submit)
        self.assertNotIn("repoint", js_function("trDecisionConfig")[js_function("trDecisionConfig").index("const previous"):])  # not offered when going back
        html = (Path(__file__).parent / "templates/projects/console.html").read_text()
        self.assertRegex(html, r'<label class="inline-check decision-ack decision-repoint" id="decision-repoint-row" for="decision-repoint" hidden>')

    # U6: a worse model is not called good
    def test_only_a_fair_comparison_that_says_worse_counts_as_worse(self) -> None:
        source = IMPROVE_FAKE_DOM + pull("trIsWorse")
        cases = [({"available": True, "verdict": "worse", "fair": True}, True), ({"available": True, "verdict": "worse"}, True),
                 ({"available": True, "verdict": "worse", "fair": False}, False), ({"available": True, "verdict": "better", "fair": True}, False),
                 ({"available": True, "verdict": "about_the_same", "fair": True}, False), ({"available": False, "verdict": "worse"}, False), (None, False)]
        for comparison, expected in cases:
            with self.subTest(comparison=comparison):
                self.assertIs(run_js(source, f"trIsWorse({json.dumps(comparison)})"), expected)

    def plain_block(self, verdict: str, comparison) -> list:
        source = IMPROVE_FAKE_DOM + pull("TR_NOT_FINAL", "TR_NOT_FINAL_LEAD", "trWithoutNotFinal", "trNotFinalNode", "TR_VERDICT_STATES", "trIsWorse", "TR_WORSE_LINE", "TR_NEXT_STEPS", "trPlainBlock") + """
        const trComparisonBlock = c => (c ? dsNode('div', 'result-compare') : null); const trTrustBlock = () => null; const trMissesBlock = () => dsNode('div', 'misses');
        """
        plain = {"overall": {"verdict": verdict, "headline": "Good enough to try: it found 78 of 90. Not a final decision. This is the computer’s opinion. A person decides."}, "labels": [], "comparison": comparison, "misses": {}}
        return run_js(source, f"trPlainBlock({{ id: 'm' }}, {json.dumps(plain)}).map(n => [n.className, n.dataset.state || '', flat(n).join(' ')])")

    def test_a_worse_model_gets_an_amber_line_under_the_headline_and_no_green(self) -> None:
        worse = self.plain_block("good_enough_to_try", {"available": True, "verdict": "worse", "fair": True})
        self.assertEqual(worse[0][0:2], ["result-headline", ""])  # not the green style
        self.assertEqual(worse[1][0], "result-worse")
        self.assertIn("Worse than the model in use on the same test pictures.", worse[1][2])  # one line, with its warning mark
        self.assertEqual(worse[2][0], "result-not-final")  # the one not-a-final-decision line follows
        better = self.plain_block("good_enough_to_try", {"available": True, "verdict": "better", "fair": True})
        self.assertEqual(better[0][0:2], ["result-headline", "ok"])
        self.assertFalse(any(row[0] == "result-worse" for row in better))
        same = self.plain_block("good_enough_to_try", {"available": True, "verdict": "about_the_same", "fair": True})
        self.assertEqual(same[0][0:2], ["result-headline", "ok"])
        unfair = self.plain_block("cannot_tell", {"available": True, "verdict": "cannot_tell", "fair": False})
        self.assertFalse(any(row[0] == "result-worse" for row in unfair))
        red = self.plain_block("not_ready", {"available": True, "verdict": "worse", "fair": True})
        self.assertEqual(red[0][0:2], ["result-headline", "none"])  # a red headline stays red; the amber line still appears
        self.assertEqual(red[1][0], "result-worse")

    def test_the_approve_dialog_asks_for_the_tick_up_front_when_the_comparison_says_worse(self) -> None:
        plain = {"overall": {"verdict": "good_enough_to_try", "headline": "Good enough to try: it found 78 of 90."},
                 "comparison": {"available": True, "verdict": "worse", "fair": True, "plain": "Worse on these test pictures: 62 of 90 against 71. Not a final decision. This is the computer’s opinion. A person decides."}}
        got = self.approve(None, approved_apps=[], plain=plain)
        self.assertEqual(got["acknowledge"], "I have read that this model did worse than the model in use on the same test pictures; I understand and still want to approve")
        self.assertTrue(got["flat"][0].startswith("p|result-compare-plain compare-in-dialog|"))  # the comparison comes first
        compare_state = got["states"][0]
        self.assertEqual(compare_state[2], "worse")
        headline = next(state for state in got["states"] if state[0] == "result-headline")
        self.assertEqual(headline[1], None)  # not green
        better = dict(plain, comparison={**plain["comparison"], "verdict": "better", "plain": "Better on these test pictures."})
        self.assertEqual(self.approve(None, approved_apps=[], plain=better)["acknowledge"], "")

    def test_the_409_sentence_from_the_server_reveals_the_tick_in_its_own_words(self) -> None:
        submit = js_function("trSubmitDecision")
        self.assertIn('typeof error.body.weak_results === "string"', submit)
        self.assertIn("trIsWorse((trState.plain.get(model.id) || {}).comparison) ? TR_WORSE_ACK : TR_WEAK_ACK", submit)
        self.assertIn("Nothing was changed. ${weak ||", submit)

    # U8: percentages are rounded down
    def test_percentages_are_never_rounded_up(self) -> None:
        source = IMPROVE_FAKE_DOM + pull("trPct")
        self.assertEqual(run_js(source, "[trPct(0.996), trPct(1), trPct(0), trPct(0.77777), trPct(0.29), trPct(0.07), trPct(null), trPct(undefined)]"),
                         ["99%", "100%", "0%", "77%", "29%", "7%", None, None])
        result = {"available": True, "recall": 0.996, "precision": 0.504, "test_images": 12, "test_boxes": 40}
        summary = run_js(IMPROVE_FAKE_DOM + pull("trPct", "trResultSummary"), f"trResultSummary({{ evaluation: {json.dumps(result)} }})")
        self.assertIn("found 99% of the boxes people drew", summary)
        self.assertIn("was right 50% of the time", summary)
        self.assertNotIn("Math.round", js_function("trResultSummary"))

    def test_the_cut_off_message_names_the_number_the_server_scanned(self) -> None:
        card = whole_js_function("renderCorrectionsCard")
        scanned = card[card.index("data.truncated === true") :]
        self.assertLess(scanned.index("data.scanned"), scanned.index("data.cannot_import_scanned"))
        self.assertIn('looked ? looked.toLocaleString() : "ones"', scanned)
        self.assertNotIn("data.total", scanned[: scanned.index("correctionsCount.textContent")])
