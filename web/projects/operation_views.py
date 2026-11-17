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
    GeometryVersion, Operation, OperationDraft, OperationDraftVersion, OperationSource,
    Site, SourceViewVersion,
)
from .permissions import access
from .vision_views import _error, _json_body, _no_store


def _data(row):
    recording = getattr(row, "recording", None)
    video = None if recording is None else {
        "id": str(recording.id), "filename": recording.filename, "sha256": recording.sha256,
        "size_bytes": recording.size_bytes, "width": recording.width, "height": recording.height,
        "duration_seconds": recording.duration_seconds,
        "content_url": f"/api/operation-drafts/{row.id}/recording/content",
    }
    return {"id": str(row.id), "operation_id": None if row.operation_id is None else str(row.operation_id),
            "name": row.name, "line": row.line, "reference_events": row.reference_events,
            "revision": row.revision, "outcome": "people_counting", "status": "draft",
            "updated_at": row.updated_at.isoformat(), "recording": video}


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
    recording_data = None if recording is None else {
        "sha256": recording.sha256,
        "size_bytes": recording.size_bytes,
        "width": recording.width,
        "height": recording.height,
        "duration_seconds": recording.duration_seconds,
    }
    return {
        "schema_version": 1,
        "draft": {
            "id": str(row.id), "revision": row.revision, "name": row.name,
            "operation_id": None if row.operation_id is None else str(row.operation_id),
        },
        "source": source,
        "geometry": {"line": row.line, "state": "draft_configuration"},
        "recipe": {"key": "people_counting", "state": "specification_only", "version": None},
        "recording": recording_data,
        "reference_events": row.reference_events,
        "component_pins": {"source_view": None, "geometry": None, "recipe": None, "model": None, "screen": None, "policy": None},
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
        "id": str(version.id), "source_id": str(version.source_id), "version": version.version,
        "configuration": version.configuration, "sha256": version.configuration_sha256,
        "created_at": version.created_at.isoformat(),
    }


def _geometry_data(version):
    payload = {"source_view_version_id": str(version.source_view_version_id), "geometry": version.geometry}
    if _component_sha256(payload) != version.geometry_sha256:
        raise ValueError("A geometry version failed its integrity check.")
    return {
        "id": str(version.id), "operation_id": str(version.operation_id), "version": version.version,
        "source_view_version_id": str(version.source_view_version_id),
        "geometry": version.geometry, "sha256": version.geometry_sha256,
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


def _operation_line(value):
    fields = {"start", "end", "positive_label", "negative_label"}
    if not isinstance(value, dict) or value.keys() != fields:
        raise ValueError("Provide line endpoints and both direction labels.")
    points = []
    for key in ("start", "end"):
        point = value[key]
        if not isinstance(point, list) or len(point) != 2 or any(
            isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item) or not 0 <= item <= 1
            for item in point
        ):
            raise ValueError("Geometry coordinates must be finite numbers between 0 and 1.")
        points.append(point)
    if points[0] == points[1]:
        raise ValueError("The geometry line must have two different endpoints.")
    for key in ("positive_label", "negative_label"):
        label = value[key]
        if not isinstance(label, str) or not label.strip() or len(label.strip()) > 40:
            raise ValueError("Direction labels must contain 1 to 40 characters.")
    return {**value, "positive_label": value["positive_label"].strip(), "negative_label": value["negative_label"].strip()}


def _record_draft_version(row):
    snapshot = _version_snapshot(row)
    return OperationDraftVersion.objects.create(
        draft=row, revision=row.revision, created_by_id=row.created_by_id,
        snapshot=snapshot, snapshot_sha256=_snapshot_sha256(snapshot),
    )


def _version_data(version):
    if _snapshot_sha256(version.snapshot) != version.snapshot_sha256:
        raise ValueError("A saved draft version failed its integrity check.")
    return {
        "id": str(version.id), "revision": version.revision,
        "sha256": version.snapshot_sha256, "created_at": version.created_at.isoformat(),
        "snapshot": version.snapshot,
    }


