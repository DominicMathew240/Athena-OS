"""The administrator's switch for ready-made model downloads (``/api/console/training-downloads``).

Prototype. Nothing here downloads anything: the web process never fetches weights, the switch only stores a
setting that the teaching helper reads, and every test that gets near a fetch replaces ``_fetch``. Weights files
are junk bytes in a temporary data folder with the checksum check patched, exactly as ``tests_training_auto`` does.
"""

from __future__ import annotations

import json
import os
import re
import signal
from pathlib import Path
from unittest import mock

from django.core.cache import cache
from django.db import DatabaseError
from django.db import connection
from django.test import Client, TestCase, TransactionTestCase

from . import console_settings
from .models import ConsoleSetting, ConsoleSettingEvent, ModelEvent, RoleChange, TrainingRun
from .tests_roles import ROLE_CASES, client_for, make_user
from .tests_training_auto import AutoBase, BACKEND
from .training import lifecycle, runner, torchvision_frcnn as tv

URL = "/api/console/training-downloads"
ENV = tv.DOWNLOAD_ENV


def put(client: Client, payload) -> object:
    return client.put(URL, data=json.dumps(payload), content_type="application/json")


class SwitchBase(AutoBase):
    def setUp(self) -> None:
        super().setUp()
        self.admin = make_user("root", is_superuser=True, is_staff=True)
        self.root = client_for(self.admin)
        self.helper(downloads=None)  # an older/absent helper report: the console decides

    def turn_on(self, **extra):
        return put(self.root, {"allow": True, "acknowledge_terms": True, "note": "Approved by the owner.", **extra})

    def turn_off(self, **extra):
        return put(self.root, {"allow": False, **extra})

    def set_env(self, value):
        patcher = mock.patch.dict(os.environ, {} if value is None else {ENV: value})
        if value is None:
            os.environ.pop(ENV, None)
        patcher.start()
        self.addCleanup(patcher.stop)


class PermissionTests(SwitchBase):
    def test_only_a_superuser_may_change_it_whatever_other_role_they_hold(self) -> None:
        body = {"allow": True, "acknowledge_terms": True, "note": "Because."}
        for role in ROLE_CASES:
            with self.subTest(role=role):
                client = client_for(make_user(f"a-{role}", role))
                self.assertEqual(put(client, body).status_code, 403)
        self.assertFalse(ConsoleSetting.objects.exists())
        self.assertFalse(ConsoleSettingEvent.objects.exists())
        self.assertEqual(put(self.root, body).status_code, 200)

    def test_the_refusal_is_the_administrator_sentence_not_a_role_name(self) -> None:
        response = put(client_for(make_user("tess", "approver")), {"allow": True})
        self.assertEqual(response.status_code, 403)
        self.assertIn("administrator", response.json()["error"].lower())

    def test_the_refusal_says_this_setting_and_not_who_holds_which_role(self) -> None:
        for role in ROLE_CASES:
            with self.subTest(role=role):
                response = put(client_for(make_user(f"w-{role}", role)), {"allow": False})
                body = response.json()
                self.assertEqual(response.status_code, 403)
                self.assertEqual(body["error"], "Only an administrator can change this setting.")
                self.assertEqual(body["required_role"], "administrator")
                self.assertIn("your_role", body)
        self.assertEqual(Client().get(URL).status_code, 401)  # the other refusals keep their own words

    def test_reading_needs_the_trainer_role_or_more(self) -> None:
        expected = {"viewer": 403, "labeler": 403, "trainer": 200, "approver": 200}
        for role, status in expected.items():
            with self.subTest(role=role):
                self.assertEqual(client_for(make_user(f"r-{role}", role)).get(URL).status_code, status)
        self.assertEqual(self.root.get(URL).status_code, 200)

    def test_anonymous_callers_get_401_for_both_methods(self) -> None:
        anonymous = Client()
        self.assertEqual(anonymous.get(URL).status_code, 401)
        self.assertEqual(put(anonymous, {"allow": True}).status_code, 401)

    def test_only_get_and_put_exist(self) -> None:
        for method in ("post", "patch", "delete"):
            with self.subTest(method=method):
                self.assertEqual(getattr(self.root, method)(URL).status_code, 405)

    def test_the_reply_says_whether_this_person_may_change_it(self) -> None:
        trainer = client_for(make_user("tom", "trainer"))
        self.assertFalse(trainer.get(URL).json()["can_change"])
        self.assertTrue(self.root.get(URL).json()["can_change"])


