"""Cross-feature checks for the three screens (console, check-screen builder, operator screen).

Focus order, dialogs and layout are verified in a real Chromium run (see docs/dogfood). These tests pin what
must stay true on every commit: the shared script is loaded everywhere, no page uses the browser's own
confirm box, every dialog is managed, the removal and incomplete-snapshot wording is present, the check-screen
list is wired up, and the interface keeps one icon approach.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase

from .tests_dataset_ui import CASE_SENSITIVE, JARGON, STATIC, user_facing_strings, visible_text
from .tests_training_ui import TRAINING_JARGON, TRAINING_CASE_SENSITIVE

TEMPLATES = Path(__file__).resolve().parent / "templates" / "projects"
SCRIPTS = sorted(STATIC.glob("*.js"))


def source(name: str) -> str:
    return (STATIC / name).read_text()


class SharedScriptTests(TestCase):
    def setUp(self) -> None:
        self.client.force_login(get_user_model().objects.create_user("operator"))

    def test_shared_script_loads_before_each_page_script(self) -> None:
        self.assertIn("ui_common.js", (TEMPLATES / "console.html").read_text())
        self.assertIn("ui_common.js", (TEMPLATES / "checkscreen_base.html").read_text())
        for template, page_script in (("console.html", "console.js"), ("checkscreen_base.html", "checkscreen_core.js")):
            html = (TEMPLATES / template).read_text()
            with self.subTest(template=template):
                self.assertLess(html.index("ui_common.js"), html.index(page_script))

    def test_no_script_uses_the_browsers_own_confirm_or_alert_box(self) -> None:
        for path in SCRIPTS:
            with self.subTest(script=path.name):
                self.assertNotRegex(path.read_text(), r"(?<![\w.])(window\.)?(confirm|alert|prompt)\(")

    def test_every_dialog_in_a_template_is_managed_or_built_by_the_shared_script(self) -> None:
        console_js = source("console.js")
        html = (TEMPLATES / "console.html").read_text()
        dialogs = re.findall(r"<dialog id=\"([\w-]+)\"", html)
        self.assertGreaterEqual(len(dialogs), 3)
        for name in ("projectDialog", "annotationDialog", "trDialog"):
            with self.subTest(dialog=name):
                self.assertIn(f"Athena.manageDialog({name})", console_js)
        self.assertEqual(re.findall(r"<dialog", (TEMPLATES / "checkscreen_builder.html").read_text()), [])
        self.assertIn("Athena.askConfirm", source("checkscreen_builder.js"))

    def test_shared_script_traps_tab_and_restores_focus(self) -> None:
        js = source("ui_common.js")
        for needle in ('event.key !== "Tab"', "event.shiftKey", "_opener", "showModal", "keepFocus", 'role", "alertdialog"'):
            with self.subTest(needle=needle):
                self.assertIn(needle, js)

    def test_confirmations_put_the_safe_answer_first_and_name_both_answers(self) -> None:
        js = source("ui_common.js")
        self.assertLess(js.index("actions.append(keep, go)"), js.index("keep.focus()"))
        self.assertIn('"primary", keepLabel', js)
        self.assertIn('"secondary danger", confirmLabel', js)


class OneIconApproachTests(TestCase):
    ALLOWED = set("\u2713\u00d7\u2212\u2026\u00b7\u2014\u2013\u201c\u201d\u2019\u2018\u00a0")  # check, times, minus, ellipsis, dot, dashes, quotes
    BANNED = re.compile("[\u2715\u2717\u2714\u2716\u26a0\u2192\u2190\u2191\u2193\uff0b\u25cb\u25d0\u25cf\u2605\u2606\u21bb\u27f3\u2022\u25b2\u25bc\u2b50\ufe0f]|[\U0001F300-\U0001FAFF]")

    def test_scripts_and_templates_use_no_emoji_or_decorative_glyphs(self) -> None:
        for path in [*SCRIPTS, *TEMPLATES.glob("*.html"), STATIC / "console.css", STATIC / "checkscreen.css"]:
            for number, line in enumerate(path.read_text().splitlines(), 1):
                with self.subTest(file=path.name, line=number):
                    self.assertIsNone(self.BANNED.search(line), line.strip()[:120])

    def test_no_icon_font_or_image_library_is_loaded(self) -> None:
        for path in TEMPLATES.glob("*.html"):
            html = path.read_text()
            with self.subTest(template=path.name):
                self.assertNotRegex(html, r"<link[^>]+(icon|font)[^>]*>|<svg|fontawesome|phosphor|<i class=\"(fa|ph|bi)")


class RemovePictureFlowTests(TestCase):
    def test_removal_asks_twice_only_when_snapshots_contain_the_picture(self) -> None:
        js = source("console.js")
        self.assertIn('keepLabel: "Keep the picture"', js)
        self.assertIn('confirmLabel: "Remove it anyway"', js)
        self.assertIn("remove_from_snapshots=1", js)
        self.assertIn("error.body.snapshots", js)
        self.assertIn("expectStatus: [409]", js)
        # The second confirmation names the snapshots and says what is lost.
        self.assertIn("can no longer be downloaded", js)
        self.assertIn("snapshots_marked_incomplete", js)

    def test_a_handled_refusal_is_not_reported_as_a_failed_save(self) -> None:
        js = source("console.js")
        self.assertIn("quiet", js)
        self.assertIn("expectStatus.includes(response.status)", js)


class IncompleteSnapshotTests(TestCase):
    def test_script_shows_badge_with_count_and_disables_download_with_a_reason(self) -> None:
        js = source("console.js")
        for needle in (
            "dataset.is_incomplete",
            "dataset.missing_pictures",
            "`Incomplete: ${plural(missing",
            'badgeNode("incomplete"',
            "button.disabled = true",
            'button.setAttribute("aria-describedby", incompleteNote.id)',
            "body.incomplete",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, js)

    def test_incomplete_badge_has_a_style_that_does_not_rely_on_colour_alone(self) -> None:
        css = source("console.css")
        self.assertRegex(css, r'\.stage-badge\[data-stage="incomplete"\]\s*\{[^}]*border: 2px solid')


class CheckScreenListTests(TestCase):
    def setUp(self) -> None:
        self.client.force_login(get_user_model().objects.create_user("operator"))
        self.html = self.client.get("/").content.decode()

    def test_panel_is_on_the_project_page_hidden_until_a_project_is_open(self) -> None:
        self.assertRegex(self.html, r'<section class="panel apps-panel" id="apps-panel"[^>]*\shidden>')
        for element_id in ("apps-heading", "apps-list", "apps-status", "apps-empty", "apps-empty-text", "apps-empty-link"):
            with self.subTest(element_id=element_id):
                self.assertIn(f'id="{element_id}"', self.html)

    def test_panel_knows_where_the_operator_screen_lives(self) -> None:
        self.assertIn('data-open-url-template="/check/slug-placeholder"', self.html)

    def test_script_lists_screens_with_open_and_edit_links_and_an_empty_state(self) -> None:
        js = source("console.js")
        for needle in (
            "/inference/apps",
            '"Open the screen"',
            '"Edit"',
            "You have no check screens yet",
            "You need an approved model first",
            "appProblem",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, js)

    def test_panel_text_and_sentences_have_no_jargon(self) -> None:
        panel = self.html[self.html.index('id="apps-panel"') : self.html.index("</section>", self.html.index('id="apps-panel"'))]
        text = visible_text(panel)
        js = source("console.js")
        section = js[js.index("// --- Check screens (the pages") : js.index("// --- Get-started checklist")]
        sentences = user_facing_strings(section)
        self.assertGreater(len(sentences), 8)
        for term in TRAINING_JARGON:
            flags = 0 if term in TRAINING_CASE_SENSITIVE else re.I
            pattern = re.compile(term, flags)
            with self.subTest(term=term):
                self.assertIsNone(pattern.search(text), text)
                for sentence in sentences:
                    self.assertIsNone(pattern.search(sentence), sentence)

    def test_checklist_step_seven_becomes_done_when_a_working_screen_exists(self) -> None:
        js = source("console.js")
        self.assertIn('state: workingScreens ? "done"', js)
        self.assertIn("Every step is done.", js)


class SameWordsForSameActionsTests(TestCase):
    def test_removing_things_uses_remove_and_only_the_whole_project_is_deleted(self) -> None:
        js = source("console.js")
        html = (TEMPLATES / "console.html").read_text()
        self.assertNotIn('"Delete box"', js)
        self.assertIn('"Remove box"', js)
        self.assertIn("Delete this project", html)
        self.assertNotRegex(js + html, r"Delete (this )?(picture|box|label)")

    def test_cancel_and_save_wording_is_shared(self) -> None:
        html = (TEMPLATES / "console.html").read_text()
        self.assertIn(">Cancel</button>", html)
        self.assertNotRegex(html, r">(Dismiss|Never mind|Abort)<")



class LoadOrderTests(TestCase):
    """Things a browser run found and a plain "does the text exist" test could not."""

    def test_every_dialog_is_managed_only_after_its_variable_exists(self) -> None:
        # A const used before its declaration throws at load time and leaves the whole console dead.
        js = source("console.js")
        for name in ("projectDialog", "annotationDialog", "trDialog"):
            with self.subTest(dialog=name):
                declared = re.search(rf"const {name}\s*=", js)
                used = js.index(f"Athena.manageDialog({name})")
                self.assertIsNotNone(declared)
                self.assertLess(declared.start(), used)

    def test_results_sentence_agrees_with_one_wrong_box(self) -> None:
        self.assertIn('=== 1 ? "was" : "were"} wrong', source("console.js"))

    def test_a_download_refused_as_incomplete_redraws_the_snapshot_list(self) -> None:
        js = source("console.js")
        self.assertIn("body.incomplete) void loadSnapshots(", js)


class UploadWordingTests(TestCase):
    def setUp(self) -> None:
        self.client.force_login(get_user_model().objects.create_user("operator"))

    def test_console_skips_the_wrong_file_and_still_sends_the_rest(self) -> None:
        js = source("console.js")
        for needle in ("MAX_PICTURE_BYTES", "Left out:", "is not a JPEG, PNG or WebP picture", "and the limit is 20 MB", "upload.append(\"images\""):
            with self.subTest(needle=needle):
                self.assertIn(needle, js)

    def test_server_names_the_file_it_refused(self) -> None:
        import io

        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image

        from .models import Project

        project = Project.objects.create(name="Names", modality="image", task="t", labels=["a"])
        buffer = io.BytesIO()
        Image.new("RGB", (8, 8), "red").save(buffer, "PNG")
        good = SimpleUploadedFile("good.png", buffer.getvalue(), content_type="image/png")
        bad = SimpleUploadedFile("shift-notes.txt", b"not a picture", content_type="text/plain")
        response = self.client.post(f"/api/projects/{project.id}/assets", {"images": [good, bad]})
        self.assertEqual(response.status_code, 400)
        error = response.json()["error"]
        self.assertIn("shift-notes.txt", error)
        self.assertIn("Nothing was added", error)
        self.assertEqual(project.assets.count(), 0)

    def test_picture_list_keeps_upload_order(self) -> None:
        import io

        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image

        from .models import Project

        project = Project.objects.create(name="Order", modality="image", task="t", labels=["a"])
        uploads = []
        for index in range(6):
            buffer = io.BytesIO()
            Image.new("RGB", (8 + index, 8), "blue").save(buffer, "PNG")
            uploads.append(SimpleUploadedFile(f"p{index}.png", buffer.getvalue(), content_type="image/png"))
        self.assertEqual(self.client.post(f"/api/projects/{project.id}/assets", {"images": uploads}).status_code, 201)
        names = [asset["filename"] for asset in self.client.get(f"/api/projects/{project.id}/assets").json()["assets"]]
        self.assertEqual(names, [f"p{index}.png" for index in range(6)])


class OneStyleTests(TestCase):
    """The console, the check-screen builder and the operator screen share one size scale."""

    def test_check_screen_css_does_not_resize_shared_buttons_or_fields_on_large_screens(self) -> None:
        css = source("checkscreen.css")
        large = css.split("@media")[0]
        self.assertNotRegex(large, r"\.cs-page \.primary[^{]*\{[^}]*(font-size|padding)")
        self.assertNotRegex(large, r"\.cs-page \.secondary[^{]*\{[^}]*(font-size|padding)")
        self.assertIn("font-size: 14px; font-weight: 400; /* same field as the console */", css)

    def test_one_field_border_colour_and_no_bold_field_text(self) -> None:
        css = source("console.css")
        self.assertNotIn("#777971", css)
        for rule in re.findall(r"^[^{}\n]*(?:input|select)[^{}\n]*\{[^}]*font-size: 1[46]px[^}]*\}", css, re.M):
            if "min-height" in rule:
                with self.subTest(rule=rule[:60]):
                    self.assertIn("font-weight: 400", rule)

    def test_phones_get_a_16px_field_font(self) -> None:
        self.assertRegex(source("console.css"), r"@media \(max-width: 768px\) \{[^}]*font-size: 16px")


class LongNamesAndBackButtonTests(TestCase):
    def setUp(self) -> None:
        self.client.force_login(get_user_model().objects.create_user("operator"))

    def test_very_long_unbroken_names_wrap_instead_of_widening_the_page(self) -> None:
        css = source("console.css")
        self.assertRegex(css, r"\.table-wrap \{[^}]*position: relative")
        self.assertRegex(css, r"\.desktop-table td \{[^}]*overflow-wrap: anywhere")
        self.assertRegex(css, r"\.workspace > \*, \.workspace-head > div \{[^}]*min-width: 0")
        self.assertRegex(css, r"\.workspace-sub \{[^}]*overflow-wrap: anywhere")

    def test_console_remembers_the_open_project_in_the_address(self) -> None:
        js = source("console.js")
        self.assertIn("history.replaceState(null, \"\", `#project=${id}`)", js)
        self.assertIn("#project=([0-9a-f-]{36})", js)

    def test_builder_links_back_to_the_same_project_and_warns_about_unsaved_edits(self) -> None:
        from .models import Project

        project = Project.objects.create(name="Back", modality="image", task="t", labels=["a"])
        self.assertIn(f'href="/#project={project.id}"', (TEMPLATES / "checkscreen_builder.html").read_text().replace("{{ project.id }}", str(project.id)))
        js = source("checkscreen_builder.js")
        self.assertIn('addEventListener("beforeunload"', js)
        self.assertIn("touched = false;", js)

    def test_dialog_close_and_small_controls_reach_44px_on_phones(self) -> None:
        css = source("console.css")
        self.assertRegex(css, r"@media \(max-width: 850px\) \{\s*\.dialog-close \{ min-width: 44px; min-height: 44px; \}")