def _validate(payload, *, update=False, recording_duration=None, existing_events=None, existing_line=None):
    allowed = {"name", "line", "reference_events"} | ({"revision"} if update else set())
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
            if not isinstance(point, list) or len(point) != 2 or any(
                isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 1 or not math.isfinite(v)
                for v in point
            ):
                raise ValueError("Line coordinates must be numbers between 0 and 1.")
            points.append(point)
        if points[0] == points[1]:
            raise ValueError("The line must have two different endpoints.")
        for key in ("positive_label", "negative_label"):
            value = line[key]
            if not isinstance(value, str) or not value.strip() or len(value.strip()) > 40:
                raise ValueError("Enter both direction labels, up to 40 characters each.")
        line = {**line, "positive_label": line["positive_label"].strip(), "negative_label": line["negative_label"].strip()}
    line_changed = update and line != existing_line
    if line_changed and existing_line is not None and line is None and payload.get("reference_events"):
        raise ValueError("Save the changed line before clearing its reference crossings.")
    events = [] if line_changed and existing_line is not None else payload.get("reference_events", existing_events or [])
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
            isinstance(event_time, bool) or not isinstance(event_time, (int, float))
            or not finite_time or event_time < 0
            or recording_duration is None or event_time > recording_duration
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
        "id": str(operation.id), "name": operation.name, "site_id": str(operation.site_id),
        "source_id": str(operation.source_id), "visibility": "private",
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
    operation = get_object_or_404(
        Operation.objects.select_related("source"), id=operation_id, created_by=request.user
    )
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
            source=operation.source, version=version, created_by=request.user,
            configuration=configuration, configuration_sha256=_component_sha256(configuration),
        )
    return _no_store(JsonResponse({"source_view": _source_view_data(row)}, status=201))


@access(POST="label")
@require_http_methods(["GET", "POST"])
def operation_geometry_versions(request: HttpRequest, operation_id):
    operation = get_object_or_404(
        Operation.objects.select_related("source"), id=operation_id, created_by=request.user
    )
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
            operation=operation, source_view_version=source_view, version=version, created_by=request.user,
            geometry=geometry, geometry_sha256=_component_sha256(hash_payload),
        )
    return _no_store(JsonResponse({"geometry_version": _geometry_data(row)}, status=201))


@access(POST="label")
@require_http_methods(["GET", "POST"])
def operation_drafts(request: HttpRequest):
    if request.method == "GET":
        rows = OperationDraft.objects.filter(created_by=request.user).select_related("operation", "recording")[:200]
        return _no_store(JsonResponse({"drafts": [_data(row) for row in rows]}))
    try:
        payload = _json_body(request)
        operation = None
        if "operation_id" in payload:
            operation_id = _scope_uuid(payload, "operation_id")
            operation = get_object_or_404(Operation, id=operation_id, created_by=request.user)
            payload = {key: value for key, value in payload.items() if key != "operation_id"}
        name, line, reference_events, _ = _validate(payload)
    except ValueError as error:
        return _error(str(error))
    with transaction.atomic():
        row = OperationDraft.objects.create(
            created_by=request.user, operation=operation, name=name, line=line, reference_events=reference_events
        )
        _record_draft_version(row)
    return _no_store(JsonResponse({"draft": _data(row)}, status=201))


@access(PUT="label", DELETE="label")
@require_http_methods(["GET", "PUT", "DELETE"])
def operation_draft(request: HttpRequest, draft_id):
    rows = OperationDraft.objects.filter(created_by=request.user)
    row = get_object_or_404(rows, id=draft_id)
    if request.method == "GET":
        return _no_store(JsonResponse({"draft": _data(row)}))
    if request.method == "DELETE":
        row.delete()
        response = HttpResponse(status=204)
        response["Cache-Control"] = "no-store"
        return response
    try:
        recording = getattr(row, "recording", None)
        name, line, reference_events, revision = _validate(
            _json_body(request), update=True,
            recording_duration=None if recording is None else recording.duration_seconds,
            existing_events=row.reference_events, existing_line=row.line,
        )
    except ValueError as error:
        return _error(str(error))
    with transaction.atomic():
        changed = rows.filter(id=draft_id, revision=revision).update(
            name=name, line=line, reference_events=reference_events,
            revision=F("revision") + 1, updated_at=timezone.now()
        )
        if not changed:
            return _error("This draft changed elsewhere. Reload it before saving.", 409)
        row = get_object_or_404(rows, id=draft_id)
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
