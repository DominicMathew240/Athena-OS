"""HTML pages for the inference apps: the builder ("Set up your check screen") and the operator screen.

These views only render a page shell. Everything on the page is read and written through the JSON API in
``inference_views.py``; nothing here records a decision, runs a model or changes an app.
"""

from __future__ import annotations

import uuid

from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET

from .api_common import image_project
from .inference import schema
from .models import InferenceApp
from .permissions import access


def _page(request: HttpRequest, template: str, context: dict) -> HttpResponse:
    response = render(request, template, context)
    response["Cache-Control"] = "no-store"
    return response


@access()
@require_GET
@ensure_csrf_cookie
def checkscreen_setup(request: HttpRequest, project_id) -> HttpResponse:
    """Builder page. Only image projects can have check screens (the same rule the API applies)."""
    project = image_project(project_id)
    try:
        wanted = uuid.UUID(request.GET.get("app", ""))
    except ValueError:  # missing or malformed: just open with no screen preselected
        wanted = None
    app = InferenceApp.objects.filter(project=project, id=wanted).first() if wanted else None
    return _page(
        request,
        "projects/checkscreen_builder.html",
        {"project": project, "initial_app_id": str(app.id) if app else ""},
    )


@access()
@require_GET
@ensure_csrf_cookie
def checkscreen_operator(request: HttpRequest, slug: str) -> HttpResponse:
    """Operator page, addressed by the app's short web name. Resolves slug -> (project, app) only."""
    app = get_object_or_404(InferenceApp.objects.select_related("project"), slug=slug)
    if app.project.modality != "image":
        raise Http404
    return _page(
        request,
        "projects/checkscreen.html",
        {"app": app, "project": app.project, "disclaimer": schema.DISCLAIMER},
    )
