from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
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
]
