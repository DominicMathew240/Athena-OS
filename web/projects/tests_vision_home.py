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
