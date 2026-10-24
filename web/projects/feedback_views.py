"""JSON API for pulling operator feedback into a project as reviewable pictures (see ``feedback_import``)."""

from __future__ import annotations

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods

from . import feedback_import
from .api_common import image_project, json_errors, json_payload, no_store, reject_unknown
from .permissions import access


@access()
@require_http_methods(["GET"])
@json_errors
def feedback_corrections(request: HttpRequest, project_id) -> JsonResponse:
    """What operators said on the check screens and whether it has been pulled into the project yet."""
    return no_store(JsonResponse(feedback_import.corrections(image_project(project_id))))


@access(POST="label")
@require_http_methods(["POST"])
@json_errors
def feedback_corrections_import(request: HttpRequest, project_id) -> JsonResponse:
    """Copy pictures with operator feedback into the project as Needs-review material (Labeler and above)."""
    project = image_project(project_id)
    payload = json_payload(request, allow_empty=True)
    reject_unknown(payload, {"observation_ids"})
    named = payload.get("observation_ids")
    ids = None if named is None else feedback_import.parse_observation_ids(named)
    return no_store(JsonResponse(feedback_import.import_corrections(project, request.user, ids)))
