"""Private drafts for a future recorded people-counting operation.

These drafts do not run, publish, or share an operation.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import transaction
from django.db.models import F
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .models import (
    GeometryVersion,
    ModelVersion,
    Operation,
    OperationDraft,
    OperationDraftVersion,
    OperationModelBinding,
    OperationScreenVersion,
    OperationSource,
    PolicyVersion,
    RecipeVersion,
    Site,
    SourceViewVersion,
)
from .permissions import access
from .vision_views import _error, _json_body, _no_store


def _data(row):
    recording = getattr(row, "recording", None)
    video = (
        None
        if recording is None
        else {
            "id": str(recording.id),
            "filename": recording.filename,
            "sha256": recording.sha256,
            "size_bytes": recording.size_bytes,
            "width": recording.width,
            "height": recording.height,
            "duration_seconds": recording.duration_seconds,
            "content_url": f"/api/operation-drafts/{row.id}/recording/content",
        }
    )
    return {
        "id": str(row.id),
        "operation_id": None if row.operation_id is None else str(row.operation_id),
        "name": row.name,
        "line": row.line,
        "reference_events": row.reference_events,
        "revision": row.revision,
        "outcome": "people_counting",
        "status": "draft",
        "updated_at": row.updated_at.isoformat(),
        "recording": video,
        "pins": {
            "geometry_version_id": None if row.geometry_version_id is None else str(row.geometry_version_id),
            "recipe_version_id": None if row.recipe_version_id is None else str(row.recipe_version_id),
            "policy_version_id": None if row.policy_version_id is None else str(row.policy_version_id),
            "model_binding_id": None if row.model_binding_id is None else str(row.model_binding_id),
            "screen_version_id": None if row.screen_version_id is None else str(row.screen_version_id),
        },
        "release_bundles": row.release_bundles.count(),
    }


def _snapshot_pins(row):
    """References to the immutable component versions the draft builds on.

    The source-view pin is derived from the geometry pin: a geometry version is
    only valid with the exact source-view version it was created against.
    """
    pins = {"source_view": None, "geometry": None, "recipe": None, "model": None, "screen": None, "policy": None}
    if row.geometry_version_id:
        geometry = row.geometry_version
        source_view = geometry.source_view_version
        pins["geometry"] = {
            "id": str(geometry.id),
            "version": geometry.version,
            "sha256": geometry.geometry_sha256,
            "source_view_version_id": str(source_view.id),
        }
        pins["source_view"] = {
            "id": str(source_view.id),
            "source_id": str(source_view.source_id),
            "version": source_view.version,
            "sha256": source_view.configuration_sha256,
        }
    if row.recipe_version_id:
        recipe = row.recipe_version
        pins["recipe"] = {
            "id": str(recipe.id),
            "version": recipe.version,
            "recipe_key": recipe.recipe_key,
            "sha256": recipe.configuration_sha256,
        }
    if row.policy_version_id:
        policy = row.policy_version
        pins["policy"] = {"id": str(policy.id), "version": policy.version, "sha256": policy.configuration_sha256}
    if row.model_binding_id:
        binding = row.model_binding
        pins["model"] = {
            "id": str(binding.id),
            "version": binding.version,
            "model_version_id": str(binding.model_version_id),
            "sha256": binding.compatibility_sha256,
        }
    if row.screen_version_id:
        screen = row.screen_version
        pins["screen"] = {"id": str(screen.id), "version": screen.version, "sha256": screen.configuration_sha256}
    return pins


def _version_snapshot(row):
    operation = row.operation
    source = None
    if operation is not None:
        source = {
            "operation_id": str(operation.id),
            "site_id": str(operation.site_id),
            "site_name": operation.site.name,
            "timezone_name": operation.site.timezone_name,
            "source_id": str(operation.source_id),
            "source_name": operation.source.name,
            "source_kind": operation.source.kind,
        }
    recording = getattr(row, "recording", None)
    recording_data = (
        None
        if recording is None
        else {
            "sha256": recording.sha256,
            "size_bytes": recording.size_bytes,
            "width": recording.width,
            "height": recording.height,
            "duration_seconds": recording.duration_seconds,
        }
    )
    pins = _snapshot_pins(row)
    return {
        "schema_version": 1,
        "draft": {
            "id": str(row.id),
            "revision": row.revision,
            "name": row.name,
            "operation_id": None if row.operation_id is None else str(row.operation_id),
        },
        "source": source,
        "geometry": {"line": row.line, "state": "pinned_version" if pins["geometry"] else "draft_configuration"},
        "recipe": {
            "key": "people_counting",
            "state": "pinned_version" if pins["recipe"] else "specification_only",
            "version": None if pins["recipe"] is None else pins["recipe"]["version"],
        },
        "recording": recording_data,
        "reference_events": row.reference_events,
        "component_pins": pins,
        "release_ready": False,
    }


def _snapshot_sha256(snapshot):
    encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _component_sha256(payload):
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _source_view_data(version):
    if _component_sha256(version.configuration) != version.configuration_sha256:
        raise ValueError("A source-view version failed its integrity check.")
    return {
        "id": str(version.id),
        "source_id": str(version.source_id),
        "version": version.version,
        "configuration": version.configuration,
        "sha256": version.configuration_sha256,
        "created_at": version.created_at.isoformat(),
    }


def _geometry_data(version):
    payload = {"source_view_version_id": str(version.source_view_version_id), "geometry": version.geometry}
    if _component_sha256(payload) != version.geometry_sha256:
        raise ValueError("A geometry version failed its integrity check.")
    return {
        "id": str(version.id),
        "operation_id": str(version.operation_id),
        "version": version.version,
        "source_view_version_id": str(version.source_view_version_id),
        "geometry": version.geometry,
        "sha256": version.geometry_sha256,
        "created_at": version.created_at.isoformat(),
    }


def _recipe_data(version):
    payload = {"recipe_key": version.recipe_key, "configuration": version.configuration}
    if _component_sha256(payload) != version.configuration_sha256:
        raise ValueError("A recipe version failed its integrity check.")
    return {
        "id": str(version.id),
        "operation_id": str(version.operation_id),
        "recipe_key": version.recipe_key,
        "version": version.version,
        "configuration": version.configuration,
        "sha256": version.configuration_sha256,
        "created_at": version.created_at.isoformat(),
    }


def _policy_data(version):
    payload = {"configuration": version.configuration}
    if _component_sha256(payload) != version.configuration_sha256:
        raise ValueError("A policy version failed its integrity check.")
    return {
        "id": str(version.id),
        "operation_id": str(version.operation_id),
        "version": version.version,
        "configuration": version.configuration,
        "sha256": version.configuration_sha256,
        "created_at": version.created_at.isoformat(),
    }


def _source_view_configuration(value):
    fields = {"width", "height", "rotation", "horizontal_flip", "vertical_flip"}
    if not isinstance(value, dict) or value.keys() != fields:
        raise ValueError("Provide width, height, rotation, and both flip settings.")
    if type(value["width"]) is not int or not 1 <= value["width"] <= 16384:
        raise ValueError("Source width must be an integer from 1 to 16384 pixels.")
    if type(value["height"]) is not int or not 1 <= value["height"] <= 16384:
        raise ValueError("Source height must be an integer from 1 to 16384 pixels.")
    if type(value["rotation"]) is not int or value["rotation"] not in {0, 90, 180, 270}:
        raise ValueError("Rotation must be 0, 90, 180, or 270 degrees.")
    if type(value["horizontal_flip"]) is not bool or type(value["vertical_flip"]) is not bool:
        raise ValueError("Flip settings must be true or false.")
    return value


def _recipe_configuration(value):
    fields = {"dead_band", "confirmations", "max_gap_seconds", "comparison_tolerance_seconds"}
    if not isinstance(value, dict) or value.keys() != fields:
        raise ValueError("Provide the recipe dead band, confirmations, maximum gap, and comparison tolerance.")
    for key in ("dead_band",):
        item = value[key]
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item) or not 0 <= item < 1:
            raise ValueError("The recipe dead band must be a number between 0 and 1.")
    if type(value["confirmations"]) is not int or not 1 <= value["confirmations"] <= 100:
        raise ValueError("Recipe confirmations must be an integer from 1 to 100.")
    for key in ("max_gap_seconds", "comparison_tolerance_seconds"):
        item = value[key]
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item) or item <= 0:
            raise ValueError(f"The recipe {key.replace('_', ' ')} must be a positive number.")
    return value


def _policy_configuration(value):
    fields = {"evidence_retention_days", "max_coverage_gap_seconds"}
    if not isinstance(value, dict) or value.keys() != fields:
        raise ValueError("Provide the evidence retention period and the maximum coverage gap.")
    if type(value["evidence_retention_days"]) is not int or not 1 <= value["evidence_retention_days"] <= 3650:
        raise ValueError("Evidence retention must be an integer from 1 to 3,650 days.")
    gap = value["max_coverage_gap_seconds"]
    if isinstance(gap, bool) or not isinstance(gap, (int, float)) or not math.isfinite(gap) or gap <= 0:
        raise ValueError("The maximum coverage gap must be a positive number of seconds.")
    return value


def _operation_line(value):
    fields = {"start", "end", "positive_label", "negative_label"}
    if not isinstance(value, dict) or value.keys() != fields:
        raise ValueError("Provide line endpoints and both direction labels.")
    points = []
    for key in ("start", "end"):
        point = value[key]
        if (
            not isinstance(point, list)
            or len(point) != 2
            or any(
                isinstance(item, bool)
                or not isinstance(item, (int, float))
                or not math.isfinite(item)
                or not 0 <= item <= 1
                for item in point
            )
        ):
            raise ValueError("Geometry coordinates must be finite numbers between 0 and 1.")
        points.append(point)
    if points[0] == points[1]:
        raise ValueError("The geometry line must have two different endpoints.")
    for key in ("positive_label", "negative_label"):
        label = value[key]
        if not isinstance(label, str) or not label.strip() or len(label.strip()) > 40:
            raise ValueError("Direction labels must contain 1 to 40 characters.")
    return {
        **value,
        "positive_label": value["positive_label"].strip(),
        "negative_label": value["negative_label"].strip(),
    }


def _record_draft_version(row):
    snapshot = _version_snapshot(row)
    return OperationDraftVersion.objects.create(
        draft=row,
        revision=row.revision,
        created_by_id=row.created_by_id,
        snapshot=snapshot,
        snapshot_sha256=_snapshot_sha256(snapshot),
    )


def _version_data(version):
    if _snapshot_sha256(version.snapshot) != version.snapshot_sha256:
        raise ValueError("A saved draft version failed its integrity check.")
    return {
        "id": str(version.id),
        "revision": version.revision,
        "sha256": version.snapshot_sha256,
        "created_at": version.created_at.isoformat(),
        "snapshot": version.snapshot,
    }


def _validate(payload, *, update=False, recording_duration=None, existing_events=None, existing_line=None):
    allowed = {"name", "line", "reference_events"} | set(_PIN_FIELDS) | ({"revision"} if update else set())
    if payload.keys() - allowed:
        raise ValueError("These draft details are not supported.")
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
        raise ValueError("Enter a name of up to 120 characters.")
    line = payload.get("line")
    if line is not None:
        if not isinstance(line, dict) or set(line) != {"start", "end", "positive_label", "negative_label"}:
            raise ValueError("Provide the line endpoints and both direction labels.")
        points = []
        for key in ("start", "end"):
            point = line[key]
            if (
                not isinstance(point, list)
                or len(point) != 2
                or any(
                    isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 1 or not math.isfinite(v)
                    for v in point
                )
            ):
                raise ValueError("Line coordinates must be numbers between 0 and 1.")
            points.append(point)
        if points[0] == points[1]:
            raise ValueError("The line must have two different endpoints.")
        for key in ("positive_label", "negative_label"):
            value = line[key]
            if not isinstance(value, str) or not value.strip() or len(value.strip()) > 40:
                raise ValueError("Enter both direction labels, up to 40 characters each.")
        line = {
            **line,
            "positive_label": line["positive_label"].strip(),
            "negative_label": line["negative_label"].strip(),
        }
    line_changed = update and line != existing_line
    if line_changed and existing_line is not None and line is None and payload.get("reference_events"):
        raise ValueError("Save the changed line before clearing its reference crossings.")
    events = (
        [] if line_changed and existing_line is not None else payload.get("reference_events", existing_events or [])
    )
    if not isinstance(events, list) or len(events) > 1000:
        raise ValueError("Provide up to 1,000 reference crossings.")
    if events and (line is None or recording_duration is None):
        raise ValueError("Add a recording and counting line before marking reference crossings.")
    normalized_events = []
    for event in events:
        if not isinstance(event, dict) or set(event) != {"time", "direction"}:
            raise ValueError("Each reference crossing needs a time and direction.")
        event_time = event["time"]
        finite_time = not isinstance(event_time, float) or math.isfinite(event_time)
        if (
            isinstance(event_time, bool)
            or not isinstance(event_time, (int, float))
            or not finite_time
            or event_time < 0
            or recording_duration is None
            or event_time > recording_duration
        ):
            raise ValueError("Reference crossing times must be within the recording.")
        direction = event["direction"]
        if not isinstance(direction, str) or direction not in {"negative_to_positive", "positive_to_negative"}:
            raise ValueError("Choose one of the two marked line directions.")
        normalized_events.append({"time": event_time, "direction": direction})
    revision = payload.get("revision")
    if update and (type(revision) is not int or revision < 1):
        raise ValueError("Provide the current draft revision.")
    return name.strip(), line, normalized_events, revision


_PIN_FIELDS = (
    "geometry_version_id",
    "recipe_version_id",
    "policy_version_id",
    "model_binding_id",
    "screen_version_id",
)


def _validate_pins(payload, operation, line):
    """Resolve optional component-version pins for one draft save.

    Pins must reference immutable versions of the draft's own operation, and a
    geometry pin is only valid while the draft line still matches the pinned
    geometry. The caller resolves ownership; this returns the model instances.
    """
    pins = {}
    for field in _PIN_FIELDS:
        if field in payload:
            value = payload[field]
            if value is None:
                pins[field] = None
                continue
            try:
                pins[field] = uuid.UUID(value)
            except (ValueError, AttributeError, TypeError) as error:
                raise ValueError(f"Provide a valid {field.replace('_', ' ')}.") from error
    if not pins:
        return None
    resolved = {}
    if "geometry_version_id" in pins:
        geometry_id = pins["geometry_version_id"]
        if geometry_id is None:
            resolved["geometry"] = None
        else:
            if operation is None:
                raise ValueError("Bind the draft to an operation before pinning component versions.")
            geometry = (
                GeometryVersion.objects.select_related("source_view_version")
                .filter(id=geometry_id, operation=operation)
                .first()
            )
            if geometry is None:
                raise ValueError("Choose a geometry version that belongs to this draft's operation.")
            if line is None or geometry.geometry.get("line") != line:
                raise ValueError("Save the matching counting line before pinning this geometry version.")
            resolved["geometry"] = geometry
    if "recipe_version_id" in pins:
        recipe_id = pins["recipe_version_id"]
        if recipe_id is None:
            resolved["recipe"] = None
        else:
            if operation is None:
                raise ValueError("Bind the draft to an operation before pinning component versions.")
            recipe = RecipeVersion.objects.filter(id=recipe_id, operation=operation).first()
            if recipe is None:
                raise ValueError("Choose a recipe version that belongs to this draft's operation.")
            resolved["recipe"] = recipe
    if "policy_version_id" in pins:
        policy_id = pins["policy_version_id"]
        if policy_id is None:
            resolved["policy"] = None
        else:
            if operation is None:
                raise ValueError("Bind the draft to an operation before pinning component versions.")
            policy = PolicyVersion.objects.filter(id=policy_id, operation=operation).first()
            if policy is None:
                raise ValueError("Choose a policy version that belongs to this draft's operation.")
            resolved["policy"] = policy
    if "model_binding_id" in pins:
        binding_id = pins["model_binding_id"]
        if binding_id is None:
            resolved["model_binding"] = None
        else:
            if operation is None:
                raise ValueError("Bind the draft to an operation before pinning component versions.")
            binding = OperationModelBinding.objects.filter(id=binding_id, operation=operation).first()
            if binding is None:
                raise ValueError("Choose a model binding that belongs to this draft's operation.")
            resolved["model_binding"] = binding
    if "screen_version_id" in pins:
        screen_id = pins["screen_version_id"]
        if screen_id is None:
            resolved["screen"] = None
        else:
            if operation is None:
                raise ValueError("Bind the draft to an operation before pinning component versions.")
            screen = OperationScreenVersion.objects.filter(id=screen_id, operation=operation).first()
            if screen is None:
                raise ValueError("Choose a screen version that belongs to this draft's operation.")
            resolved["screen"] = screen
    return resolved


def _scope_name(payload):
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
        raise ValueError("Enter a name of up to 120 characters.")
    return name.strip()


def _scope_uuid(payload, key):
    value = payload.get(key)
    if not isinstance(value, str):
        raise ValueError(f"Provide a valid {key.replace('_', ' ')}.")
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError) as error:
        raise ValueError(f"Provide a valid {key.replace('_', ' ')}.") from error


def _site_data(site):
    return {"id": str(site.id), "name": site.name, "timezone_name": site.timezone_name}


def _source_data(source):
    return {"id": str(source.id), "site_id": str(source.site_id), "name": source.name, "kind": source.kind}


def _operation_data(operation):
    return {
        "id": str(operation.id),
        "name": operation.name,
        "site_id": str(operation.site_id),
        "source_id": str(operation.source_id),
        "visibility": "private",
        "created_at": operation.created_at.isoformat(),
    }


@access(POST="label")
@require_http_methods(["GET", "POST"])
def operation_sites(request: HttpRequest):
    if request.method == "GET":
        sites = Site.objects.filter(created_by=request.user)
        return _no_store(JsonResponse({"sites": [_site_data(site) for site in sites]}))
    try:
        payload = _json_body(request)
        if payload.keys() != {"name", "timezone_name"}:
            raise ValueError("Provide only a site name and time zone.")
        name = _scope_name(payload)
        timezone_name = payload["timezone_name"]
        if not isinstance(timezone_name, str) or not timezone_name.strip() or len(timezone_name) > 64:
            raise ValueError("Choose a valid IANA time zone.")
        timezone_name = timezone_name.strip()
        try:
            ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("Choose a valid IANA time zone.") from error
    except ValueError as error:
        return _error(str(error))
    site = Site.objects.create(created_by=request.user, name=name, timezone_name=timezone_name)
    return _no_store(JsonResponse({"site": _site_data(site)}, status=201))


@access(POST="label")
@require_http_methods(["GET", "POST"])
def operation_sources(request: HttpRequest):
    if request.method == "GET":
        sources = OperationSource.objects.filter(site__created_by=request.user).select_related("site")
        site_id = request.GET.get("site_id")
        if site_id:
            try:
                sources = sources.filter(site_id=uuid.UUID(site_id))
            except (ValueError, AttributeError):
                return _error("Provide a valid site ID.")
        return _no_store(JsonResponse({"sources": [_source_data(source) for source in sources]}))
    try:
        payload = _json_body(request)
        if payload.keys() != {"site_id", "name", "kind"}:
            raise ValueError("Provide only a site, source name, and source kind.")
        site_id = _scope_uuid(payload, "site_id")
        name = _scope_name(payload)
        kind = payload["kind"]
        if not isinstance(kind, str) or kind not in dict(OperationSource.KIND_CHOICES):
            raise ValueError("Choose a supported source kind.")
    except ValueError as error:
        return _error(str(error))
    site = get_object_or_404(Site, id=site_id, created_by=request.user)
    source = OperationSource.objects.create(site=site, name=name, kind=kind)
    return _no_store(JsonResponse({"source": _source_data(source)}, status=201))


@access(POST="label")
@require_http_methods(["GET", "POST"])
def operations(request: HttpRequest):
    if request.method == "GET":
        rows = Operation.objects.filter(created_by=request.user).select_related("site", "source")
        return _no_store(JsonResponse({"operations": [_operation_data(row) for row in rows]}))
    try:
        payload = _json_body(request)
        if payload.keys() != {"name", "site_id", "source_id"}:
            raise ValueError("Provide only an operation name, site, and source.")
        name = _scope_name(payload)
        site_id = _scope_uuid(payload, "site_id")
        source_id = _scope_uuid(payload, "source_id")
    except ValueError as error:
        return _error(str(error))
    site = get_object_or_404(Site, id=site_id, created_by=request.user)
    source = get_object_or_404(OperationSource, id=source_id, site__created_by=request.user)
    if source.site_id != site.id:
        return _error("Choose a source that belongs to the selected site.")
    operation = Operation.objects.create(created_by=request.user, site=site, source=source, name=name)
    return _no_store(JsonResponse({"operation": _operation_data(operation)}, status=201))


@access()
@require_http_methods(["GET"])
def operation_detail(request: HttpRequest, operation_id):
    operation = get_object_or_404(
        Operation.objects.select_related("site", "source"), id=operation_id, created_by=request.user
    )
    return _no_store(JsonResponse({"operation": _operation_data(operation)}))


@access(POST="label")
@require_http_methods(["GET", "POST"])
def operation_source_views(request: HttpRequest, operation_id):
    operation = get_object_or_404(Operation.objects.select_related("source"), id=operation_id, created_by=request.user)
    versions = SourceViewVersion.objects.filter(source=operation.source)
    if request.method == "GET":
        try:
            data = [_source_view_data(row) for row in versions]
        except (TypeError, ValueError):
            return _error("A source-view version failed its integrity check.", 500)
        return _no_store(JsonResponse({"source_views": data}))
    try:
        payload = _json_body(request)
        if payload.keys() != {"configuration"}:
            raise ValueError("Provide only the source-view configuration.")
        configuration = _source_view_configuration(payload["configuration"])
    except ValueError as error:
        return _error(str(error))
    with transaction.atomic():
        version = (versions.order_by("-version").values_list("version", flat=True).first() or 0) + 1
        row = SourceViewVersion.objects.create(
            source=operation.source,
            version=version,
            created_by=request.user,
            configuration=configuration,
            configuration_sha256=_component_sha256(configuration),
        )
    return _no_store(JsonResponse({"source_view": _source_view_data(row)}, status=201))


@access(POST="label")
@require_http_methods(["GET", "POST"])
def operation_geometry_versions(request: HttpRequest, operation_id):
    operation = get_object_or_404(Operation.objects.select_related("source"), id=operation_id, created_by=request.user)
    versions = GeometryVersion.objects.filter(operation=operation).select_related("source_view_version")
    if request.method == "GET":
        try:
            data = [_geometry_data(row) for row in versions]
        except (TypeError, ValueError):
            return _error("A geometry version failed its integrity check.", 500)
        return _no_store(JsonResponse({"geometry_versions": data}))
    try:
        payload = _json_body(request)
        if payload.keys() != {"source_view_version_id", "geometry"}:
            raise ValueError("Provide a source-view version and geometry.")
        source_view_id = _scope_uuid(payload, "source_view_version_id")
        geometry_payload = payload["geometry"]
        if not isinstance(geometry_payload, dict) or geometry_payload.keys() != {"line"}:
            raise ValueError("Provide the supported counting-line geometry.")
        geometry = {"line": _operation_line(geometry_payload["line"])}
    except ValueError as error:
        return _error(str(error))
    source_view = get_object_or_404(
        SourceViewVersion, id=source_view_id, source=operation.source, created_by=request.user
    )
    hash_payload = {"source_view_version_id": str(source_view.id), "geometry": geometry}
    with transaction.atomic():
        version = (versions.order_by("-version").values_list("version", flat=True).first() or 0) + 1
        row = GeometryVersion.objects.create(
            operation=operation,
            source_view_version=source_view,
            version=version,
            created_by=request.user,
            geometry=geometry,
            geometry_sha256=_component_sha256(hash_payload),
        )
    return _no_store(JsonResponse({"geometry_version": _geometry_data(row)}, status=201))


@access(POST="label")
@require_http_methods(["GET", "POST"])
def operation_recipe_versions(request: HttpRequest, operation_id):
    operation = get_object_or_404(Operation, id=operation_id, created_by=request.user)
    versions = RecipeVersion.objects.filter(operation=operation)
    if request.method == "GET":
        try:
            data = [_recipe_data(row) for row in versions]
        except (TypeError, ValueError):
            return _error("A recipe version failed its integrity check.", 500)
        return _no_store(JsonResponse({"recipe_versions": data}))
    try:
        payload = _json_body(request)
        if payload.keys() != {"recipe_key", "configuration"}:
            raise ValueError("Provide only a recipe key and configuration.")
        if payload["recipe_key"] not in RecipeVersion.SUPPORTED_KEYS:
            raise ValueError("Choose a supported recipe key.")
        configuration = _recipe_configuration(payload["configuration"])
    except ValueError as error:
        return _error(str(error))
    with transaction.atomic():
        version = (versions.order_by("-version").values_list("version", flat=True).first() or 0) + 1
        row = RecipeVersion.objects.create(
            operation=operation,
            recipe_key=payload["recipe_key"],
            version=version,
            created_by=request.user,
            configuration=configuration,
            configuration_sha256=_component_sha256(
                {"recipe_key": payload["recipe_key"], "configuration": configuration}
            ),
        )
    return _no_store(JsonResponse({"recipe_version": _recipe_data(row)}, status=201))


@access(POST="label")
@require_http_methods(["GET", "POST"])
def operation_policy_versions(request: HttpRequest, operation_id):
    operation = get_object_or_404(Operation, id=operation_id, created_by=request.user)
    versions = PolicyVersion.objects.filter(operation=operation)
    if request.method == "GET":
        try:
            data = [_policy_data(row) for row in versions]
        except (TypeError, ValueError):
            return _error("A policy version failed its integrity check.", 500)
        return _no_store(JsonResponse({"policy_versions": data}))
    try:
        payload = _json_body(request)
        if payload.keys() != {"configuration"}:
            raise ValueError("Provide only the policy configuration.")
        configuration = _policy_configuration(payload["configuration"])
    except ValueError as error:
        return _error(str(error))
    with transaction.atomic():
        version = (versions.order_by("-version").values_list("version", flat=True).first() or 0) + 1
        row = PolicyVersion.objects.create(
            operation=operation,
            version=version,
            created_by=request.user,
            configuration=configuration,
            configuration_sha256=_component_sha256({"configuration": configuration}),
        )
    return _no_store(JsonResponse({"policy_version": _policy_data(row)}, status=201))


@access(POST="label")
@require_http_methods(["GET", "POST"])
def operation_drafts(request: HttpRequest):
    if request.method == "GET":
        rows = OperationDraft.objects.filter(created_by=request.user).select_related(
            "operation",
            "recording",
            "geometry_version",
            "recipe_version",
            "policy_version",
            "model_binding",
            "screen_version",
        )[:200]
        return _no_store(JsonResponse({"drafts": [_data(row) for row in rows]}))
    try:
        payload = _json_body(request)
        operation = None
        if "operation_id" in payload:
            operation_id = _scope_uuid(payload, "operation_id")
            operation = get_object_or_404(
                Operation.objects.select_related("site", "source"), id=operation_id, created_by=request.user
            )
            payload = {key: value for key, value in payload.items() if key != "operation_id"}
        name, line, reference_events, _ = _validate(payload)
        pins = _validate_pins(payload, operation, line)
    except ValueError as error:
        return _error(str(error))
    with transaction.atomic():
        row = OperationDraft.objects.create(
            created_by=request.user,
            operation=operation,
            name=name,
            line=line,
            reference_events=reference_events,
            **(
                {}
                if pins is None
                else {
                    "geometry_version": pins.get("geometry"),
                    "recipe_version": pins.get("recipe"),
                    "policy_version": pins.get("policy"),
                    "model_binding": pins.get("model_binding"),
                    "screen_version": pins.get("screen"),
                }
            ),
        )
        _record_draft_version(row)
    return _no_store(JsonResponse({"draft": _data(row)}, status=201))


@access(PUT="label", DELETE="label")
@require_http_methods(["GET", "PUT", "DELETE"])
def operation_draft(request: HttpRequest, draft_id):
    rows = OperationDraft.objects.filter(created_by=request.user)
    row = get_object_or_404(rows.select_related("operation"), id=draft_id)
    if request.method == "GET":
        return _no_store(JsonResponse({"draft": _data(row)}))
    if request.method == "DELETE":
        row.delete()
        response = HttpResponse(status=204)
        response["Cache-Control"] = "no-store"
        return response
    try:
        recording = getattr(row, "recording", None)
        payload = _json_body(request)
        operation = row.operation
        bind_to = None
        if "operation_id" in payload:
            # A draft joins its operation once, when it is first attached; after that the
            # binding is permanent, like the component versions it makes possible.
            if row.operation_id is not None:
                raise ValueError("This draft already belongs to its operation.")
            bind_to = _scope_uuid(payload, "operation_id")
            payload.pop("operation_id")
            operation = get_object_or_404(
                Operation.objects.select_related("site", "source"), id=bind_to, created_by=request.user
            )
        name, line, reference_events, revision = _validate(
            payload,
            update=True,
            recording_duration=None if recording is None else recording.duration_seconds,
            existing_events=row.reference_events,
            existing_line=row.line,
        )
        pins = _validate_pins(payload, operation, line)
    except ValueError as error:
        return _error(str(error))
    updates = {"name": name, "line": line, "reference_events": reference_events}
    if bind_to is not None:
        updates["operation_id"] = bind_to
    if pins is not None:
        updates["geometry_version"] = pins.get("geometry", row.geometry_version_id and row.geometry_version)
        updates["recipe_version"] = pins.get("recipe", row.recipe_version_id and row.recipe_version)
        updates["policy_version"] = pins.get("policy", row.policy_version_id and row.policy_version)
        updates["model_binding"] = pins.get("model_binding", row.model_binding_id and row.model_binding)
        updates["screen_version"] = pins.get("screen", row.screen_version_id and row.screen_version)
    if line != row.line and row.geometry_version_id and not (pins and "geometry" in pins):
        # A geometry pin is only valid while the draft line matches the pinned geometry; a
        # changed line invalidates a kept pin, exactly as it clears reference crossings. A
        # pin in this same save was already checked against the new line and survives.
        updates["geometry_version"] = None
    with transaction.atomic():
        changed = rows.filter(id=draft_id, revision=revision).update(
            **updates, revision=F("revision") + 1, updated_at=timezone.now()
        )
        if not changed:
            return _error("This draft changed elsewhere. Reload it before saving.", 409)
        row = get_object_or_404(
            rows.select_related(
                "operation",
                "recording",
                "geometry_version",
                "recipe_version",
                "policy_version",
                "model_binding",
                "screen_version",
            ),
            id=draft_id,
        )
        _record_draft_version(row)
    return _no_store(JsonResponse({"draft": _data(row)}))


@access()
@require_http_methods(["GET"])
def operation_draft_versions(request: HttpRequest, draft_id):
    draft = get_object_or_404(OperationDraft.objects.filter(created_by=request.user), id=draft_id)
    versions = list(draft.versions.order_by("-revision", "-id")[:201])
    has_more = len(versions) > 200
    versions = versions[:200]
    try:
        data = [_version_data(version) for version in versions]
    except (TypeError, ValueError):
        return _error("A saved draft version failed its integrity check.", 500)
    return _no_store(JsonResponse({"versions": data, "has_more": has_more}))


@access(POST="label")
@require_http_methods(["GET", "POST"])
def operation_model_bindings(request: HttpRequest, operation_id):
    operation = get_object_or_404(Operation, id=operation_id, created_by=request.user)
    versions = OperationModelBinding.objects.filter(operation=operation).select_related("model_version")
    if request.method == "GET":
        try:
            data = [_model_binding_data(row) for row in versions]
        except (TypeError, ValueError):
            return _error("A model binding failed its integrity check.", 500)
        return _no_store(JsonResponse({"model_bindings": data}))
    try:
        payload = _json_body(request)
        if payload.keys() != {"model_version_id"}:
            raise ValueError("Provide only the model version to bind.")
        model_version_id = _scope_uuid(payload, "model_version_id")
    except ValueError as error:
        return _error(str(error))
    model_version = get_object_or_404(
        ModelVersion.objects.select_related("training_run", "training_run__dataset_version"),
        id=model_version_id,
        training_run__created_by=request.user,
    )
    compatibility = _model_compatibility(operation, model_version)
    if not compatibility["compatible"]:
        return _error(compatibility["reason"], 409)
    with transaction.atomic():
        version = (versions.order_by("-version").values_list("version", flat=True).first() or 0) + 1
        row = OperationModelBinding.objects.create(
            operation=operation,
            version=version,
            created_by=request.user,
            model_version=model_version,
            compatibility=compatibility,
            compatibility_sha256=_component_sha256(compatibility),
        )
    return _no_store(JsonResponse({"model_binding": _model_binding_data(row)}, status=201))


def _model_compatibility(operation, model_version):
    """Server-side compatibility checks for binding a model version to an operation.

    The checks are intentionally conservative and recorded in the binding: the model
    must be an approved version whose training run belongs to the operation's owner,
    taught on a dataset version that same owner created. Models without a traceable
    training run cannot be bound at all (the route's owner scoping 404s them). A high
    detection score never establishes compatibility; artifact-digest re-checks and
    recipe label compatibility remain future work (see the T05 contract).
    """
    checks = {
        "compatible": True,
        "reason": None,
        "model_version_id": str(model_version.id),
        "model_version_number": model_version.version,
        "artifact_sha256": model_version.artifact_sha256,
        "stage": model_version.stage,
        "labels": list(model_version.label_names),
    }
    if model_version.stage != ModelVersion.APPROVED:
        checks.update(compatible=False, reason="Only an approved model version can be bound to an operation.")
        return checks

    run = model_version.training_run
    if run is not None and run.dataset_version.created_by_id != operation.created_by_id:
        checks.update(compatible=False, reason="Choose a model version taught from your own project data.")
        return checks
    return checks


def _model_binding_data(version):
    if _component_sha256(version.compatibility) != version.compatibility_sha256:
        raise ValueError("A model binding failed its integrity check.")
    return {
        "id": str(version.id),
        "operation_id": str(version.operation_id),
        "version": version.version,
        "model_version_id": str(version.model_version_id),
        "compatibility": version.compatibility,
        "sha256": version.compatibility_sha256,
        "created_at": version.created_at.isoformat(),
    }


@access(POST="label")
@require_http_methods(["GET", "POST"])
def operation_screen_versions(request: HttpRequest, operation_id):
    operation = get_object_or_404(Operation, id=operation_id, created_by=request.user)
    versions = OperationScreenVersion.objects.filter(operation=operation)
    if request.method == "GET":
        try:
            data = [_screen_data(row) for row in versions]
        except (TypeError, ValueError):
            return _error("A screen version failed its integrity check.", 500)
        return _no_store(JsonResponse({"screen_versions": data}))
    try:
        payload = _json_body(request)
        if payload.keys() != {"configuration"}:
            raise ValueError("Provide only the screen configuration.")
        configuration = _screen_configuration(payload["configuration"])
    except ValueError as error:
        return _error(str(error))
    with transaction.atomic():
        version = (versions.order_by("-version").values_list("version", flat=True).first() or 0) + 1
        row = OperationScreenVersion.objects.create(
            operation=operation,
            version=version,
            created_by=request.user,
            configuration=configuration,
            configuration_sha256=_component_sha256({"configuration": configuration}),
        )
    return _no_store(JsonResponse({"screen_version": _screen_data(row)}, status=201))


def _screen_configuration(value):
    fields = {"title", "show_entries", "show_exits"}
    if not isinstance(value, dict) or value.keys() != fields:
        raise ValueError("Provide the board title and both metric toggles.")
    title = value["title"]
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 120:
        raise ValueError("Enter a board title of up to 120 characters.")
    if type(value["show_entries"]) is not bool or type(value["show_exits"]) is not bool:
        raise ValueError("Both metric toggles must be true or false.")
    return {**value, "title": title.strip()}


def _screen_data(version):
    payload = {"configuration": version.configuration}
    if _component_sha256(payload) != version.configuration_sha256:
        raise ValueError("A screen version failed its integrity check.")
    return {
        "id": str(version.id),
        "operation_id": str(version.operation_id),
        "version": version.version,
        "configuration": version.configuration,
        "sha256": version.configuration_sha256,
        "created_at": version.created_at.isoformat(),
    }


@access(POST="approve")
@require_http_methods(["GET", "POST"])
def operation_release_bundles(request: HttpRequest, draft_id):
    """Assemble an append-only release bundle from one draft revision's pins.

    Creating a bundle requires every component pin to be present and coherent;
    it never edits the draft or any component version. A bundle is a release
    candidate only — no endpoint publishes or activates it.
    """
    from .models import OperationReleaseBundle

    draft = get_object_or_404(
        OperationDraft.objects.filter(created_by=request.user).select_related(
            "operation",
            "recording",
            "geometry_version",
            "recipe_version",
            "policy_version",
            "model_binding",
            "screen_version",
        ),
        id=draft_id,
    )
    if draft.operation_id is None:
        return _error("Bind the draft to an operation before assembling a release bundle.")
    if request.method == "GET":
        bundles = draft.release_bundles.order_by("-number", "-id")
        try:
            data = [_bundle_data(row) for row in bundles]
        except (TypeError, ValueError):
            return _error("A release bundle failed its integrity check.", 500)
        return _no_store(JsonResponse({"release_bundles": data}))
    snapshot = _version_snapshot(draft)
    missing = [name for name, pin in snapshot["component_pins"].items() if pin is None]
    if missing:
        return _error(
            "Pin every component before assembling a release bundle (still missing: "
            + ", ".join(sorted(missing))
            + ").",
            409,
        )
    if snapshot["recording"] is None:
        return _error("Attach a recording before assembling a release bundle.", 409)
    bundle_snapshot = {
        "schema_version": 1,
        "kind": "operation_release_bundle",
        "operation_id": str(draft.operation_id),
        "draft_id": str(draft.id),
        "draft_revision": draft.revision,
        "component_pins": snapshot["component_pins"],
        "recording": snapshot["recording"],
        "reference_events": snapshot["reference_events"],
        "release_ready": True,
    }
    with transaction.atomic():
        number = (
            OperationReleaseBundle.objects.filter(operation_id=draft.operation_id)
            .order_by("-number")
            .values_list("number", flat=True)
            .first()
            or 0
        ) + 1
        row = OperationReleaseBundle.objects.create(
            operation_id=draft.operation_id,
            draft=draft,
            draft_revision=draft.revision,
            number=number,
            created_by=request.user,
            snapshot=bundle_snapshot,
            snapshot_sha256=_snapshot_sha256(bundle_snapshot),
        )
    return _no_store(JsonResponse({"release_bundle": _bundle_data(row)}, status=201))


def _bundle_data(row):
    if _snapshot_sha256(row.snapshot) != row.snapshot_sha256:
        raise ValueError("A release bundle failed its integrity check.")
    return {
        "id": str(row.id),
        "operation_id": str(row.operation_id),
        "draft_id": str(row.draft_id),
        "draft_revision": row.draft_revision,
        "number": row.number,
        "sha256": row.snapshot_sha256,
        "created_at": row.created_at.isoformat(),
        "snapshot": row.snapshot,
    }
