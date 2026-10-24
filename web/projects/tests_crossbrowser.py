"""Fixes for the cross-browser findings XB-1, XB-2, XB-4, XB-6 and XB-7 (docs/dogfood/2026-10-cross-browser.md).

Honest limits: these pin the markup, stylesheet and script text, and run the one pure function (the
operator sentence) in Node when Node exists. They cannot see the browser. The behaviour itself (address after
create, computed field sizes at every width, Tab/Shift+Tab inside dialogs in Chromium and WebKit, landmarks) is
checked by `web/scripts/browser_smoke.mjs`, which is the check that fails when a page is dead.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from unittest import skipUnless

STATIC = Path(__file__).parent / "static" / "projects"
TEMPLATES = Path(__file__).parent / "templates"
NODE = shutil.which("node")


def source(name: str) -> str:
    return (STATIC / name).read_text()


class OpenProjectAddressTests(TestCase):
    """XB-1: creating a project must put it in the address, like opening one does."""

    def test_create_handler_remembers_the_new_project_before_it_renders(self) -> None:
        js = source("console.js")
        handler = js[js.index('projectForm.addEventListener("submit"') :]
        handler = handler[: handler.index("// --- Auto-suggest")]
        self.assertIn("rememberProjectInAddress(selectedId)", handler)
        self.assertLess(handler.index("rememberProjectInAddress(selectedId)"), handler.index("render();"))

    def test_one_helper_writes_the_address_for_every_way_of_opening_a_project(self) -> None:
        js = source("console.js")
        self.assertEqual(js.count("history.replaceState(null, \"\", `#project=${id}`)"), 1)
        self.assertIn("function rememberProjectInAddress(id)", js)
        self.assertIn("rememberProjectInAddress(id);", js)  # selectProject


FIELD_RULE = re.compile(r"(?P<selector>[^{}@]+)\{(?P<body>[^{}]*border: 1px solid var\(--field-line\)[^{}]*background: var\(--field\)[^{}]*)\}")


class FieldSizeTests(TestCase):
    """XB-2: every text field is at least 16px at every width (iOS Safari zooms smaller ones on focus)."""

    def css(self, name: str) -> str:
        return source(name)

    def test_field_size_is_one_variable_of_at_least_16px(self) -> None:
        css = self.css("console.css")
        match = re.search(r"--field-size:\s*(\d+)px", css)
        self.assertIsNotNone(match)
        self.assertGreaterEqual(int(match.group(1)), 16)

    def test_every_bordered_field_rule_uses_that_size_without_a_media_query(self) -> None:
        for name in ("console.css", "checkscreen.css"):
            css = self.css(name)
            rules = [m for m in FIELD_RULE.finditer(css)]
            self.assertGreater(len(rules), 0, name)
            for rule in rules:
                with self.subTest(file=name, selector=rule.group("selector").strip()[:60]):
                    self.assertIn("font-size: var(--field-size)", rule.group("body"))

    def test_no_field_rule_sets_a_pixel_size_below_16(self) -> None:
        for name in ("console.css", "checkscreen.css"):
            for rule in FIELD_RULE.finditer(self.css(name)):
                for size in re.findall(r"font-size:\s*(\d+)px", rule.group("body")):
                    self.assertGreaterEqual(int(size), 16, rule.group("selector"))

    def test_the_old_phone_only_override_is_gone(self) -> None:
        css = self.css("console.css")
        self.assertNotIn(".keyboard-box-grid input, textarea, .search { font-size: 16px; }", css)
        self.assertIn("textarea", css)

    def test_a_catch_all_covers_fields_no_other_rule_names(self) -> None:
        css = self.css("console.css")
        self.assertRegex(css, r"input:not\(\[type=\"checkbox\"\][^{]*\{[^}]*font-size: var\(--field-size\)")


class DialogTabTests(TestCase):
    """XB-4: Tab and Shift+Tab move through the dialog's own controls and wrap; they never rely on the
    browser's own stops (Safari skips buttons and links by default, so 'the last control' differs)."""

    def test_trap_steps_focus_itself_instead_of_waiting_for_the_last_button(self) -> None:
        js = source("ui_common.js")
        trap = js[js.index("function trapTab") : js.index("/* Call once per <dialog>")]
        self.assertNotIn("active === last", trap)
        self.assertIn("event.preventDefault()", trap)
        self.assertIn("nextStop(", trap)

    def test_every_dialog_uses_the_trap(self) -> None:
        js = source("console.js")
        for dialog in ("projectDialog", "annotationDialog", "trDialog", "decisionDialog"):
            if f"const {dialog}" in js:
                self.assertIn(f"Athena.manageDialog({dialog}", js)


