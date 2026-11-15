"""Pages: the public landing page, the signed-in home, the inspection flow, the report, history and AI settings."""

from __future__ import annotations

from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from .models import Inspection
from .permissions import access
from .vision import catalog, service


def _ctx(request: HttpRequest, nav: str, **extra) -> dict:
    return {"nav": nav, "templates_data": catalog.all_public(), **extra}


@require_http_methods(["GET", "HEAD"])
@ensure_csrf_cookie
def landing_or_home(request: HttpRequest):
    if not request.user.is_authenticated:
        return render(request, "projects/landing.html", {"templates": catalog.TEMPLATES})
    return render(request, "projects/home.html", _ctx(request, "home", templates=catalog.TEMPLATES))


# Anyone may see the landing page; a signed-in person gets their home, which only reads.
landing_or_home.athena_access = {"GET": "view"}


@access()
@require_http_methods(["GET", "HEAD"])
@ensure_csrf_cookie
def inspect_new(request: HttpRequest):
    chosen = request.GET.get("template", "")
    return render(request, "projects/inspect_new.html", _ctx(
        request, "new", chosen=chosen if chosen in catalog.BY_KEY else "",
        prefill=(request.GET.get("q") or "")[:2000],
    ))


@access()
@require_http_methods(["GET", "HEAD"])
@ensure_csrf_cookie
def inspections_page(request: HttpRequest):
    return render(request, "projects/inspections.html", _ctx(request, "inspections"))


@access()
@require_http_methods(["GET", "HEAD"])
@ensure_csrf_cookie
def inspection_report(request: HttpRequest, inspection_id):
    if not Inspection.objects.filter(id=inspection_id, created_by=request.user).exists():
        raise Http404("No such inspection.")
    return render(request, "projects/inspection_report.html", _ctx(request, "inspections", inspection_id=str(inspection_id)))


@access()
@require_http_methods(["GET", "HEAD"])
@ensure_csrf_cookie
def ai_settings_page(request: HttpRequest):
    return render(request, "projects/ai_settings.html", _ctx(request, "ai", builtin=service.builtin_connection()))


@require_http_methods(["GET", "HEAD"])
@ensure_csrf_cookie
def docs_page(request: HttpRequest):
    template = "projects/docs.html" if request.user.is_authenticated else "projects/docs_public.html"
    canonical_url = request.build_absolute_uri(reverse("docs-page"))
    return render(request, template, _ctx(request, "docs", canonical_url=canonical_url))


# Anyone (including search engines and AI crawlers) may read the step-by-step docs wiki.
docs_page.athena_access = {"GET": "view"}


@require_http_methods(["GET", "HEAD"])
def robots_txt(request: HttpRequest) -> HttpResponse:
    sitemap_url = request.build_absolute_uri(reverse("sitemap-xml"))
    body = "\n".join([
        "User-agent: *",
        "Allow: /",
        "Allow: /docs",
        "Disallow: /api/",
        "Disallow: /access",
        "Disallow: /settings/",
        "Disallow: /inspections",
        "Disallow: /detectors",
        "Disallow: /check/",
        "Disallow: /setup/",
        f"Sitemap: {sitemap_url}",
        "",
    ])
    return HttpResponse(body, content_type="text/plain; charset=utf-8")


robots_txt.athena_access = {"GET": "view"}


@require_http_methods(["GET", "HEAD"])
def sitemap_xml(request: HttpRequest) -> HttpResponse:
    home_url = request.build_absolute_uri(reverse("home"))
    docs_url = request.build_absolute_uri(reverse("docs-page"))
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"  <url><loc>{home_url}</loc><changefreq>weekly</changefreq><priority>1.0</priority></url>\n"
        f"  <url><loc>{docs_url}</loc><changefreq>weekly</changefreq><priority>0.9</priority></url>\n"
        "</urlset>\n"
    )
    return HttpResponse(xml, content_type="application/xml; charset=utf-8")


sitemap_xml.athena_access = {"GET": "view"}
