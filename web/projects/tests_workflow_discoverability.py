"""Existing inspection and training routes stay discoverable (T07).

The operations console must not strand the image workflow: every screen a
labeler, trainer or operator already uses stays one navigation hop away, and
task-linked help explains how operations relate to inspections, custom
detectors and check screens — including what operations cannot do yet.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .tests import grant


class WorkflowRoutesStayReachableTests(TestCase):
    """Each screen of the existing image workflow answers 200 for a signed-in user."""

    def setUp(self):
        self.user = get_user_model().objects.create_user("flow-reader")
        grant(self.user, "viewer")
        self.client.force_login(self.user)

    def test_image_workflow_screens_answer(self):
        pages = {
            "home": "/",
            "new inspection": reverse("inspect-new"),
            "inspections list": reverse("inspections-page"),
            "custom detectors (import, annotate, dataset export, Teach, review)": reverse("dashboard"),
            "AI model settings": reverse("ai-settings"),
            "operations": reverse("operations-page"),
            "docs wiki": reverse("docs-page"),
        }
        for name, url in pages.items():
            with self.subTest(page=name):
                self.assertEqual(self.client.get(url).status_code, 200, name)

    def test_operations_page_keeps_its_private_scope(self):
        # The operations console is sign-in only; an anonymous visitor is bounced.
        response = self.client.get(reverse("operations-page"))
        self.assertEqual(response.status_code, 200)
        self.client.logout()
        response = self.client.get(reverse("operations-page"))
        self.assertIn(response.status_code, (302, 403))


class NavigationLinksTheWorkflowsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("nav-reader")
        grant(self.user, "viewer")
        self.client.force_login(self.user)

    def test_sidebar_links_every_workflow_including_operations(self):
        html = self.client.get("/operations").content.decode()
        for needle in (
            'data-testid="nav-home-link"',
            'data-testid="nav-inspections-link"',
            'data-testid="nav-detectors-link"',
            'data-testid="nav-operations-link"',
            'data-testid="nav-ai-settings-link"',
            'data-testid="nav-docs-link"',
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, html)
        # The operations page marks its own sidebar entry as the current page.
        self.assertIn('data-testid="nav-operations-link" aria-current="page"', html)

    def test_home_still_links_to_operations_and_detectors(self):
        html = self.client.get("/").content.decode()
        self.assertIn("People counting drafts", html)
        self.assertIn('data-testid="template-card-detectors"', html)


class TaskLinkedHelpExplainsOperationsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("help-reader")
        grant(self.user, "viewer")
        self.client.force_login(self.user)

    def test_operations_page_explains_its_relationship_to_the_image_workflow(self):
        page = self.client.get("/operations")
        self.assertEqual(page.status_code, 200)
        for needle in (
            'data-testid="operations-relationship"',
            "photo inspection",
            "custom detector",
            "cannot count people",  # the honest-limit sentence in the header stays
        ):
            with self.subTest(needle=needle):
                self.assertContains(page, needle)

    def test_docs_wiki_has_an_operations_section_for_every_visitor(self):
        self.client.force_login(self.user)
        for signed_in in (True, False):
            if not signed_in:
                self.client.logout()
            html = self.client.get("/docs").content.decode()
            with self.subTest(signed_in=signed_in):
                for needle in (
                    'id="operations"',
                    'data-toc="operations"',
                    "People counting operations",
                    "how many people crossed a line",
                    "four-corner polygon bays with stable identities",
                    "an approved",  # detector pinning
                    "custom detector",
                    "recorded replay analysis, not a live count",
                    "operator boards are not built yet",
                    "encrypted",
                ):
                    self.assertIn(needle, html)

    def test_docs_overview_names_all_three_workflows(self):
        self.client.force_login(self.user)
        html = self.client.get("/docs").content.decode()
        for needle in (
            "1. Instant visual inspections",
            "2. No-code custom model training",
            "3. People counting operations",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, html)