class SwitchTests(SwitchBase):
    def setUp(self) -> None:
        super().setUp()
        self.set_env(None)

    def test_fresh_install_is_off_by_default_and_says_so(self) -> None:
        body = self.root.get(URL).json()
        self.assertFalse(body["allow"])
        self.assertEqual(body["source"], "default")
        self.assertIsNone(body["stored"])
        self.assertFalse(body["environment"]["set"])

    def test_turning_it_on_stores_it_records_who_and_why_and_returns_the_licence_text(self) -> None:
        response = self.turn_on()
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual((body["allow"], body["source"], body["changed"]), (True, "console", True))
        self.assertEqual(self.root.get(URL).json()["allow"], True)
        event = ConsoleSettingEvent.objects.get()
        self.assertEqual(
            (event.actor_ref, event.actor_role, event.before, event.before_source, event.after, event.note,
             event.terms_acknowledged),
            (f"u{self.admin.pk}", "administrator", "off", "default", "on", "Approved by the owner.", True),
        )
        self.assertEqual(body["event"]["by"], f"u{self.admin.pk}")
        licence = body["licence"]
        self.assertEqual(licence["backend_note"], tv.TorchvisionFasterRCNN.license_note)
        self.assertEqual(licence["general_terms"], tv.GENERAL_TERMS)
        self.assertEqual(
            {o["value"]: o["licence_plain"] for o in licence["options"]},
            {mode: tv.START_OPTION_TEXT[mode]["licence_plain"] for mode in ("imagenet_backbone", "coco")},
        )

    def test_the_record_keeps_no_name_or_address(self) -> None:
        self.turn_on()
        stored = " ".join(str(value) for row in ConsoleSettingEvent.objects.values() for value in row.values())
        self.assertNotIn("root", stored.replace("administrator", ""))
        self.assertNotIn("example.com", stored)
        self.assertFalse({"username", "email"} & {f.name for f in ConsoleSettingEvent._meta.get_fields()})

    def test_turning_it_on_needs_the_exact_boolean_acknowledgement_and_a_real_note(self) -> None:
        good = {"allow": True, "acknowledge_terms": True, "note": "Owner agreed."}
        bad = [
            {k: v for k, v in good.items() if k != "acknowledge_terms"},
            {**good, "acknowledge_terms": False},
            {**good, "acknowledge_terms": "true"},
            {**good, "acknowledge_terms": 1},
            {**good, "acknowledge_terms": None},
            {k: v for k, v in good.items() if k != "note"},
            {**good, "note": ""},
            {**good, "note": "  ab "},
            {**good, "note": 42},
            {**good, "note": "x" * 301},
        ]
        for payload in bad:
            with self.subTest(payload=payload):
                response = put(self.root, payload)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertTrue(response.json()["error"])
        self.assertFalse(ConsoleSetting.objects.exists())
        self.assertFalse(ConsoleSettingEvent.objects.exists())

    def test_allow_must_be_a_real_boolean_and_unknown_fields_are_refused(self) -> None:
        for payload in ({}, {"allow": "true"}, {"allow": 1}, {"allow": None},
                        {"allow": False, "colour": "red"}, {"allow": False, "based_on": "off"}):
            with self.subTest(payload=payload):
                self.assertEqual(put(self.root, payload).status_code, 400)

    def test_a_body_that_is_not_json_is_refused(self) -> None:
        self.assertEqual(self.root.put(URL, data="allow=1", content_type="text/plain").status_code, 415)

    def test_turning_it_off_needs_no_tick_and_no_note(self) -> None:
        self.turn_on()
        response = self.turn_off()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual((response.json()["allow"], response.json()["changed"]), (False, True))
        last = ConsoleSettingEvent.objects.order_by("-id").first()
        self.assertEqual((last.before, last.after, last.note), ("on", "off", ""))
        self.assertEqual(ConsoleSettingEvent.objects.count(), 2)

    def test_saying_the_same_thing_again_writes_nothing(self) -> None:
        self.turn_on()
        again = self.turn_on(note="Same again.")
        self.assertEqual(again.status_code, 200)
        self.assertFalse(again.json()["changed"])
        self.assertIsNone(again.json()["event"])
        self.assertEqual(ConsoleSettingEvent.objects.count(), 1)

    def test_a_stale_page_gets_a_409_and_changes_nothing(self) -> None:
        self.turn_on()
        response = put(self.root, {"allow": False, "based_on": False})  # the page thought it was off
        self.assertEqual(response.status_code, 409)
        self.assertIn("changed", response.json()["error"].lower())
        self.assertIn("nothing was changed", response.json()["error"])
        self.assertTrue(self.root.get(URL).json()["allow"])
        self.assertEqual(ConsoleSettingEvent.objects.count(), 1)
        self.assertEqual(put(self.root, {"allow": False, "based_on": True}).status_code, 200)
        self.assertFalse(self.root.get(URL).json()["allow"])

    def test_the_web_process_never_fetches_anything(self) -> None:
        with mock.patch.object(tv, "_fetch") as fetch, mock.patch("urllib.request.OpenerDirector.open") as urlopen:
            self.turn_on()
            self.root.get(URL)
            self.turn_off()
        fetch.assert_not_called()
        urlopen.assert_not_called()

    def test_the_reply_uses_plain_words(self) -> None:
        body = self.turn_on().json()
        text = body["summary"]
        self.assertIn("on", text.lower())
        self.assertIn("teaching helper", body["fetched_by"])
        self.assertIn("never downloads", body["fetched_by"])