@skipUnless(NODE, "Node is not installed")
class OperatorSentenceTests(TestCase):
    """XB-6: the headline never says 'at least 0% sure'."""

    def run_sentence(self, count: int, threshold: float) -> str:
        script = f"""
        const vm = require('vm'); const fs = require('fs');
        const window = {{}}; const Athena = {{ ICONS: {{ done: 'ok', warn: '!', unknown: '?', busy: '..', close: 'x' }} }};
        vm.runInNewContext(fs.readFileSync({json.dumps(str(STATIC / "checkscreen_core.js"))}, 'utf8'), {{ window, Athena, document: {{}} }});
        const detections = Array.from({{ length: {count} }}, () => ({{}}));
        process.stdout.write(window.CheckScreen.machineSentence({{ status: 'succeeded', detections }}, {{ confidence_threshold: {threshold} }}));
        """
        return subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True, timeout=30).stdout

    def test_nothing_found_at_zero(self) -> None:
        text = self.run_sentence(0, 0)
        self.assertNotIn("0%", text)
        self.assertNotIn("at least", text)
        self.assertEqual(text, "The computer found nothing at all.")

    def test_some_found_at_zero(self) -> None:
        self.assertEqual(self.run_sentence(1, 0), "The computer found 1 thing.")
        self.assertEqual(self.run_sentence(3, 0.004), "The computer found 3 things.")

    def test_other_thresholds_keep_the_existing_sentence(self) -> None:
        self.assertEqual(self.run_sentence(0, 0.5), "The computer found nothing it is at least 50% sure about.")
        self.assertEqual(self.run_sentence(2, 0.25), "The computer found 2 things it is at least 25% sure about.")


@skipUnless(NODE, "Node is not installed")
class MeasuredScoreWordingTests(TestCase):
    """The check screen words a score only from what the server measured (`confidence_wording`), never from fixed edges."""

    def wording(self) -> dict:
        from .tests_operating_points import synthetic_operating_points
        from .training import operating_points as op

        return op.confidence_wording({"operating_points": synthetic_operating_points()})

    def words(self, score: float, wording: dict) -> dict:
        script = f"""
        const vm = require('vm'); const fs = require('fs');
        const window = {{}}; const Athena = {{ ICONS: {{}} }};
        vm.runInNewContext(fs.readFileSync({json.dumps(str(STATIC / "checkscreen_core.js"))}, 'utf8'), {{ window, Athena, document: {{}} }});
        const w = window.CheckScreen.scoreWords({score}, {json.dumps(wording)});
        process.stdout.write(JSON.stringify({{ kind: w.kind, text: w.text }}));
        """
        return json.loads(subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True, timeout=30).stdout)

    def test_a_score_in_a_measured_band_is_shown_with_that_bands_own_label(self) -> None:
        wording = self.wording()
        self.assertTrue(wording["measured"])
        for band in wording["bands"]:
            if not band["enough"]:
                continue
            middle = round((band["low"] + band["high"]) / 2, 3)
            got = self.words(middle, wording)
            self.assertEqual(got["kind"], "measured")
            self.assertEqual(got["text"], f"{band['label'][0].upper()}{band['label'][1:]} · score {middle:.2f}")

    def test_band_edges_low_is_inclusive_high_is_exclusive_and_the_last_band_holds_one(self) -> None:
        wording = self.wording()
        bands = wording["bands"]
        self.assertGreater(len(bands), 2)
        for lower, upper in zip(bands, bands[1:]):
            edge = upper["low"]
            self.assertEqual(lower["high"], edge)
            self.assertTrue(self.words(edge, wording)["text"].startswith(upper["label"][0].upper() + upper["label"][1:]) or not upper["enough"])
            if upper["enough"] and lower["enough"] and lower["label"] != upper["label"]:
                self.assertNotIn(lower["label"][1:], self.words(edge, wording)["text"])
        last = bands[-1]
        self.assertEqual(self.words(1.0, wording)["kind"], "measured" if last["enough"] else "thin")
        self.assertIn(last["label"][1:], self.words(1.0, wording)["text"])

    def test_a_band_without_enough_checking_says_so_and_gives_no_measured_words(self) -> None:
        wording = self.wording()
        thin = json.loads(json.dumps(wording))
        thin["bands"][3].update({"enough": False, "label": "too few examples to say"})
        score = round((thin["bands"][3]["low"] + thin["bands"][3]["high"]) / 2, 3)
        got = self.words(score, thin)
        self.assertEqual(got["kind"], "thin")
        self.assertEqual(got["text"], f"score {score:.2f} · not enough checking to say how often this is right")
        self.assertNotIn("right about", got["text"])

    def test_without_a_measurement_the_number_is_the_computers_own_and_nothing_stronger(self) -> None:
        for wording in ({"measured": False, "bands": [], "note": "x"}, None, {}):
            got = self.words(0.82, wording)
            self.assertEqual(got, {"kind": "unchecked", "text": "score 0.82 (the computer’s own number, not checked)"})
        # a stored band list that is marked as not measured is not used, whatever it holds
        lie = {**self.wording(), "measured": False}
        self.assertEqual(self.words(0.95, lie)["kind"], "unchecked")

    def test_the_servers_note_is_what_the_screen_shows_and_the_old_fixed_words_are_gone_everywhere(self) -> None:
        old = ["Very" + " sure", "Fairly" + " sure", "Somewhat" + " sure", "Not very" + " sure"]
        for folder in (STATIC, TEMPLATES):
            for path in folder.rglob("*"):
                if path.suffix in {".js", ".html", ".css"}:
                    text = path.read_text()
                    for phrase in old:
                        self.assertNotIn(phrase, text, f"{path.name}: {phrase}")
        self.assertNotIn("sureness(", source("checkscreen_core.js"))
        self.assertIn("CS.scoreNote(render)", source("checkscreen.js"))
        self.assertIn("Not a final decision", (TEMPLATES / "projects" / "checkscreen.html").read_text())  # the banner stays


