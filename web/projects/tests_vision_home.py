"""The signed-in front view reuses the landing page's photographic checks."""

from __future__ import annotations

from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase

from .vision import catalog

STATIC = Path(__file__).resolve().parent / "static" / "projects"
TEMPLATES = Path(__file__).resolve().parent / "templates" / "projects"
LANDING_COVERS = {
    "corrosion": "projects/landing/refinery-skyline.jpg",
    "parking": "projects/landing/parking-aerial.jpg",
    "footfall": "projects/landing/store-entrance.jpg",
    "ppe": "projects/landing/safety-worker.jpg",
    "defects": "projects/landing/welding.jpg",
    "leaks": "projects/landing/refinery-night.jpg",
}


class LandingCoversOnFrontViewTests(TestCase):
    def setUp(self) -> None:
        user = get_user_model().objects.create_user("operator")
        self.client.force_login(user)

    def test_photo_counting_examples_are_clearly_limited_to_snapshots(self) -> None:
        home = self.client.get("/")
        self.assertContains(home, "Photo inspections explain what is visible in the pictures")
        self.assertContains(home, "not live monitoring or a complete count of arrivals and departures")
        self.assertContains(home, "Train a custom detector")

        self.client.logout()
        landing = self.client.get("/")
        self.assertContains(landing, "Videos are sampled into six still frames")
        self.assertContains(landing, "not a complete crossing count or live monitor")
        self.assertContains(landing, "A snapshot of people and queues")
        self.assertContains(landing, "does not count every crossing or measure waiting time")

    def test_sampled_video_limit_is_visible_on_setup_and_docs(self) -> None:
        for path in ('/inspect/new', '/docs'):
            with self.subTest(path=path):
                self.assertContains(self.client.get(path), 'not a complete count of crossings')
        footfall = catalog.BY_KEY['footfall']
        self.assertNotIn('Walking in', footfall['counts'])
        self.assertNotIn('Walking out', footfall['counts'])
        self.assertIn('cannot establish', footfall['focus'])
        self.assertNotIn('"Walking in"', (STATIC / 'landing.js').read_text())

    def test_each_ready_made_check_has_the_landing_photo(self) -> None:
        by_key = {item["key"]: item for item in catalog.TEMPLATES}
        self.assertEqual(set(by_key), set(LANDING_COVERS))
        for key, cover in LANDING_COVERS.items():
            with self.subTest(key=key):
                self.assertEqual(by_key[key]["cover"], cover)
                self.assertTrue((STATIC.parent / cover).is_file(), cover)

    def test_signed_in_home_shows_those_photos_and_opens_the_matching_check(self) -> None:
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        html = page.content.decode()
        self.assertIn("What do you want to check today?", html)
        for key, cover in LANDING_COVERS.items():
            with self.subTest(key=key):
                self.assertIn(cover.split("/")[-1], html)
                self.assertIn(f"/inspect/new?template={key}", html)
        self.assertIn("case-card--hero", html)
        self.assertIn('data-testid="template-card-corrosion"', html)

    def test_new_inspection_picker_draws_the_same_photos(self) -> None:
        js = (STATIC / "inspect.js").read_text()
        self.assertIn("t.cover", js)
        self.assertIn("<img", js)
        public = {item["key"]: item for item in catalog.all_public()}
        for key, cover in LANDING_COVERS.items():
            with self.subTest(key=key):
                self.assertEqual(public[key]["cover"], cover)

    def test_landing_page_and_home_share_the_same_case_photos(self) -> None:
        landing = (TEMPLATES / "landing.html").read_text()
        home = self.client.get("/").content.decode()
        for filename in (
            "refinery-skyline.jpg",
            "parking-aerial.jpg",
            "store-entrance.jpg",
            "safety-worker.jpg",
            "welding.jpg",
        ):
            with self.subTest(file=filename):
                self.assertIn(filename, landing)
                self.assertIn(filename, home)

    def test_home_links_to_docs_wiki_and_docs_page_renders_guides(self) -> None:
        home_html = self.client.get("/").content.decode()
        self.assertIn('data-testid="home-docs-link"', home_html)
        self.assertIn('data-testid="nav-docs-link"', home_html)

        docs_resp = self.client.get("/docs")
        self.assertEqual(docs_resp.status_code, 200)
        docs_html = docs_resp.content.decode()
        self.assertIn("Athena OS Docs Wiki", docs_html)
        self.assertIn('data-testid="docs-search-input"', docs_html)
        self.assertIn('data-testid="docs-toc"', docs_html)
        self.assertIn("projects/docs.js", docs_html)
        self.assertNotIn("<script>", docs_html)
        for section_id in (
            'id="overview"',
            'id="setup-signin"',
            'id="connect-ai"',
            'id="run-inspection"',
            'id="regions-reports"',
            'id="custom-detectors"',
            'id="check-screens"',
            'id="access-roles"',
            'id="cli-sdk"',
            'id="faq"',
        ):
            self.assertIn(section_id, docs_html)

    def test_unregistered_users_and_crawlers_can_access_docs_seo_aeo_and_sitemap(self) -> None:
        self.client.logout()
        landing_html = self.client.get("/").content.decode()
        self.assertIn('data-testid="landing-nav-docs"', landing_html)

        docs_resp = self.client.get("/docs")
        self.assertEqual(docs_resp.status_code, 200)
        html = docs_resp.content.decode()
        self.assertIn('data-testid="public-docs-nav"', html)
        self.assertIn('<meta name="robots" content="index, follow">', html)
        self.assertIn('<link rel="canonical"', html)
        self.assertIn('itemtype="https://schema.org/TechArticle"', html)
        self.assertIn('itemtype="https://schema.org/FAQPage"', html)
        self.assertNotIn("<script>", html)

        robots = self.client.get("/robots.txt")
        self.assertEqual(robots.status_code, 200)
        self.assertIn("Allow: /docs", robots.content.decode())
        self.assertIn("Sitemap:", robots.content.decode())

        sitemap = self.client.get("/sitemap.xml")
        self.assertEqual(sitemap.status_code, 200)
        self.assertIn("/docs</loc>", sitemap.content.decode())

    def test_landing_page_presents_multimodal_positioning_no_code_outcomes_and_roadmap(self) -> None:
        self.client.logout()
        landing = self.client.get("/")
        self.assertEqual(landing.status_code, 200)
        html = landing.content.decode()
        for expected in (
            "Athena OS · Plain-English AI for Everyday Operations",
            'data-testid="landing-teach-workflow"',
            'data-testid="landing-outcome-vision"',
            'data-testid="landing-outcome-audio"',
            'data-testid="landing-outcome-ocr"',
            'data-testid="landing-outcome-documents"',
            'data-testid="landing-outcome-movements"',
            'data-testid="landing-outcome-spreadsheets"',
            "Listen for Machine Faults",
            "Short motor &amp; pump sound recordings",
            "Makes sure real machine faults aren’t missed",
            'data-testid="landing-six-steps"',
            'data-testid="landing-stage-stepper"',
            'data-testid="landing-stage-raw"',
            'data-testid="landing-stage-label"',
            'data-testid="landing-stage-eval"',
            'data-testid="landing-stage-deploy"',
            'data-testid="landing-multimodal-visualizer"',
            'id="lp-vis-interactive"',
            'data-testid="landing-vis-inspector"',
            'data-testid="landing-modality-gallery"',
            'data-mini-modality="audio"',
            'data-mini-modality="ocr"',
            'data-mini-modality="documents"',
            'data-mini-modality="movements"',
            'data-mini-modality="spreadsheets"',
            'data-mini-modality="vision"',
            'data-testid="landing-roadmap-section"',
            'data-testid="landing-fusion-showcase"',
            'id="lp-fusion-tabs"',
            'id="lp-fusion-diagram"',
            'lp-site-monitor',
            'data-testid="landing-theme-toggle"',
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, html)
        landing_js = (STATIC / "landing.js").read_text()
        self.assertIn("lp-outcome-tabs", landing_js)
        self.assertIn("ArrowRight", landing_js)
        self.assertIn("renderStudio", landing_js)
        self.assertIn("renderGalleryThumbs", landing_js)
        self.assertIn("renderFusion", landing_js)
        self.assertIn("Safe limit", landing_js)
        self.assertIn("contentvisibilityautostatechange", landing_js)

    def test_home_and_console_surface_outcome_first_training_recipes(self) -> None:
        home_html = self.client.get("/").content.decode()
        for expected in (
            'data-testid="home-new-model-section"',
            'id="home-outcome-tabs"',
            "What do you want Athena to learn?",
            "Acoustic Event Detection",
            "WAV recordings + event labels",
            "Missed sound events and false alarms",
            "/detectors?recipe=audio",
        ):
            with self.subTest(home=expected):
                self.assertIn(expected, home_html)
        self.assertIn("home-outcome-tabs", (STATIC / "home.js").read_text())

        console_html = self.client.get("/detectors").content.decode()
        for expected in (
            'id="project-recipe-picker"',
            'id="project-recipe-note"',
            'data-testid="console-recipe-vision"',
            'data-testid="console-recipe-audio"',
            'data-testid="console-recipe-ocr"',
            'data-testid="console-recipe-spreadsheets"',
            'data-testid="console-recipe-signals"',
        ):
            with self.subTest(console=expected):
                self.assertIn(expected, console_html)
        console_js = (STATIC / "console.js").read_text()
        self.assertIn("applyProjectRecipe", console_js)
        self.assertIn('params.get("recipe")', console_js)

    def test_docs_wiki_includes_multimodal_roadmap_and_delivery_waves_for_all_visitors(self) -> None:
        for signed_in in (True, False):
            if not signed_in:
                self.client.logout()
            else:
                self.client.force_login(get_user_model().objects.get(username="operator"))
            html = self.client.get("/docs").content.decode()
            with self.subTest(signed_in=signed_in):
                for expected in (
                    'id="multimodal-roadmap"',
                    'id="delivery-waves"',
                    'data-toc="multimodal-roadmap"',
                    'data-toc="delivery-waves"',
                    "OS-CORE-001",
                    "OS-VIS-001",
                    "OS-AUD-001",
                    "OS-OCR-001",
                    "OS-TAB-001",
                    "Athena Forge",
                    "Athena Lab",
                    "Athena Nodes",
                    "18 of 65 tasks locally verified",
                    "Text classification and signal classification merged",
                    "Event F1, missed events, false alarms per hour",
                ):
                    self.assertIn(expected, html)