class EventRecordTests(TestCase):
    def make(self, **extra) -> ConsoleSettingEvent:
        return ConsoleSettingEvent.objects.create(
            key=console_settings.TRAINING_DOWNLOADS, actor_ref="u1", actor_role="administrator",
            before="off", before_source="default", after="on", note="x", terms_acknowledged=True, **extra,
        )

    def test_a_record_cannot_be_edited_or_deleted(self) -> None:
        event = self.make()
        event.after = "off"
        with self.assertRaises(ValueError):
            event.save()
        with self.assertRaises(ValueError):
            event.delete()
        with self.assertRaises(ValueError):
            ConsoleSettingEvent.objects.all().delete()
        with self.assertRaises(ValueError):
            ConsoleSettingEvent.objects.all().update(after="off")
        with self.assertRaises(ValueError):
            ConsoleSettingEvent.objects.bulk_update([event], ["after"])
        self.assertEqual(ConsoleSettingEvent.objects.get().after, "on")

    def test_it_outlives_the_account_that_made_it(self) -> None:
        user = make_user("gone", is_superuser=True)
        event = self.make(actor=user)
        user.delete()
        event.refresh_from_db()
        self.assertIsNone(event.actor)
        self.assertEqual(event.actor_ref, "u1")


class PrecedenceTests(SwitchBase):
    """Stored console setting wins; the environment only supplies the default when nothing is stored."""

    # (environment, console setting) -> (allowed, source)
    MATRIX = {
        (None, None): (False, "default"),
        ("0", None): (False, "environment"),
        ("1", None): (True, "environment"),
        (None, True): (True, "console"),
        ("0", True): (True, "console"),
        ("1", True): (True, "console"),
        (None, False): (False, "console"),
        ("0", False): (False, "console"),
        ("1", False): (False, "console"),
    }

    def store(self, allow) -> None:
        if allow is not None:
            console_settings.set_training_downloads(
                allow, actor=self.admin, acknowledge_terms=allow, note="matrix" if allow else ""
            )

    def test_the_whole_matrix_in_the_helper_the_resolver_and_the_reply(self) -> None:
        for (env, stored), (allowed, source) in self.MATRIX.items():
            with self.subTest(env=env, console=stored):
                ConsoleSetting.objects.all().delete()
                self.set_env(env)
                self.store(stored)
                resolved = console_settings.training_downloads()
                self.assertEqual((resolved.allow, resolved.source), (allowed, source))
                self.assertEqual(tv.downloads_allowed(), allowed)  # what the helper acts on
                body = self.root.get(URL).json()
                self.assertEqual((body["allow"], body["source"]), (allowed, source))
                self.assertEqual(body["environment"]["set"], env is not None)

    def test_the_helper_and_the_console_name_the_same_variable(self) -> None:
        self.assertEqual(tv.DOWNLOAD_ENV, console_settings.TRAINING_DOWNLOAD_ENV)

    def test_other_environment_spellings_follow_the_old_rule(self) -> None:
        for value, expected in (("true", True), ("YES", True), (" 1 ", True), ("no", False), ("", False), ("2", False)):
            with self.subTest(value=value):
                self.set_env(value)
                self.assertEqual(tv.downloads_allowed(), expected)

    def test_the_record_remembers_what_it_replaced(self) -> None:
        self.set_env("1")
        self.turn_off()
        event = ConsoleSettingEvent.objects.get()
        self.assertEqual((event.before, event.before_source, event.after), ("on", "environment", "off"))

    def test_pinning_the_same_value_the_environment_gives_is_still_a_change(self) -> None:
        self.set_env("1")
        self.assertTrue(self.turn_on().json()["changed"])
        self.assertEqual(self.root.get(URL).json()["source"], "console")

    def test_if_the_database_cannot_be_read_the_environment_decides(self) -> None:
        self.set_env("1")
        with mock.patch.object(console_settings, "_stored", side_effect=DatabaseError("down")):
            self.assertTrue(tv.downloads_allowed())
        self.set_env("0")
        with mock.patch.object(console_settings, "_stored", side_effect=DatabaseError("down")):
            self.assertFalse(tv.downloads_allowed())


