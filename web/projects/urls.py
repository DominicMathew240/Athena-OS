from django.urls import path

from . import access_views, autolabel_views, dataset_views, inference_pages, inference_views, training_views, views
from . import feedback_views, results_views, settings_views, vision_pages, vision_views

urlpatterns = [
    path("", vision_pages.landing_or_home, name="home"),
    path("detectors", views.dashboard, name="dashboard"),
    path("inspect/new", vision_pages.inspect_new, name="inspect-new"),
    path("inspections", vision_pages.inspections_page, name="inspections-page"),
    path("inspections/<uuid:inspection_id>", vision_pages.inspection_report, name="inspection-report"),
    path("settings/ai", vision_pages.ai_settings_page, name="ai-settings"),
    path("api/vision/templates", vision_views.vision_templates, name="vision-templates"),
    path("api/vision/connection", vision_views.vision_connection, name="vision-connection"),
    path("api/vision/connection/test", vision_views.vision_connection_test, name="vision-connection-test"),
    path("api/vision/status", vision_views.vision_status, name="vision-status"),
    path("api/inspections", vision_views.inspection_collection, name="inspection-list"),
    path("api/inspections/summary", vision_views.inspection_summary, name="inspection-summary"),
    path("api/inspections/<uuid:inspection_id>", vision_views.inspection_detail, name="inspection-detail"),
    path("api/inspections/<uuid:inspection_id>/rerun", vision_views.inspection_rerun, name="inspection-rerun"),
    path("api/inspections/<uuid:inspection_id>/feedback", vision_views.inspection_feedback, name="inspection-feedback"),
    path("api/inspections/<uuid:inspection_id>/images/<int:position>", vision_views.inspection_image, name="inspection-image"),
    path("api/me", views.api_me, name="me"),
    # Who holds which role (administrators only): the screen, the people, and the record of changes
    path("access", access_views.access_page, name="access-page"),
    path("api/access", access_views.access_overview, name="access-overview"),
    path("api/access/changes", access_views.access_changes, name="access-changes"),
    path("api/access/people/<int:user_id>/console", access_views.access_console_role, name="access-console-role"),
    path("api/access/people/<int:user_id>/projects", access_views.access_project_role, name="access-project-role"),
    # Whether the teaching helper may download ready-made models (administrators change it; Trainers can read it)
    path("api/console/training-downloads", settings_views.training_downloads, name="console-training-downloads"),
    path("api/projects", views.project_collection, name="project-list-create"),
    path("api/projects/<uuid:project_id>", views.project_detail, name="project-detail"),
    path("api/projects/<uuid:project_id>/assets", views.project_assets, name="project-assets"),
    path(
        "api/projects/<uuid:project_id>/assets/<uuid:asset_id>",
        views.project_asset_detail,
        name="project-asset-detail",
    ),
    path(
        "api/projects/<uuid:project_id>/assets/<uuid:asset_id>/content",
        views.project_asset_content,
        name="project-asset-content",
    ),
    path(
        "api/projects/<uuid:project_id>/assets/<uuid:asset_id>/annotations",
        views.project_asset_annotations,
        name="project-asset-annotations",
    ),
    path(
        "api/projects/<uuid:project_id>/assets/<uuid:asset_id>/annotations/<uuid:annotation_id>",
        views.box_annotation_detail,
        name="box-annotation-detail",
    ),
    path("api/autolabel", autolabel_views.autolabel_status, name="autolabel-status"),
    path(
        "api/projects/<uuid:project_id>/autolabel",
        autolabel_views.project_autolabel,
        name="project-autolabel",
    ),
    path(
        "api/projects/<uuid:project_id>/autolabel/<uuid:job_id>/cancel",
        autolabel_views.project_autolabel_cancel,
        name="project-autolabel-cancel",
    ),
    path(
        "api/projects/<uuid:project_id>/annotations/review",
        autolabel_views.project_annotation_review,
        name="project-annotation-review",
    ),
    path(
        "api/projects/<uuid:project_id>/datasets",
        dataset_views.project_datasets,
        name="project-datasets",
    ),
    path(
        "api/projects/<uuid:project_id>/datasets/readiness",
        dataset_views.project_dataset_readiness,
        name="project-dataset-readiness",
    ),
    path(
        "api/projects/<uuid:project_id>/datasets/<uuid:dataset_id>",
        dataset_views.project_dataset_detail,
        name="project-dataset-detail",
    ),
    path(  # trailing-slash alias
        "api/projects/<uuid:project_id>/datasets/<uuid:dataset_id>/",
        dataset_views.project_dataset_detail,
    ),
    path(
        "api/projects/<uuid:project_id>/datasets/<uuid:dataset_id>/export",
        dataset_views.project_dataset_export,
        name="project-dataset-export",
    ),
    path(
        "api/projects/<uuid:project_id>/datasets/<uuid:dataset_id>/exports",
        dataset_views.project_dataset_exports,
        name="project-dataset-exports",
    ),

    # Training runs, model versions and staged approval
    path("api/projects/<uuid:project_id>/training/backends", training_views.training_backends, name="training-backends"),
    path("api/projects/<uuid:project_id>/training/readiness", training_views.training_readiness, name="training-readiness"),
    path("api/projects/<uuid:project_id>/training/runs", training_views.training_runs, name="training-runs"),
    path(
        "api/projects/<uuid:project_id>/training/runs/<uuid:run_id>",
        training_views.training_run_detail,
        name="training-run-detail",
    ),
    path(
        "api/projects/<uuid:project_id>/training/runs/<uuid:run_id>/cancel",
        training_views.training_run_cancel,
        name="training-run-cancel",
    ),
    path("api/projects/<uuid:project_id>/models", training_views.model_collection, name="model-list"),
    path("api/projects/<uuid:project_id>/models/<uuid:model_id>", training_views.model_detail, name="model-detail"),
    path(
        "api/projects/<uuid:project_id>/models/<uuid:model_id>/evaluation",
        training_views.model_evaluation,
        name="model-evaluation",
    ),
    path("api/projects/<uuid:project_id>/models/<uuid:model_id>/stage", training_views.model_stage, name="model-stage"),
    path(
        "api/projects/<uuid:project_id>/models/<uuid:model_id>/approve", training_views.model_approve, name="model-approve"
    ),
    path(
        "api/projects/<uuid:project_id>/models/<uuid:model_id>/rollback",
        training_views.model_rollback,
        name="model-rollback",
    ),
    # Inference apps, observations and feedback
    path("api/projects/<uuid:project_id>/inference/apps", inference_views.inference_apps, name="inference-apps"),
    path(
        "api/projects/<uuid:project_id>/inference/apps/<uuid:app_id>",
        inference_views.inference_app_detail,
        name="inference-app-detail",
    ),
    path(
        "api/projects/<uuid:project_id>/inference/apps/<uuid:app_id>/preview",
        inference_views.inference_app_preview,
        name="inference-app-preview",
    ),
    path(
        "api/projects/<uuid:project_id>/inference/apps/<uuid:app_id>/run",
        inference_views.inference_app_run,
        name="inference-app-run",
    ),
    path(
        "api/projects/<uuid:project_id>/inference/apps/<uuid:app_id>/observations",
        inference_views.inference_observations,
        name="inference-observations",
    ),
    path(
        "api/projects/<uuid:project_id>/inference/apps/<uuid:app_id>/observations/<uuid:observation_id>",
        inference_views.inference_observation_detail,
        name="inference-observation-detail",
    ),
    path(
        "api/projects/<uuid:project_id>/inference/apps/<uuid:app_id>/observations/<uuid:observation_id>/image",
        inference_views.inference_observation_image,
        name="inference-observation-image",
    ),
    path(
        "api/projects/<uuid:project_id>/inference/apps/<uuid:app_id>/observations/<uuid:observation_id>/feedback",
        inference_views.inference_observation_feedback,
        name="inference-observation-feedback",
    ),
    path(
        "api/projects/<uuid:project_id>/inference/apps/<uuid:app_id>/observations/<uuid:observation_id>/decision",
        inference_views.inference_observation_decision,
        name="inference-observation-decision",
    ),
    path(
        "api/projects/<uuid:project_id>/inference/apps/<uuid:app_id>/feedback/export",
        inference_views.inference_feedback_export,
        name="inference-feedback-export",
    ),
    # Pages: the builder ("Set up your check screen") and the operator's check screen
    path(
        "projects/<uuid:project_id>/check-screen/setup",
        inference_pages.checkscreen_setup,
        name="checkscreen-setup",
    ),
    path("check/<slug:slug>", inference_pages.checkscreen_operator, name="checkscreen-operator"),
    # Operator feedback pulled into the project as reviewable pictures (Needs-review boxes only)
    path("api/projects/<uuid:project_id>/feedback/corrections", feedback_views.feedback_corrections, name="feedback-corrections"),
    path(
        "api/projects/<uuid:project_id>/feedback/corrections/import",
        feedback_views.feedback_corrections_import,
        name="feedback-corrections-import",
    ),
    path("api/projects/<uuid:project_id>/datasets/health", dataset_views.project_dataset_health, name="project-dataset-health"),
    path("api/projects/<uuid:project_id>/models/<uuid:model_id>/results/plain", results_views.model_results_plain, name="model-results-plain"),
]
