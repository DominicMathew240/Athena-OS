"""The operations console page: scope setup, pin pickers and release candidates."""

import json

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from .tests import grant


def make_scope(client, name="Main shop", timezone_name="Asia/Kuala_Lumpur"):
    """Create a site, one recorded-video source, and an operation through the API."""
    site = client.post(
        "/api/operation-sites",
        json.dumps({"name": name, "timezone_name": timezone_name}),
        content_type="application/json",
    ).json()["site"]
    source = client.post(
        "/api/operation-sources",
        json.dumps({"site_id": site["id"], "name": "Front entrance", "kind": "recorded_video"}),
        content_type="application/json",
    ).json()["source"]
    operation = client.post(
        "/api/operations",
        json.dumps({"name": "Shop entrance", "site_id": site["id"], "source_id": source["id"]}),
        content_type="application/json",
    ).json()["operation"]
    return site, source, operation


class DraftAttachTests(TestCase):
    """An unbound draft can join its operation when it is saved, exactly once."""

    def setUp(self):
        self.user = get_user_model().objects.create_user("attacher")
        grant(self.user, "labeler")
        self.client.force_login(self.user)
        self.line = {
            "start": [0.1, 0.5],
            "end": [0.9, 0.5],
            "name": "Front door line",
            "positive_label": "Entering",
            "negative_label": "Leaving",
        }
        _, _, self.operation = make_scope(self.client)

    def put(self, path, payload, status=200):
        response = self.client.put(path, json.dumps(payload), content_type="application/json")
        self.assertEqual(response.status_code, status, response.content)
        return response.json()

    def test_unbound_draft_can_be_attached_on_save_and_only_once(self):
        draft = self.client.post(
            "/api/operation-drafts",
            json.dumps({"name": "Shop door", "line": self.line}),
            content_type="application/json",
        ).json()["draft"]
        self.assertIsNone(draft["operation_id"])
        url = f"/api/operation-drafts/{draft['id']}"
        result = self.put(
            url,
            {
                "name": "Shop door",
                "line": self.line,
                "revision": draft["revision"],
                "operation_id": self.operation["id"],
            },
        )
        self.assertEqual(result["draft"]["operation_id"], self.operation["id"])
        # The binding is permanent: a second attach attempt is refused, not a rebind.
        self.put(
            url,
            {
                "name": "Shop door",
                "line": self.line,
                "revision": result["draft"]["revision"],
                "operation_id": self.operation["id"],
            },
            status=400,
        )
        self.assertEqual(self.client.get(url).json()["draft"]["operation_id"], self.operation["id"])

    def test_attaching_another_persons_operation_is_refused(self):
        stranger = get_user_model().objects.create_user("attach-stranger")
        grant(stranger, "labeler")
        stranger_client = Client()
        stranger_client.force_login(stranger)
        _, _, foreign_operation = make_scope(stranger_client, name="Foreign shop")
        draft = self.client.post(
            "/api/operation-drafts",
            json.dumps({"name": "Shop door", "line": self.line}),
            content_type="application/json",
        ).json()["draft"]
        response = self.client.put(
            f"/api/operation-drafts/{draft['id']}",
            json.dumps(
                {
                    "name": "Shop door",
                    "line": self.line,
                    "revision": draft["revision"],
                    "operation_id": foreign_operation["id"],
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404, response.content)


class OperationsPageTests(TestCase):
    """The operations page shows the setup, pin and release areas alongside the draft editor."""

    def setUp(self):
        self.user = get_user_model().objects.create_user("setup-user")
        grant(self.user, "labeler")
        self.client.force_login(self.user)

    def test_page_includes_the_operation_setup_surfaces(self):
        page = self.client.get("/operations")
        self.assertEqual(page.status_code, 200)
        for needle in (
            'id="operation-setup"',
            'id="pin-section"',
            'id="creator-section"',
            'id="release-section"',
            'id="site-create"',
            'id="source-create"',
            'id="operation-create"',
            'id="attach-area"',
            'id="pin-fields"',
            'id="operation-creators"',
            'id="release-area"',
            "operation-versions.js",
        ):
            with self.subTest(needle=needle):
                self.assertContains(page, needle)

    def test_page_includes_the_guided_setup_surfaces(self):
        page = self.client.get("/operations")
        self.assertEqual(page.status_code, 200)
        for needle in (
            'id="guide-card"',
            'id="guide-steps"',
            'id="guide-stage"',
            'id="guide-exit"',
            'id="draft-section"',
            "operation-setup-guide.js",
        ):
            with self.subTest(needle=needle):
                self.assertContains(page, needle)

    def test_scope_forms_are_gated_to_the_labeler_role(self):
        page = self.client.get("/operations")
        for needle in (
            'id="site-create" data-needs="label"',
            'id="source-create" data-needs="label"',
            'id="operation-create" data-needs="label"',
        ):
            with self.subTest(needle=needle):
                self.assertContains(page, needle)

    def test_viewer_sees_the_page_but_the_forms_say_why_they_cannot_act(self):
        grant(self.user, "viewer")
        page = self.client.get("/operations")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'id="operation-setup"')


class ReplaySurfacePageTests(TestCase):
    """The shared replay surface (T13) is the only playback control on the recording step."""

    def setUp(self):
        self.user = get_user_model().objects.create_user("replay-user")
        grant(self.user, "labeler")
        self.client.force_login(self.user)

    def test_recording_step_replaces_native_controls_with_the_replay_surface(self):
        page = self.client.get("/operations")
        self.assertEqual(page.status_code, 200)
        for needle in (
            'data-testid="replay-surface"',
            'id="recording-preview"',
            'class="replay-overlay"',
            'data-testid="replay-play"',
            'data-testid="replay-time"',
            'data-testid="replay-duration"',
            'data-testid="replay-toggles"',
            'class="replay-scrub"',
            'aria-label="Move through the recording"',
            "replay-surface.js",
        ):
            with self.subTest(needle=needle):
                self.assertContains(page, needle)
        # The preview video must not carry native controls; the surface owns play, pause and seeking.
        self.assertNotContains(page, 'id="recording-preview" class="recording-preview" controls')

    def test_replay_surface_script_loads_before_the_draft_editor_uses_it(self):
        page = self.client.get("/operations")
        body = page.content.decode()
        self.assertIn("replay-surface.js", body)
        self.assertIn("operation-drafts.js", body)
        self.assertLess(body.index("replay-surface.js"), body.index("operation-drafts.js"))


class LineDirectionSurfaceTests(TestCase):
    """T14: the line step names the line, labels its sides, reverses by button and shows a crossing example."""

    def setUp(self):
        self.user = get_user_model().objects.create_user("line-direction-user")
        grant(self.user, "labeler")
        self.client.force_login(self.user)

    def test_line_step_includes_naming_direction_and_crossing_example_surfaces(self):
        page = self.client.get("/operations")
        self.assertEqual(page.status_code, 200)
        for needle in (
            'id="line-name"',
            'id="line-reverse"',
            'data-testid="line-reverse"',
            'id="line-direction"',
            'id="line-preview"',
            'data-testid="line-preview-canvas"',
            'data-testid="line-preview-step"',
            'data-testid="line-preview-play"',
            'id="line-preview-text"',
            'for="line-positive">Movement towards the arrow',
            'for="line-negative">Movement against the arrow',
        ):
            with self.subTest(needle=needle):
                self.assertContains(page, needle)
        # The copy must present side labels as the user's own names, not a promise
        # about which side is "in".
        self.assertContains(page, "Athena does not decide which side means what")
        self.assertContains(page, "not a detection and not a promise about counts")
        # Reversal stays a labeler-gated action like every other mutation on this page.
        self.assertContains(page, 'id="line-reverse" type="button" data-needs="label"')


class LineAccessibilitySurfaceTests(TestCase):
    """T15: the line step carries keyboard alternatives, a text summary, and undo/redo
    surfaces in the server-rendered markup, so no essential geometry action needs a
    mouse or colour recognition."""

    def setUp(self):
        self.user = get_user_model().objects.create_user("line-a11y-user")
        grant(self.user, "labeler")
        self.client.force_login(self.user)

    def test_line_step_includes_keyboard_endpoint_nudging_surfaces(self):
        page = self.client.get("/operations")
        self.assertEqual(page.status_code, 200)
        for needle in (
            'id="line-nudge"',
            'id="line-select-start"',
            'id="line-select-end"',
            'id="line-move-left"',
            'id="line-move-right"',
            'id="line-move-up"',
            'id="line-move-down"',
        ):
            with self.subTest(needle=needle):
                self.assertContains(page, needle)
        # The chosen-endpoint buttons expose their state as text, never colour alone.
        self.assertContains(page, 'id="line-select-start" type="button" aria-pressed="false"')
        self.assertContains(page, 'id="line-select-end" type="button" aria-pressed="false"')
        self.assertContains(page, 'role="group" aria-labelledby="line-nudge-point-label"')
        self.assertContains(page, 'role="group" aria-labelledby="line-nudge-move-label"')
        # The copy states the fine and coarse keyboard steps and that dragging is
        # never required.
        self.assertContains(page, "fine step of 0.001")
        self.assertContains(page, "coarse step of 0.01")
        self.assertContains(page, "Dragging an endpoint is never required")

    def test_line_step_includes_undo_redo_and_summary_surfaces(self):
        page = self.client.get("/operations")
        self.assertEqual(page.status_code, 200)
        for needle in (
            'id="line-undo"',
            'id="line-redo"',
            'data-testid="line-undo"',
            'data-testid="line-redo"',
            'id="line-history"',
            'id="line-summary"',
            'id="line-summary-box"',
            'data-testid="line-summary"',
        ):
            with self.subTest(needle=needle):
                self.assertContains(page, needle)
        # The summary is a live region: screen readers announce geometry changes,
        # and the undo/redo copy is honest about the saved state boundary.
        self.assertContains(page, 'id="line-summary" class="line-summary-text" role="status" aria-live="polite"')
        self.assertContains(page, "undo never reaches past the last saved state")
        # The numeric endpoint fields and the editing canvas are labelled.
        self.assertContains(page, 'for="line-start-x">Start: left to right (%)')
        self.assertContains(page, 'for="line-end-y">End: top to bottom (%)')
        self.assertContains(
            page, 'id="line-canvas" class="line-canvas" width="640" height="360" tabindex="0" role="img"'
        )

    def test_line_step_clear_and_nudge_controls_are_labeler_gated(self):
        page = self.client.get("/operations")
        for needle in (
            'id="line-clear" type="button" data-needs="label"',
            'id="line-undo" type="button" data-testid="line-undo"',
            'id="line-move-left" type="button" data-testid="line-move-left"',
        ):
            with self.subTest(needle=needle):
                self.assertContains(page, needle)