class PreviewTests(SwitchBase):
    """The Teach panel's readiness preview (web process) follows the stored switch. No weights are fetched."""

    def setUp(self) -> None:
        super().setUp()
        self.set_env("0")
        self.add_pictures(10)

    def start_from(self) -> dict:
        return self.readiness()["start_from"]

    def test_the_resolved_start_flips_when_the_switch_turns_on_and_back_when_it_turns_off(self) -> None:
        self.assertIsNone(self.start_from()["resolved"])
        with mock.patch.object(tv, "_fetch") as fetch:
            self.assertEqual(self.turn_on().status_code, 200)
            start = self.start_from()
            self.assertEqual(start["resolved"], "coco")
            self.assertEqual([o["value"] for o in start["available"]], ["coco", "imagenet_backbone"])
            self.assertFalse(any(o["ready"] for o in start["available"]))  # allowed to fetch, nothing fetched yet
            self.turn_off()
            self.assertIsNone(self.start_from()["resolved"])
        fetch.assert_not_called()

    def test_with_the_switch_on_a_run_can_be_queued_with_a_ready_made_start(self) -> None:
        dataset = self.make_dataset()
        self.turn_on()
        response = self.post_run(
            dataset_id=str(dataset.id), config={"pretrained": "auto"}, acknowledge_pretrained_terms=True
        )
        self.assertEqual(response.status_code, 202, response.content)
        self.assertEqual(TrainingRun.objects.get().config["pretrained"], "coco")

    def test_the_tick_per_run_is_still_enforced_with_the_switch_on(self) -> None:
        dataset = self.make_dataset()
        self.turn_on()
        response = self.post_run(dataset_id=str(dataset.id), config={"pretrained": "auto"})
        self.assertEqual(response.status_code, 400, response.content)
        self.assertFalse(TrainingRun.objects.exists())

    def test_cached_weights_are_ready_with_the_switch_off_and_stay_ready_with_it_on(self) -> None:
        self.cache("coco", "imagenet_backbone")
        for turn in (self.turn_off, self.turn_on):
            turn()
            start = self.start_from()
            self.assertEqual(start["resolved"], "coco")
            self.assertTrue(all(o["ready"] for o in start["available"]))

    def test_a_helper_still_reporting_off_does_not_hide_a_switch_that_was_just_turned_on(self) -> None:
        self.helper(downloads=False)  # its last report, up to ten seconds old
        self.assertIsNone(self.start_from()["resolved"])
        self.turn_on()
        self.assertEqual(self.start_from()["resolved"], "coco")

    def test_with_nothing_stored_the_helpers_own_report_still_wins_over_the_consoles_environment(self) -> None:
        self.set_env("1")
        self.helper(downloads=False)
        self.assertIsNone(self.start_from()["resolved"])
        self.helper(downloads=True)
        self.assertEqual(self.start_from()["resolved"], "coco")

    def test_the_reason_when_it_is_off_names_the_console_switch_first(self) -> None:
        start = self.start_from()
        self.assertTrue(start["how_to_enable"].startswith("An administrator can switch this on in the console"))
        self.assertIn(tv.DOWNLOAD_ENV, start["how_to_enable"])  # the environment steps stay as the alternative


