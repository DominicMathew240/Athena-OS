"""The shared JSON-body helper: every odd body gets the plain JSON answer, never an HTML page or a 500."""

from __future__ import annotations

from .tests_training import TrainingBase

OVER_ONE_MIB = 1_048_576 + 10
HUGE_INTEGER = "9" * 6000  # more digits than Python's int-from-text limit (4300) allows


class JsonBodyEdgeTests(TrainingBase):
    """Run the odd bodies through two real endpoints: one that allows an empty body, one that does not."""

    def endpoints(self):
        return {
            "training runs": self.api("/training/runs"),
            "feedback import": self.api("/feedback/corrections/import"),
        }

    def send(self, url: str, body: str):
        return self.client.post(url, data=body, content_type="application/json")

    def assertPlainJson(self, response, status: int, text: str) -> None:
        self.assertEqual(response.status_code, status, response.content[:200])
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(response.json()["error"], text)
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_a_body_over_the_hard_limit_is_a_json_413(self) -> None:
        body = '{"note": "' + "x" * OVER_ONE_MIB + '"}'
        for name, url in self.endpoints().items():
            with self.subTest(endpoint=name), self.assertNoLogs("django.request", level="ERROR"):
                self.assertPlainJson(self.send(url, body), 413, "The request is too large.")

    def test_a_body_over_the_helper_limit_is_a_json_413(self) -> None:
        body = '{"note": "' + "x" * 40_000 + '"}'
        for name, url in self.endpoints().items():
            with self.subTest(endpoint=name):
                self.assertPlainJson(self.send(url, body), 413, "The request is too large.")

    def test_a_number_with_thousands_of_digits_is_a_plain_400(self) -> None:
        for name, url in self.endpoints().items():
            with self.subTest(endpoint=name, shape="value"):
                response = self.send(url, '{"config": {"epochs": ' + HUGE_INTEGER + "}}")
                self.assertPlainJson(response, 400, "The request body is not valid JSON.")
            with self.subTest(endpoint=name, shape="bare number"):
                self.assertPlainJson(self.send(url, HUGE_INTEGER), 400, "The request body is not valid JSON.")

    def test_a_deeply_nested_body_is_a_plain_400(self) -> None:
        for name, url in self.endpoints().items():
            with self.subTest(endpoint=name):
                self.assertPlainJson(self.send(url, "[" * 30_000), 400, "The request body is not valid JSON.")
