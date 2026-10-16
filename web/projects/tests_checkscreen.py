"""The builder page and the operator page for inference apps: shells only, the API does the work."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from django.contrib.staticfiles import finders
from django.test import Client
from django.urls import reverse
from django.utils.html import escape

from .inference import schema
from .models import InferenceApp, Project
from .tests_inference import InferenceBase

STATIC = Path(__file__).parent / "static" / "projects"
SCRIPTS = ("checkscreen_core.js", "checkscreen.js", "checkscreen_builder.js")


class PageAccessTests(InferenceBase):
    def setup_url(self, project=None) -> str:
        return f"/projects/{(project or self.project).id}/check-screen/setup"

    def test_routes_are_named_and_match_the_documented_addresses(self) -> None:
        self.assertEqual(reverse("checkscreen-setup", args=[self.project.id]), self.setup_url())
        self.assertEqual(reverse("checkscreen-operator", args=["lane-4"]), "/check/lane-4")

    def test_both_pages_need_sign_in_and_send_browsers_to_the_login_page(self) -> None:
        app = self.make_app(slug="lane-4")
        anonymous = Client()
        for url in (self.setup_url(), f"/check/{app['slug']}"):
            with self.subTest(url=url):
                response = anonymous.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response["Location"].startswith("/login/?next="), response["Location"])

    def test_pages_are_get_only(self) -> None:
        app = self.make_app(slug="lane-4")
        for url in (self.setup_url(), f"/check/{app['slug']}"):
            self.assertEqual(self.client.post(url).status_code, 405)

    def test_builder_renders_for_an_image_project_and_names_things_plainly(self) -> None:
        response = self.client.get(self.setup_url())
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("Set up your check screen", html)
        self.assertIn(f'data-project-id="{self.project.id}"', html)
        for words in ("Flag more things", "Flag fewer things", "A person must always decide", "When nothing was found", "Try it with a picture"):
            self.assertIn(words, html)
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_builder_only_opens_for_image_projects_that_exist(self) -> None:
        text = Project.objects.create(name="Docs", modality="text", task="Tag", labels=["a"])
        self.assertEqual(self.client.get(self.setup_url(text)).status_code, 404)
        self.assertEqual(self.client.get(f"/projects/{uuid.uuid4()}/check-screen/setup").status_code, 404)

    def test_builder_preselects_an_app_only_from_the_same_project(self) -> None:
        app = self.make_app(slug="lane-4")
        other = Project.objects.create(name="Other", modality="image", task="Detect", labels=["damaged"])
        self.assertIn(f'data-initial-app="{app["id"]}"', self.client.get(self.setup_url() + f"?app={app['id']}").content.decode())
        self.assertIn('data-initial-app=""', self.client.get(self.setup_url(other) + f"?app={app['id']}").content.decode())
        self.assertIn('data-initial-app=""', self.client.get(self.setup_url() + "?app=not-a-uuid").content.decode())

    def test_operator_page_shows_the_permanent_disclaimer_before_any_script_runs(self) -> None:
        app = self.make_app(slug="lane-4", name="Bag check, lane 4")
        response = self.client.get("/check/lane-4")
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn(escape(schema.DISCLAIMER), html)
        self.assertIn("Bag check, lane 4", html)
        self.assertIn(f'data-app-id="{app["id"]}"', html)
        self.assertIn(f'data-project-id="{self.project.id}"', html)
        self.assertIn("Not a final decision.", html)
        self.assertIn("do <strong>not</strong> change the computer model", html)

    def test_operator_page_for_an_archived_app_still_loads_so_it_can_say_why(self) -> None:
        app = self.make_app(slug="lane-4")
        self.assertEqual(self.client.patch(self.apps_url(f"/{app['id']}"), data='{"status": "archived"}', content_type="application/json").status_code, 200)
        self.assertEqual(self.client.get("/check/lane-4").status_code, 200)

    def test_unknown_or_malformed_slugs_are_404(self) -> None:
        for slug in ("missing", "a" * 90):
            self.assertEqual(self.client.get(f"/check/{slug}").status_code, 404)
        self.assertEqual(self.client.get("/check/Bad_Slug!").status_code, 404)

    def test_operator_page_for_a_non_image_project_is_404(self) -> None:
        app = self.make_app(slug="lane-4")
        Project.objects.filter(pk=self.project.pk).update(modality="text")
        self.assertEqual(self.client.get(f"/check/{app['slug']}").status_code, 404)


class StrictCspTests(InferenceBase):
    """The site's CSP forbids inline script and style, so the pages must not rely on either."""

    def pages(self) -> list[str]:
        app = self.make_app(slug="lane-4")
        return [
            self.client.get(f"/projects/{self.project.id}/check-screen/setup").content.decode(),
            self.client.get(f"/check/{app['slug']}").content.decode(),
        ]

    def test_pages_carry_no_inline_script_style_or_handlers(self) -> None:
        for html in self.pages():
            for tag in re.findall(r"<script\b[^>]*>", html):
                self.assertIn(" src=", tag, tag)
            self.assertNotRegex(html, r"<script\b[^>]*>(?!\s*</script>)")  # nothing between the tags
            self.assertNotRegex(html, r"\son[a-z]+\s*=")
            self.assertNotRegex(html, r"\sstyle\s*=")
            self.assertNotIn("<style", html)
            self.assertNotIn("javascript:", html)

    def test_csp_header_has_no_unsafe_inline(self) -> None:
        response = self.client.get("/projects/%s/check-screen/setup" % self.project.id)
        policy = response.headers.get("Content-Security-Policy", "")
        self.assertIn("script-src 'self'", policy)
        self.assertNotIn("unsafe-inline", policy)

    def test_scripts_never_write_markup(self) -> None:
        for name in SCRIPTS:
            source = (STATIC / name).read_text()
            for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", 'setAttribute("style"', "setAttribute('style'", "cssText"):
                self.assertNotIn(banned, source, f"{name} uses {banned}")

    def test_static_files_are_findable_by_the_static_pipeline(self) -> None:
        for name in (*SCRIPTS, "checkscreen.css", "console.css"):
            self.assertIsNotNone(finders.find(f"projects/{name}"), name)


class ClientContractTests(InferenceBase):
    """The scripts must call only what inference_views exposes, with the exact vocabulary of the API."""

    def test_script_paths_match_the_real_routes(self) -> None:
        pid, aid, oid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        core = (STATIC / "checkscreen_core.js").read_text()
        builder = (STATIC / "checkscreen_builder.js").read_text()
        route = lambda name, *args: reverse(name, args=args)
        apps = route("inference-apps", pid)
        self.assertIn("`/api/projects/${projectId}/inference/apps`", core)
        self.assertEqual(apps, f"/api/projects/{pid}/inference/apps")
        for name, suffix in (
            ("inference-app-preview", "/preview"), ("inference-app-run", "/run"),
        ):
            self.assertEqual(route(name, pid, aid), f"{apps}/{aid}{suffix}")
            self.assertIn("`${base}" + suffix + "`", core)
        for name, suffix, js in (
            ("inference-observation-detail", "", "`${base}/observations/${id}`"),
            ("inference-observation-image", "/image", "`${base}/observations/${id}/image`"),
            ("inference-observation-feedback", "/feedback", "`${base}/observations/${id}/feedback`"),
            ("inference-observation-decision", "/decision", "`${base}/observations/${id}/decision`"),
        ):
            self.assertEqual(route(name, pid, aid, oid), f"{apps}/{aid}/observations/{oid}{suffix}")
            self.assertIn(js, core)
        self.assertIn("`${appsPath}/${currentApp.id}`", builder)
        self.assertIn("`/api/projects/${projectId}/models`", builder)
        self.assertEqual(route("model-list", pid), f"/api/projects/{pid}/models")

    def test_vocabulary_matches_the_schema(self) -> None:
        core = (STATIC / "checkscreen_core.js").read_text()
        for key in schema.DECISION_OPTIONS:
            self.assertRegex(core, rf"\b{key}:\s*\{{ label:")
        for key in (*schema.STATE_KEYS, "processing", "failed"):
            self.assertRegex(core, rf"\b{key}:\s*(\"|Athena\.ICONS\.)")
        for content_type in schema.ACCEPTED_INPUTS:
            self.assertIn(content_type, core)
        builder = (STATIC / "checkscreen_builder.js").read_text()
        for key in ("task_title", "accepted_inputs", "displayed_labels", "confidence_threshold", "status_texts",
                    "decision_controls", "required_note_on_override", "human_review_required"):
            self.assertIn(key, builder)
            self.assertIn(key, schema.CONFIG_KEYS | {"required_note_on_override"})
        self.assertEqual(set(re.findall(r"\b(clear|flagged|needs_review)\b:", builder)) , set(schema.STATE_KEYS))

    def test_builder_defaults_are_accepted_by_the_real_validation(self) -> None:
        """The values the form starts with must be creatable without editing anything."""
        payload = {
            "name": "Bag check", "model_version_id": str(self.model.id),
            "config": {
                "task_title": "Bag check", "accepted_inputs": list(schema.ACCEPTED_INPUTS),
                "displayed_labels": list(self.model.label_names), "confidence_threshold": 0.5,
                "status_texts": {"clear": "Nothing found", "flagged": "Something found", "needs_review": "Please look closely"},
                "decision_controls": {"options": list(schema.DECISION_OPTIONS), "required_note_on_override": True},
                "human_review_required": True,
            },
        }
        response = self.post(self.apps_url(), payload)
        self.assertEqual(response.status_code, 201, response.content)
        app = InferenceApp.objects.get()
        self.assertEqual(app.config["status_texts"]["needs_review"], "Please look closely")