class WorkerSideTests(SwitchBase):
    """The teaching helper has database access and reads the switch itself: no environment variable needed."""

    def setUp(self) -> None:
        super().setUp()
        self.set_env(None)

    def fake_fetch(self, url, destination, hash_prefix) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"junk")

    def test_the_download_step_obeys_the_stored_switch_with_the_environment_unset(self) -> None:
        with self.assertRaises(ValueError) as refused:
            tv.ensure_pretrained_weights("coco")
        self.assertIn("switched off", str(refused.exception))
        self.turn_on()
        with mock.patch.object(tv, "_fetch", side_effect=self.fake_fetch) as fetch, \
                mock.patch.object(tv, "_verify", return_value=True):
            provenance = tv.ensure_pretrained_weights("coco")
        fetch.assert_called_once()
        self.assertEqual(provenance["from"], "download")
        self.turn_off()
        tv._weights_path("coco").unlink()
        with mock.patch.object(tv, "_fetch") as fetch, mock.patch.object(tv, "_verify", return_value=True):
            with self.assertRaises(ValueError):
                tv.ensure_pretrained_weights("coco")
        fetch.assert_not_called()

    def test_the_stored_off_stops_a_download_even_if_the_helpers_environment_says_on(self) -> None:
        self.turn_off()
        self.set_env("1")
        with mock.patch.object(tv, "_fetch") as fetch:
            with self.assertRaises(ValueError):
                tv.ensure_pretrained_weights("imagenet_backbone")
        fetch.assert_not_called()

    def test_the_heartbeat_reports_what_the_helper_will_do(self) -> None:
        runner.heartbeat("ready", {BACKEND: True})
        self.assertIs(lifecycle.worker_status()["downloads_allowed"], False)
        self.turn_on()
        runner.heartbeat("ready", {BACKEND: True})
        self.assertIs(lifecycle.worker_status()["downloads_allowed"], True)
        self.turn_off()
        runner.heartbeat("ready", {BACKEND: True})
        self.assertIs(lifecycle.worker_status()["downloads_allowed"], False)

    def test_a_run_started_by_the_helper_picks_the_switch_up_at_start_time(self) -> None:
        """``fit`` calls ``ensure_pretrained_weights`` first; flipping the switch between two runs changes the outcome."""
        cache.clear()
        outcomes = []
        for flip in (self.turn_off, self.turn_on):
            flip()
            with mock.patch.object(tv, "_fetch", side_effect=self.fake_fetch), mock.patch.object(tv, "_verify", return_value=True):
                try:
                    tv.ensure_pretrained_weights("coco")
                    outcomes.append("ok")
                except ValueError:
                    outcomes.append("refused")
                tv._weights_path("coco").unlink(missing_ok=True)
        self.assertEqual(outcomes, ["refused", "ok"])


class NoteCharacterTests(SwitchBase):
    """A note is plain text: nothing that cannot be shown (NUL, BEL, ESC, bidi tricks) may be stored."""

    DIRTY = ("ab\x00c\x07d\x1b[31m", "x\x00yz", "bell\x07 ring", "\x1b[2Jclear", "ab\u202ecd", "del\x7fete", "a\u0085b\x9bc")
    CLEAN = (
        "Approved by the owner.",
        "Reviewed 👍 and agreed ✅",
        "Zoë, Müller, 田中, Ελένη, Ольга — ok",
        "family 👨\u200d👩\u200d👧 flag 🇲🇾 heart ❤\ufe0f",
        "tabs\tand\nnewlines collapse",
        "پارسی می\u200cخواهم",
    )
    MESSAGE = "The note contains characters that cannot be saved."

    def test_clean_note_refuses_control_characters(self) -> None:
        for note in self.DIRTY:
            for required in (True, False):
                with self.subTest(note=note, required=required), self.assertRaises(console_settings.SettingError) as caught:
                    console_settings.clean_note(note, required=required)
                self.assertEqual(caught.exception.message, self.MESSAGE)
                self.assertEqual(caught.exception.status, 400)

    def test_emoji_and_letters_from_any_language_are_kept_exactly(self) -> None:
        for note in self.CLEAN:
            with self.subTest(note=note):
                self.assertEqual(console_settings.clean_note(note, required=True), " ".join(note.split()))

    def test_the_api_refuses_a_dirty_note_for_on_and_for_off_and_stores_nothing(self) -> None:
        for body in (
            {"allow": True, "acknowledge_terms": True, "note": self.DIRTY[0]},
            {"allow": False, "note": self.DIRTY[0]},
        ):
            with self.subTest(allow=body["allow"]):
                response = put(self.root, body)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"], self.MESSAGE)
        self.assertFalse(ConsoleSetting.objects.exists())
        self.assertFalse(ConsoleSettingEvent.objects.exists())

    def test_the_api_stores_an_emoji_note_as_written(self) -> None:
        body = self.turn_on(note="Owner agreed 👍 — see Zoë").json()
        self.assertEqual(body["event"]["note"], "Owner agreed 👍 — see Zoë")
        self.assertEqual(ConsoleSettingEvent.objects.get().note, "Owner agreed 👍 — see Zoë")

    def test_a_control_character_never_reaches_the_event_table(self) -> None:
        with self.assertRaises(console_settings.SettingError):
            console_settings.set_training_downloads(False, actor=self.admin, note="a\x1bb")
        self.assertFalse(ConsoleSettingEvent.objects.exists())