class BuilderSuggestionTests(TestCase):
    """The builder shows the server's plain sentence; the button exists only for a usable, measured value."""

    def test_the_button_needs_a_value_and_enough_data_and_nothing_is_applied_by_itself(self) -> None:
        js = source("checkscreen_builder.js")
        self.assertIn('typeof hint.value === "number" && hint.value >= 0 && hint.value <= 1 && hint.enough_data === true', js)
        self.assertIn("f.suggestUse.hidden = suggestion.value === null;", js)
        use = js[js.index("function useSuggestion()") : js.index("function fillForm(app)")]
        self.assertIn("markDirty", js)
        self.assertNotIn("onSave", use)  # filling the field never saves
        self.assertNotIn("CS.request", use)
        html = (TEMPLATES / "projects" / "checkscreen_builder.html").read_text()
        self.assertIn('id="cs-suggest-use" hidden>Use the suggested setting</button>', html)
        self.assertIn('id="cs-threshold" type="range" min="0" max="100"', html)  # the range the validator takes is unchanged

    def test_a_suggestion_belongs_to_the_saved_model_only(self) -> None:
        js = source("checkscreen_builder.js")
        self.assertIn("preview.model.id !== modelId", js)
        self.assertIn("preview = null; // the last screen's render model must not feed this screen's suggestion", js)


