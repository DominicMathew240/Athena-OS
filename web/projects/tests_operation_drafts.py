"""Private, resumable operation drafts are not published operations."""
import json

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from .tests import grant


class OperationDraftTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('builder')
        self.client.force_login(self.user)
        self.payload = {'name': 'Shop door', 'line': {'start': [0.1, 0.5], 'end': [0.9, 0.5], 'positive_label': 'Entering', 'negative_label': 'Leaving'}}

    def test_create_resume_update_and_discard(self):
        response = self.client.post('/api/operation-drafts', json.dumps(self.payload), content_type='application/json')
        self.assertEqual(response.status_code, 201, response.content)
        draft = response.json()['draft']
        url = '/api/operation-drafts/' + draft['id']
        self.assertEqual(self.client.get(url).json()['draft']['line'], self.payload['line'])
        updated = {**self.payload, 'name': 'Main door', 'revision': draft['revision']}
        response = self.client.put(url, json.dumps(updated), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get(url).json()['draft']['name'], 'Main door')
        self.assertEqual(self.client.put(url, json.dumps(updated), content_type='application/json').status_code, 409)
        self.assertEqual(self.client.delete(url).status_code, 204)
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_saved_versions_preserve_prior_draft_state_and_are_owner_scoped(self):
        from .models import OperationDraftVersion

        response = self.client.post('/api/operation-drafts', json.dumps(self.payload), content_type='application/json')
        draft = response.json()['draft']
        url = f"/api/operation-drafts/{draft['id']}"
        versions_url = f"{url}/versions"
        self.assertEqual(self.client.get(versions_url).status_code, 200)
        first = self.client.get(versions_url).json()['versions'][0]
        self.assertEqual(first['revision'], 1)
        self.assertEqual(first['snapshot']['draft']['name'], 'Shop door')
        self.assertFalse(first['snapshot']['release_ready'])
        self.assertEqual(set(first['snapshot']['component_pins'].values()), {None})

        changed = {**self.payload, 'name': 'Main door', 'revision': draft['revision']}
        response = self.client.put(url, json.dumps(changed), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.put(url, json.dumps(changed), content_type='application/json').status_code, 409)
        versions = self.client.get(versions_url).json()['versions']
        self.assertEqual([item['revision'] for item in versions], [2, 1])
        self.assertEqual(versions[1]['snapshot']['draft']['name'], 'Shop door')
        self.assertEqual(versions[0]['snapshot']['draft']['name'], 'Main door')
        self.assertEqual(len(versions[0]['sha256']), 64)
        with self.assertRaises(ValueError):
            OperationDraftVersion.objects.filter(pk=versions[0]['id']).update(snapshot={'tampered': True})
        saved_version = OperationDraftVersion.objects.get(pk=versions[0]['id'])
        saved_version.snapshot = {'tampered': True}
        with self.assertRaises(ValueError):
            saved_version.save()
        self.assertEqual(OperationDraftVersion.objects.get(pk=versions[0]['id']).snapshot['draft']['name'], 'Main door')
        stranger = Client()
        stranger.force_login(get_user_model().objects.create_user('version-stranger'))
        self.assertEqual(stranger.get(versions_url).status_code, 404)
        self.assertEqual(self.client.delete(url).status_code, 204)
        self.assertFalse(OperationDraftVersion.objects.filter(draft_id=draft['id']).exists())

    def test_recording_changes_create_new_immutable_draft_versions(self):
        from unittest.mock import patch

        from django.core.files.uploadedfile import SimpleUploadedFile

        from .models import OperationDraftVersion

        response = self.client.post('/api/operation-drafts', json.dumps(self.payload), content_type='application/json')
        draft = response.json()['draft']
        url = f"/api/operation-drafts/{draft['id']}"
        recording_url = f"{url}/recording"
        metadata = {"sha256": "c" * 64, "size_bytes": 100, "width": 640, "height": 480, "duration_seconds": 12.5}
        with patch("projects.recording_views.recordings.inspect_upload", return_value=metadata):
            uploaded = self.client.post(recording_url, {"revision": "1", "video": SimpleUploadedFile("clip.mp4", b"synthetic")})
        self.assertEqual(uploaded.status_code, 201, uploaded.content)
        versions_url = f"{url}/versions"
        versions = self.client.get(versions_url).json()['versions']
        self.assertEqual([item['revision'] for item in versions], [2, 1])
        self.assertEqual(versions[0]['snapshot']['recording']['sha256'], metadata['sha256'])

        removed = self.client.delete(recording_url, json.dumps({"revision": 2}), content_type="application/json")
        self.assertEqual(removed.status_code, 200, removed.content)
        versions = self.client.get(versions_url).json()['versions']
        self.assertEqual([item['revision'] for item in versions], [3, 2, 1])
        self.assertIsNone(versions[0]['snapshot']['recording'])
        self.assertEqual(OperationDraftVersion.objects.filter(draft_id=draft['id']).count(), 3)

    def test_isolation_roles_and_invalid_geometry(self):
        response = self.client.post('/api/operation-drafts', json.dumps(self.payload), content_type='application/json')
        url = '/api/operation-drafts/' + response.json()['draft']['id']
        other = Client()
        other.force_login(get_user_model().objects.create_user('other'))
        for method in ('get', 'delete'):
            self.assertEqual(getattr(other, method)(url).status_code, 404)
        viewer = get_user_model().objects.create_user('viewer')
        grant(viewer, 'viewer')
        other.force_login(viewer)
        self.assertEqual(other.post('/api/operation-drafts', json.dumps(self.payload), content_type='application/json').status_code, 403)
        invalid = {**self.payload, 'line': {**self.payload['line'], 'end': self.payload['line']['start']}}
        self.assertEqual(self.client.post('/api/operation-drafts', json.dumps(invalid), content_type='application/json').status_code, 400)

    def test_invalid_payloads_are_rejected_without_changing_the_draft(self):
        response = self.client.post('/api/operation-drafts', json.dumps(self.payload), content_type='application/json')
        draft = response.json()['draft']
        url = '/api/operation-drafts/' + draft['id']
        invalid_values = [float('nan'), float('inf'), True, -0.1, 1.1, '0.1']
        for value in invalid_values:
            with self.subTest(value=value):
                line = {**self.payload['line'], 'start': [value, 0.5]}
                payload = {**self.payload, 'line': line, 'revision': draft['revision']}
                self.assertEqual(self.client.put(url, json.dumps(payload), content_type='application/json').status_code, 400)
        self.assertEqual(self.client.get(url).json()['draft']['revision'], draft['revision'])
        self.assertEqual(self.client.get(url).json()['draft']['line'], self.payload['line'])
        self.assertEqual(self.client.post('/api/operation-drafts', json.dumps({**self.payload, 'source_url': 'http://localhost'}), content_type='application/json').status_code, 400)

    def test_draft_setup_is_reachable_and_makes_its_limit_clear(self):
        self.assertContains(self.client.get('/'), 'href="/operations"')
        page = self.client.get('/operations')
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'People counting drafts')
        self.assertContains(page, 'Recorded-video replay is not available yet')
        self.assertContains(page, 'operation-drafts.js')
        self.assertContains(page, 'id="draft-form"')
        self.assertContains(page, 'id="line-canvas"')
        self.assertContains(page, 'id="reference-events"')
        self.assertContains(page, 'id="mark-positive-event"')
        self.assertContains(page, 'id="mark-negative-event"')
        self.assertContains(page, 'operation-line.js')

    def test_reference_crossings_save_and_resume_with_the_recording_draft(self):
        from .models import OperationRecording

        response = self.client.post('/api/operation-drafts', json.dumps(self.payload), content_type='application/json')
        draft = response.json()['draft']
        OperationRecording.objects.create(
            draft_id=draft['id'], storage_key='operation-recordings/clip.mp4', filename='clip.mp4',
            sha256='a' * 64, size_bytes=100, width=640, height=480, duration_seconds=12.5,
        )
        url = '/api/operation-drafts/' + draft['id']
        events = [
            {'time': 1.25, 'direction': 'negative_to_positive'},
            {'time': 4.5, 'direction': 'positive_to_negative'},
        ]
        response = self.client.put(url, json.dumps({**self.payload, 'revision': draft['revision'], 'reference_events': events}), content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['draft']['reference_events'], events)
        self.assertEqual(self.client.get(url).json()['draft']['reference_events'], events)

    def test_changing_line_clears_reference_events_from_the_previous_geometry(self):
        from .models import OperationRecording

        response = self.client.post('/api/operation-drafts', json.dumps(self.payload), content_type='application/json')
        draft = response.json()['draft']
        OperationRecording.objects.create(
            draft_id=draft['id'], storage_key='operation-recordings/clip.mp4', filename='clip.mp4',
            sha256='a' * 64, size_bytes=100, width=640, height=480, duration_seconds=12.5,
        )
        url = '/api/operation-drafts/' + draft['id']
        events = [{'time': 1.25, 'direction': 'negative_to_positive'}]
        saved = self.client.put(url, json.dumps({**self.payload, 'revision': draft['revision'], 'reference_events': events}), content_type='application/json')
        self.assertEqual(saved.status_code, 200)
        changed_line = {**self.payload['line'], 'start': [0.2, 0.2]}
        changed = self.client.put(url, json.dumps({**self.payload, 'line': changed_line, 'reference_events': events, 'revision': saved.json()['draft']['revision']}), content_type='application/json')
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(changed.json()['draft']['reference_events'], [])

    def test_reference_crossings_require_a_line_and_valid_in_range_observations(self):
        from .models import OperationRecording

        response = self.client.post('/api/operation-drafts', json.dumps(self.payload), content_type='application/json')
        draft = response.json()['draft']
        OperationRecording.objects.create(
            draft_id=draft['id'], storage_key='operation-recordings/clip.mp4', filename='clip.mp4',
            sha256='a' * 64, size_bytes=100, width=640, height=480, duration_seconds=12.5,
        )
        url = '/api/operation-drafts/' + draft['id']
        invalid_events = [
            [{'time': -0.1, 'direction': 'negative_to_positive'}],
            [{'time': 12.6, 'direction': 'negative_to_positive'}],
            [{'time': True, 'direction': 'negative_to_positive'}],
            [{'time': float('nan'), 'direction': 'negative_to_positive'}],
            [{'time': 10 ** 1000, 'direction': 'negative_to_positive'}],
            [{'time': 1, 'direction': 'inside_to_outside'}],
            [{'time': 1, 'direction': 'negative_to_positive', 'label': 'forged'}],
        ]
        for events in invalid_events:
            with self.subTest(events=events):
                payload = {**self.payload, 'revision': draft['revision'], 'reference_events': events}
                self.assertEqual(self.client.put(url, json.dumps(payload), content_type='application/json').status_code, 400)
        without_line = {**self.payload, 'line': None, 'reference_events': [{'time': 1, 'direction': 'negative_to_positive'}], 'revision': draft['revision']}
        self.assertEqual(self.client.put(url, json.dumps(without_line), content_type='application/json').status_code, 400)
        self.assertEqual(self.client.get(url).json()['draft']['revision'], draft['revision'])
        self.assertEqual(self.client.get(url).json()['draft']['reference_events'], [])

    def test_csrf_required(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post('/api/operation-drafts', json.dumps(self.payload), content_type='application/json').status_code, 403)