class AppendOnlyGapTests(SwitchBase):
    """The shared append-only base: rewriting by upsert and the fast delete are refused like update/delete."""

    def make_rows(self):
        event = ConsoleSettingEvent.objects.create(
            key="k", actor_ref="u1", actor_role="administrator", before="off", before_source="default", after="on", note="x"
        )
        change = RoleChange.objects.create(scope="console", via="screen", user_ref="u2", before="", after="viewer")
        model_event = ModelEvent.objects.create(project=self.project, kind="note", note="x")
        return {ConsoleSettingEvent: event, RoleChange: change, ModelEvent: model_event}

    def test_an_upsert_cannot_rewrite_a_row(self) -> None:
        for model, row in self.make_rows().items():
            with self.subTest(model=model.__name__):
                copy = model.objects.get(pk=row.pk)
                copy.note = "rewritten"
                with self.assertRaises(ValueError):
                    model.objects.bulk_create([copy], update_conflicts=True, update_fields=["note"], unique_fields=["id"])
                self.assertEqual(model.objects.get(pk=row.pk).note, row.note)

    def test_the_fast_delete_cannot_wipe_the_table(self) -> None:
        rows = self.make_rows()
        counts = {model: model.objects.count() for model in rows}
        for model in rows:
            with self.subTest(model=model.__name__):
                with self.assertRaises(ValueError):
                    model.objects.all()._raw_delete(using="default")
                with self.assertRaises(ValueError):
                    model.objects.filter(pk=rows[model].pk)._raw_delete(using="default")
                self.assertEqual(model.objects.count(), counts[model])

    def test_adding_rows_in_bulk_still_works(self) -> None:
        rows = self.make_rows()
        for model, row in rows.items():
            with self.subTest(model=model.__name__):
                before = model.objects.count()
                fresh = model.objects.get(pk=row.pk)
                fresh.pk = None
                fresh._state.adding = True
                model.objects.bulk_create([fresh])
                self.assertEqual(model.objects.count(), before + 1)

    def test_update_delete_and_bulk_update_stay_refused_for_all_three(self) -> None:
        for model, row in self.make_rows().items():
            with self.subTest(model=model.__name__):
                before = model.objects.count()
                with self.assertRaises(ValueError):
                    model.objects.all().update(note="y")
                with self.assertRaises(ValueError):
                    model.objects.all().delete()
                with self.assertRaises(ValueError):
                    model.objects.bulk_update([row], ["note"])
                with self.assertRaises(ValueError):
                    row.delete()
                self.assertEqual(model.objects.count(), before)


class SettingReadFailureTests(TransactionTestCase):
    """Outside a transaction, as the helper runs: a failed read must not close the connection under the caller."""

    def test_a_database_error_returns_the_fallback_and_does_not_close_the_connection(self) -> None:
        with mock.patch.dict(os.environ, {ENV: "1"}), mock.patch.object(connection, "close") as close, \
                mock.patch.object(console_settings, "_stored", side_effect=DatabaseError("locked")):
            resolved = console_settings.training_downloads()
        self.assertEqual((resolved.allow, resolved.source), (True, console_settings.SOURCE_ENVIRONMENT))
        close.assert_not_called()

    def test_a_connection_that_really_died_is_still_dropped_so_the_helper_reconnects(self) -> None:
        connection.ensure_connection()
        with mock.patch.dict(os.environ, {ENV: "0"}), mock.patch.object(connection, "close") as close, \
                mock.patch.object(connection, "errors_occurred", True), \
                mock.patch.object(connection, "is_usable", return_value=False), \
                mock.patch.object(console_settings, "_stored", side_effect=DatabaseError("gone")):
            resolved = console_settings.training_downloads()
        self.assertEqual((resolved.allow, resolved.source), (False, console_settings.SOURCE_ENVIRONMENT))
        close.assert_called_once()


