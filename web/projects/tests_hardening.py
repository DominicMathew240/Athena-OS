"""Cross-boundary checks that walk the URL configuration instead of listing routes by hand.

* Anonymous callers get a redirect or a 401/403 from every route that is not deliberately public.
* An id that belongs to one project never resolves through another project's URL (404).

New routes are picked up automatically; a new URL parameter name the fixtures cannot supply makes
the cross-project test fail loudly so it cannot be skipped by accident.
"""

from __future__ import annotations

import uuid

from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import URLPattern, URLResolver, get_resolver

from .middleware import PUBLIC_PATHS
from .models import (
    AutoLabelJob,
    DatasetVersion,
    InferenceApp,
    ModelVersion,
    ObservationRecord,
    Project,
    TrainingRun,
)
from .tests_inference import InferenceBase

METHODS = ("get", "post", "put", "patch", "delete")
SAMPLE = {"uuid": lambda: str(uuid.uuid4()), "int": lambda: "1", "str": lambda: "x", "slug": lambda: "x"}


def walk(patterns=None, prefix="", params=None):
    """Yield (route_string, {param_name: converter_name}) for every leaf pattern."""
    for pattern in patterns if patterns is not None else get_resolver().url_patterns:
        if isinstance(pattern, URLResolver):
            yield from walk(pattern.url_patterns, prefix + str(pattern.pattern), params)
        elif isinstance(pattern, URLPattern):
            converters = {name: type(conv).__name__.replace("Converter", "").lower()
                          for name, conv in pattern.pattern.converters.items()}
            yield prefix + str(pattern.pattern), converters


def render(route: str, values: dict[str, str]) -> str:
    import re

    return "/" + re.sub(r"<(?:\w+:)?(\w+)>", lambda match: values[match.group(1)], route).lstrip("^").rstrip("$")


def is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path.rstrip("/") + "/" in PUBLIC_PATHS or path.rstrip("/") in PUBLIC_PATHS


class AnonymousAccessSweep(InferenceBase):
    def test_every_route_is_closed_to_anonymous_callers(self) -> None:
        anonymous = Client()
        checked = 0
        for route, converters in walk():
            values = {name: SAMPLE.get(kind, SAMPLE["str"])() for name, kind in converters.items()}
            path = render(route, values)
            if is_public(path):
                continue
            for method in METHODS:
                with self.subTest(route=route, method=method):
                    response = getattr(anonymous, method)(path)
                    self.assertIn(response.status_code, (302, 401, 403), f"{method.upper()} {path} -> {response.status_code}")
                    if response.status_code == 302:
                        self.assertIn("/login/", response["Location"])
                    checked += 1
        self.assertGreater(checked, 100)  # the walk really found the API surface

    def test_public_paths_are_exactly_the_documented_ones(self) -> None:
        public = sorted(route for route, converters in walk() if not converters and is_public("/" + route))
        self.assertEqual(public, ["healthz", "login/"])

    def test_walk_covers_the_routes_added_for_this_milestone(self) -> None:
        routes = {route for route, _ in walk()}
        for fragment in ("datasets/<uuid:dataset_id>/exports", "training/runs", "inference/apps", "autolabel"):
            self.assertTrue(any(fragment in route for route in routes), fragment)


