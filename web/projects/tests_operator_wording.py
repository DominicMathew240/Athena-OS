"""Plain-language guidance for operators when the picture-reading service is absent."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings


@override_settings(VISION={"BASE_URL": "", "MODEL": "qwen2.5vl:7b", "LABEL": "", "API_KEY": "", "MAX_TOKENS": 1000, "TIMEOUT_SECONDS": 30})
class OperatorWordingTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("operator", "operator@example.invalid", "unused"))

    def test_inspection_explains_the_missing_service_without_server_jargon(self):
        response = self.client.get("/inspect/new")
        self.assertContains(response, "Ask the person who manages Athena to connect it")
        self.assertNotContains(response, "Ollama or vLLM")

    def test_settings_do_not_show_default_model_identifiers_without_a_connection(self):
        response = self.client.get("/settings/ai")
        self.assertContains(response, "Ask the person who manages Athena")
        self.assertNotContains(response, "ATHENA_VLM_BASE_URL")
        self.assertNotContains(response, "qwen2.5vl:7b")

    def test_public_landing_explains_the_withdrawn_esbuild_advisory(self):
        self.client.logout()
        response = self.client.get("/")
        self.assertContains(response, "GHSA-gv7w-rqvm-qjhr")
        self.assertContains(response, "withdrawn")
        self.assertContains(response, "https://github.com/advisories/GHSA-gv7w-rqvm-qjhr")
        self.assertContains(response, "Deno")

    def test_access_explains_the_record_without_example_ids(self):
        response = self.client.get("/access")
        self.assertContains(response, "Names shown here are looked up today")
        self.assertNotContains(response, "<code>u3</code>")
