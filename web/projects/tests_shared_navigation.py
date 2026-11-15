from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import InferenceApp, Inspection, ModelVersion, Project


class SharedNavigationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('navigation-admin', password='test-only')
        self.client.force_login(self.user)
        self.project = Project.objects.create(name='Sample', modality='image', task='check', labels=['item'])
        self.model = ModelVersion.objects.create(project=self.project, version=1, backend='test-only', artifact_path='sample', artifact_sha256='a' * 64)
        self.app = InferenceApp.objects.create(project=self.project, name='Sample check', slug='sample-check', model_version=self.model)
        self.inspection = Inspection.objects.create(created_by=self.user, template='corrosion', title='Sample', status='failed')

    def test_authenticated_pages_use_the_same_sidebar_and_active_navigation(self):
        pages = [
            ('home', reverse('home'), 'nav-home-link'),
            ('inspections', reverse('inspections-page'), 'nav-inspections-link'),
            ('report', reverse('inspection-report', args=[self.inspection.id]), 'nav-inspections-link'),
            ('settings', reverse('ai-settings'), 'nav-ai-settings-link'),
            ('detectors', reverse('dashboard'), 'nav-detectors-link'),
            ('access', reverse('access-page'), 'nav-access-link'),
            ('builder', reverse('checkscreen-setup', args=[self.project.id]), 'nav-detectors-link'),
            ('operator', reverse('checkscreen-operator', args=[self.app.slug]), 'nav-detectors-link'),
        ]
        for name, url, active in pages:
            with self.subTest(page=name):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'data-testid="app-sidebar"')
                self.assertRegex(response.content.decode(), rf'data-testid="{active}"\s+aria-current="page"')
                self.assertContains(response, 'data-testid="sidebar-menu-toggle"')

    def test_builder_returns_to_the_actual_project_route(self):
        response = self.client.get(reverse('checkscreen-setup', args=[self.project.id]))
        self.assertContains(response, f'/detectors#project={self.project.id}')

    def test_authenticated_pages_use_unified_saas_shell_and_pagehead(self):
        for name in ('home', 'inspections-page', 'ai-settings', 'dashboard', 'access-page'):
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'class="a-shell"')
                self.assertContains(response, 'class="a-main"')
                self.assertContains(response, 'class="a-wrap"')
                self.assertContains(response, 'class="a-pagehead"')