class Landmarks(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.banners: list[list[str]] = []
        self.forms: list[dict] = []
        self.skip_links: list[dict] = []
        self.mains = 0

    def handle_starttag(self, tag, attrs) -> None:
        attrs = dict(attrs)
        if tag == "header" or attrs.get("role") == "banner":
            self.banners.append(list(self.stack))
        if tag == "form":
            self.forms.append(attrs)
        if tag == "a" and "skip-link" in (attrs.get("class") or ""):
            self.skip_links.append(attrs)
        if tag == "main":
            self.mains += 1
        if tag not in {"br", "img", "input", "meta", "link", "hr"}:
            self.stack.append(tag)

    def handle_endtag(self, tag) -> None:
        if tag in self.stack:
            while self.stack and self.stack.pop() != tag:
                pass


def landmarks(html: str) -> Landmarks:
    parser = Landmarks()
    parser.feed(html)
    return parser


class LandmarkTests(TestCase):
    """XB-7: a banner landmark on the console, a skip link on sign-in, a name on every form."""

    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user("operator", is_superuser=True)

    def test_console_has_a_top_level_banner_and_one_main(self) -> None:
        self.client.force_login(self.user)
        page = landmarks(self.client.get("/").content.decode())
        top_level = [path for path in page.banners if not set(path) & {"main", "aside", "nav", "section", "article"}]
        self.assertEqual(len(top_level), 1, page.banners)
        self.assertEqual(page.mains, 1)

    def test_login_has_a_skip_link_a_main_and_a_banner(self) -> None:
        html = Client().get("/login/").content.decode()
        page = landmarks(html)
        self.assertEqual(len(page.skip_links), 1)
        self.assertEqual(page.skip_links[0].get("href"), "#main")
        self.assertRegex(html, r'<main[^>]*id="main"')
        self.assertEqual(len(page.banners), 1)
        self.assertLess(html.index("skip-link"), html.index("<header"))

    def test_every_form_has_an_accessible_name(self) -> None:
        self.client.force_login(self.user)
        pages = {"login": Client().get("/login/").content.decode(), "console": self.client.get("/").content.decode()}
        for name, html in pages.items():
            for attrs in landmarks(html).forms:
                with self.subTest(page=name, form=attrs.get("id") or attrs.get("action") or attrs.get("class")):
                    self.assertTrue(attrs.get("aria-label") or attrs.get("aria-labelledby"), attrs)

    def test_check_screen_templates_name_their_forms(self) -> None:
        for name in ("checkscreen_base.html", "checkscreen.html", "checkscreen_builder.html"):
            for attrs in landmarks((TEMPLATES / "projects" / name).read_text()).forms:
                with self.subTest(template=name, form=attrs.get("id") or attrs.get("action")):
                    self.assertTrue(attrs.get("aria-label") or attrs.get("aria-labelledby"), attrs)


SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "web-ci.yml"


class BrowserSmokeKitTests(TestCase):
    """The smoke kit exists, parses, and says what it fails on. Running it is the real test: `bash web/scripts/run_browser_smoke.sh`."""

    def test_files_exist_and_the_runner_is_executable(self) -> None:
        for name in ("browser_smoke.mjs", "run_browser_smoke.sh", "seed_browser_smoke.py"):
            self.assertTrue((SCRIPTS / name).is_file(), name)
        self.assertTrue((SCRIPTS / "run_browser_smoke.sh").stat().st_mode & 0o111)

    @skipUnless(NODE, "Node is not installed")
    def test_the_smoke_script_parses(self) -> None:
        subprocess.run([NODE, "--check", str(SCRIPTS / "browser_smoke.mjs")], check=True, capture_output=True, timeout=30)

    @skipUnless(shutil.which("bash"), "bash is not installed")
    def test_the_runner_parses(self) -> None:
        subprocess.run(["bash", "-n", str(SCRIPTS / "run_browser_smoke.sh")], check=True, capture_output=True, timeout=30)

    def test_the_script_fails_on_every_class_of_dead_page_it_promises(self) -> None:
        js = (SCRIPTS / "browser_smoke.mjs").read_text()
        for needle in ('page.on("pageerror"', "securitypolicyviolation", 'message.type() === "error"', 'page.on("requestfailed"',
                       'page.on("response"', "project-count", "data-ready", "unnamed controls", "under 16px", "sideways",
                       "Tab", "Shift+Tab", "Escape", "process.exit(1)"):
            with self.subTest(needle=needle):
                self.assertIn(needle, js)

    def test_the_runner_uses_a_disposable_database_and_a_loopback_port_and_cleans_up(self) -> None:
        sh = (SCRIPTS / "run_browser_smoke.sh").read_text()
        for needle in ("mktemp -d", "ATHENA_DATA_HOME=", "127.0.0.1", "s.bind((\"127.0.0.1\", 0))", "trap cleanup EXIT", "kill \"$SERVER_PID\"", "rm -rf \"$TMP\""):
            with self.subTest(needle=needle):
                self.assertIn(needle, sh)

    def test_the_seed_refuses_to_run_without_a_scratch_data_home(self) -> None:
        seed = (SCRIPTS / "seed_browser_smoke.py").read_text()
        self.assertIn("Refusing to run: set ATHENA_DATA_HOME", seed)

    def test_playwright_is_not_a_runtime_dependency(self) -> None:
        for name in ("requirements.txt", "requirements-autolabel.txt"):
            self.assertNotIn("playwright", (SCRIPTS.parent / name).read_text().lower())
        self.assertFalse((SCRIPTS.parent / "package.json").exists())

    def test_ci_job_is_optional_and_limited_to_dispatch_and_console_front_end_changes(self) -> None:
        text = WORKFLOW.read_text()
        job = text[text.index("  browser-smoke:") : text.index("  image:")]
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("github.event_name == 'workflow_dispatch' || github.event_name == 'pull_request'", job)
        self.assertIn("continue-on-error: false", job)
        self.assertIn("projects/static/", job)
        self.assertIn("projects/templates/", job)
        self.assertIn("playwright@1", job)  # installed outside the repo, never a repo dependency
        self.assertNotIn("npm install playwright", job)
        self.assertIn("run_browser_smoke.sh chromium", job)