class OperatorTextTests(SwitchBase):
    """The words people are sent away with follow the setting that is actually in force."""

    CONSOLE_OFF = (
        "Downloads were switched off in the console (Access > Ready-made model downloads); the environment setting "
        "is ignored while a console choice exists. Switch it on there."
    )
    WINS = "once an administrator makes a choice in the console, that choice wins"

    def setUp(self) -> None:
        super().setUp()
        self.add_pictures(10)

    def start_from(self) -> dict:
        return self.readiness()["start_from"]

    def refusal(self, mode="coco") -> str:
        with self.assertRaises(ValueError) as caught:
            tv.ensure_pretrained_weights(mode)
        return str(caught.exception)

    def test_the_shared_builder_has_both_voices(self) -> None:
        self.assertEqual(tv.download_blocked_message("console"), self.CONSOLE_OFF)
        for source in ("environment", "default"):
            with self.subTest(source=source):
                text = tv.download_blocked_message(source)
                self.assertIn(tv.DOWNLOAD_ENV + "=1", text)
                self.assertIn("Ready-made model downloads", text)
                self.assertIn(self.WINS, text)
                self.assertNotIn("ignored", text)

    def test_the_refusal_after_a_console_choice_does_not_send_anyone_to_the_environment(self) -> None:
        self.set_env(None)
        self.turn_off()
        message = self.refusal()
        self.assertIn("downloading is switched off", message)
        self.assertIn(self.CONSOLE_OFF, message)
        self.assertNotIn(tv.DOWNLOAD_ENV, message)
        self.assertNotIn("web/.env", message)

    def test_a_console_off_wins_over_an_environment_that_says_on_in_the_words_too(self) -> None:
        self.turn_off()
        self.set_env("1")
        message = self.refusal("imagenet_backbone")
        self.assertIn(self.CONSOLE_OFF, message)
        self.assertNotIn(tv.DOWNLOAD_ENV, message)

    def test_the_refusal_with_no_console_choice_keeps_the_environment_steps_and_the_caveat(self) -> None:
        self.set_env(None)
        message = self.refusal()
        self.assertIn(tv.DOWNLOAD_ENV + "=1", message)
        self.assertIn(self.WINS, message)
        self.assertNotIn("is ignored while a console choice exists", message)

    def test_the_teach_panel_steps_follow_the_source(self) -> None:
        self.set_env(None)
        before = self.start_from()["how_to_enable"]
        self.assertIn(tv.DOWNLOAD_ENV, before)
        self.assertIn(self.WINS, before)
        self.turn_off()
        after = self.start_from()["how_to_enable"]
        self.assertIn(self.CONSOLE_OFF, after)
        self.assertNotIn(tv.DOWNLOAD_ENV, after)
        self.assertNotIn("web/.env", after)

    def test_the_one_click_steps_follow_the_source_and_the_docker_steps_carry_the_caveat(self) -> None:
        self.set_env(None)
        self.assertIn(self.WINS, tv.AUTO_NONE_STEPS)
        self.assertGreater(tv.AUTO_NONE_STEPS.index(self.WINS), tv.AUTO_NONE_STEPS.index("web/.env"))
        self.assertIn(self.WINS, tv.ENABLE_STEPS)
        self.turn_off()
        plan = tv.TorchvisionFasterRCNN().resolve_start({"online": True, "downloads_allowed": False})
        self.assertIsNone(plan["resolved"])
        self.assertIn(self.CONSOLE_OFF, plan["how_to_enable"])
        self.assertNotIn("web/.env", plan["how_to_enable"])
        self.assertNotIn("docker compose", plan["how_to_enable"])
        self.assertIn("models/pretrained/checkpoints/", plan["how_to_enable"])  # the no-download way stays

    def test_the_old_name_is_kept_and_the_public_one_is_the_same_function(self) -> None:
        self.assertIs(tv.downloads_for, tv._downloads_for)
        self.assertEqual(tv.downloads_for({"downloads_allowed": True}), (True, "helper"))
        self.turn_off()
        self.assertEqual(tv.downloads_for({"downloads_allowed": True}), (False, "setting"))

    def test_the_backends_route_reports_setting_when_the_console_decides(self) -> None:
        url = self.api("/training/backends")
        self.assertEqual(self.client.get(url).json()["downloads"], {"allowed": False, "reported_by": "console"})
        self.turn_on()
        self.assertEqual(self.client.get(url).json()["downloads"], {"allowed": True, "reported_by": "setting"})


