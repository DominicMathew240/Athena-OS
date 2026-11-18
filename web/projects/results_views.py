"""Read-only plain-language results for one model: found X of Y per label, a verdict, the worst pictures.

Everything is derived from the evaluation stored on the model (see ``training.report.plain_results``); this
module only looks the model up inside its own project and checks which listed pictures still exist there.
"""

from __future__ import annotations

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods

from .api_common import image_project, no_store
from .models import Asset
from .permissions import access
from .training import report

# The project-scoped model lookup (a model of another project is a plain 404) is written once, in training_views;
# training_views does not import this module, so there is no cycle.
from .training_views import _get_model


@access()
@require_http_methods(["GET"])
def model_results_plain(request: HttpRequest, project_id, model_id) -> JsonResponse:
    project = image_project(project_id)
    model = _get_model(project, model_id)
    if _is_non_vision_evaluation(model):
        # A model whose evaluation is not the box-detection report: the found/missed wording
        # below is about boxes, so say plainly where its results live instead of guessing.
        return no_store(
            JsonResponse(
                {
                    "model_id": str(model.id),
                    "evaluated": True,
                    "reason_if_not": "Results for this model type are shown in its evaluation report.",
                    "counts": {"test_pictures": None, "test_boxes": None},
                    "labels": [],
                    "overall": {
                        "found": None,
                        "of": None,
                        "wrong_boxes": None,
                        "verdict": "cannot_tell",
                        "headline": "Results for this model type are shown in its evaluation report.",
                        "reasons": [],
                    },
                    "non_vision": True,
                }
            )
        )
    listed = report.stored_outcome_asset_ids(model.evaluation)
    known = (
        {str(pk) for pk in Asset.objects.filter(project=project, id__in=listed).values_list("id", flat=True)}
        if listed
        else None
    )
    result = report.plain_results(model.evaluation, known_asset_ids=known)
    return no_store(JsonResponse({"model_id": str(model.id), **result}))


def _is_non_vision_evaluation(model) -> bool:
    """True when the model's task is a non-vision recipe, or (older rows) its evaluation claims
    to be available but does not carry the box-detection shape the plain results read."""
    if model.task and model.task != "vision-detection":
        return True
    evaluation = model.evaluation if isinstance(model.evaluation, dict) else {}
    if not evaluation.get("available"):
        return False
    return not isinstance(evaluation.get("per_label"), dict)