class CrossProjectIsolation(InferenceBase):
    """Every id is created in project A, then requested through project B's URL."""

    def setUp(self) -> None:
        super().setUp()
        app = self.make_app()
        self.assertEqual(self.run_image(app["id"], process=False).status_code, 202)
        self.other = Project.objects.create(name="Other line", modality="image", task="Detect", labels=["damaged"])
        asset = self.project.assets.first()
        annotation = asset.annotations.first()
        self.ids = {
            "asset_id": asset.id,
            "annotation_id": annotation.id,
            "dataset_id": DatasetVersion.objects.filter(project=self.project).first().id,
            "run_id": TrainingRun.objects.filter(project=self.project).first().id,
            "model_id": ModelVersion.objects.filter(project=self.project).first().id,
            "app_id": InferenceApp.objects.filter(project=self.project).first().id,
            "observation_id": ObservationRecord.objects.filter(app__project=self.project).first().id,
            "job_id": AutoLabelJob.objects.create(project=self.project).id,
        }

    def test_every_nested_route_404s_through_another_project(self) -> None:
        nested = [(route, c) for route, c in walk() if "project_id" in c and len(c) > 1]
        self.assertGreaterEqual(len(nested), 20)
        for route, converters in nested:
            unknown = set(converters) - {"project_id"} - set(self.ids)
            self.assertFalse(unknown, f"{route}: add fixtures for {unknown} to CrossProjectIsolation")
            values = {name: str(self.ids.get(name, self.other.id)) for name in converters}
            values["project_id"] = str(self.other.id)
            path = render(route, values)
            statuses = {}
            for method in METHODS:
                kwargs = {"data": "{}", "content_type": "application/json"} if method in ("post", "put", "patch") else {}
                statuses[method] = getattr(self.client, method)(path, **kwargs).status_code
            with self.subTest(route=route):
                self.assertIn(404, statuses.values(), f"{path} never returned 404: {statuses}")
                # nothing may succeed or be accepted through the wrong project
                self.assertFalse([m for m, code in statuses.items() if code < 400], f"{path}: {statuses}")
        self.assertTrue(Project.objects.filter(pk=self.project.pk).exists())

    def test_unknown_ids_404_through_the_right_project(self) -> None:
        for route, converters in walk():
            if "project_id" not in converters or len(converters) < 2:
                continue
            values = {name: str(uuid.uuid4()) for name in converters}
            values["project_id"] = str(self.project.id)
            path = render(route, values)
            with self.subTest(route=route):
                statuses = {
                    m: getattr(self.client, m)(
                        path, **({"data": "{}", "content_type": "application/json"} if m in ("post", "put", "patch") else {})
                    ).status_code
                    for m in METHODS
                }
                self.assertIn(404, statuses.values(), statuses)
                self.assertFalse([m for m, code in statuses.items() if code < 400], statuses)

    def test_a_signed_in_user_cannot_use_project_a_ids_in_a_body_for_project_b(self) -> None:
        response = self.post(
            self.api("/training/runs", self.other), {"dataset_version_id": str(self.ids["dataset_id"]), "backend": "fake-backend"}
        )
        self.assertIn(response.status_code, (400, 404))
        self.assertEqual(TrainingRun.objects.filter(project=self.other).count(), 0)
        response = self.post(
            self.api("/inference/apps", self.other), {"name": "x", "model_version_id": str(self.ids["model_id"])}
        )
        self.assertIn(response.status_code, (400, 404))
        self.assertEqual(InferenceApp.objects.filter(project=self.other).count(), 0)


class UnknownProjectSweep(InferenceBase):
    def test_every_project_scoped_route_404s_for_an_unknown_project(self) -> None:
        scoped = [(route, c) for route, c in walk() if "project_id" in c]
        self.assertGreaterEqual(len(scoped), 30)
        for route, converters in scoped:
            values = {name: str(uuid.uuid4()) for name in converters}
            path = render(route, values)
            statuses = {
                m: getattr(self.client, m)(
                    path, **({"data": "{}", "content_type": "application/json"} if m in ("post", "put", "patch") else {})
                ).status_code
                for m in METHODS
            }
            with self.subTest(route=route):
                self.assertIn(404, statuses.values(), statuses)
                self.assertFalse([m for m, code in statuses.items() if code < 400], statuses)

    def test_non_image_projects_are_invisible_to_the_image_apis(self) -> None:
        audio = Project.objects.create(name="Sound", modality="audio", task="Classify", labels=["a"])
        for route, converters in walk():
            if "project_id" not in converters or route.startswith("api/projects/<uuid:project_id>") is False:
                continue
            if route == "api/projects/<uuid:project_id>":  # generic project detail/delete is modality-agnostic
                continue
            values = {name: str(uuid.uuid4()) for name in converters}
            values["project_id"] = str(audio.id)
            path = render(route, values)
            with self.subTest(route=route):
                for method in ("get", "post", "delete"):
                    kwargs = {"data": "{}", "content_type": "application/json"} if method == "post" else {}
                    self.assertGreaterEqual(getattr(self.client, method)(path, **kwargs).status_code, 400)