class WorkerStartupTests(TestCase):
    """The heartbeat thread must never send an older state after the first 'ready'."""

    def test_the_first_ready_is_not_overwritten_by_the_thread(self) -> None:
        import threading
        import time as time_module

        from .management.commands import train_worker

        calls: list[tuple[str, str]] = []
        main_ready = threading.Event()

        def fake_heartbeat(state, availability, *args, **kwargs):
            name = threading.current_thread().name
            if name == "train-heartbeat" and not main_ready.is_set():
                main_ready.wait(2)  # the thread's write lands after the main thread's: the bad ordering
            calls.append((name, state))
            if name != "train-heartbeat" and state == "ready":
                main_ready.set()

        real_thread = threading.Thread

        class SlowStart(real_thread):
            def start(self):
                super().start()
                time_module.sleep(0.2)  # let the thread read its state before the main thread moves on

        saved = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
        for sig, handler in saved.items():
            self.addCleanup(signal.signal, sig, handler)
        with mock.patch.object(train_worker, "heartbeat", fake_heartbeat), \
                mock.patch.object(train_worker, "backend_classes", return_value={}), \
                mock.patch.object(train_worker, "fail_orphaned_runs", return_value=0), \
                mock.patch.object(train_worker, "fail_stale_runs"), \
                mock.patch.object(train_worker, "claim_next_run", return_value=None), \
                mock.patch.object(train_worker.threading, "Thread", SlowStart):
            from django.core.management import call_command

            call_command("train_worker", "--once", stdout=mock.MagicMock())
            time_module.sleep(0.3)
        states = [state for _, state in calls]
        self.assertTrue(states, "no heartbeat was sent")
        self.assertEqual(set(states), {"ready"}, calls)
        self.assertEqual(calls[-1][1], "ready")


class NoteLengthTests(SwitchBase):
    """The Access page's note box and the server accept the same number of characters (hygiene H6)."""

    ACCESS_HTML = Path(__file__).resolve().parent / "templates" / "projects" / "access.html"

    def test_the_one_constant_is_the_models_own_limit(self) -> None:
        self.assertEqual(console_settings.NOTE_MAX_CHARS, ConsoleSettingEvent._meta.get_field("note").max_length)
        self.assertEqual(console_settings.NOTE_MAX_CHARS, 300)

    def test_the_note_box_on_the_access_page_allows_exactly_that_many(self) -> None:
        html = self.ACCESS_HTML.read_text()
        box = re.search(r'<textarea\b[^>]*\bid="downloads-note"[^>]*>', html)
        self.assertIsNotNone(box, "the note box is gone from access.html")
        limit = re.search(r'\bmaxlength="(\d+)"', box.group(0))
        self.assertIsNotNone(limit)
        self.assertEqual(int(limit.group(1)), console_settings.NOTE_MAX_CHARS)

    def test_the_validator_uses_the_constant_not_a_number_of_its_own(self) -> None:
        limit = console_settings.NOTE_MAX_CHARS
        self.assertEqual(console_settings.clean_note("a" * limit, required=True), "a" * limit)
        with self.assertRaises(console_settings.SettingError) as caught:
            console_settings.clean_note("a" * (limit + 1), required=True)
        self.assertEqual(caught.exception.message, f"The note is too long ({limit} characters at most).")
        with mock.patch.object(console_settings, "NOTE_MAX_CHARS", 10):
            with self.assertRaises(console_settings.SettingError):
                console_settings.clean_note("a" * 11, required=False)

    def test_what_the_box_allows_the_server_stores(self) -> None:
        note = "x" * console_settings.NOTE_MAX_CHARS
        body = self.turn_on(note=note).json()
        self.assertEqual(body["event"]["note"], note)
        self.assertEqual(ConsoleSettingEvent.objects.get().note, note)
        response = put(self.root, {"allow": False, "note": note + "y"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "The note is too long (300 characters at most).")


class PrecedenceTextTests(SwitchBase):
    """The reply says plainly that a console choice cannot be handed back to the environment (hygiene H8)."""

    SENTENCE = (
        "Once someone has chosen here, this page can only switch downloads on or off; it cannot hand the decision "
        "back to the environment setting."
    )

    def test_the_precedence_text_says_there_is_no_way_back(self) -> None:
        body = self.root.get(URL).json()
        self.assertIn(self.SENTENCE, body["precedence"])
        self.assertEqual(body["precedence"].count("no way back") + body["precedence"].count("cannot hand"), 1)
        self.assertTrue(body["precedence"].startswith("A choice made here wins over the setting"))  # nothing else moved

    def test_the_same_text_comes_with_a_change_and_for_a_trainer(self) -> None:
        self.assertIn(self.SENTENCE, self.turn_on().json()["precedence"])
        trainer = client_for(make_user("tilly", "trainer"))
        self.assertIn(self.SENTENCE, trainer.get(URL).json()["precedence"])

    def test_it_is_still_true_there_is_no_reset_action(self) -> None:
        self.turn_on()
        self.assertEqual(put(self.root, {"allow": None}).status_code, 400)
        self.assertEqual(self.root.delete(URL).status_code, 405)
        self.assertEqual(self.root.get(URL).json()["source"], "console")

