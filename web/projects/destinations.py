"""Approved network destination guard for operation integrations (T11).

HTTPS outbound destinations (webhooks, exports, future remote calls) must pass
through this choke point. Live camera RTSP is a separate allow-list
(``ATHENA_APPROVED_RTSP_HOSTS`` / :mod:`projects.live_rtsp`, T10). A destination
is allowed only when its scheme and exact host appear in
``settings.ATHENA_APPROVED_DESTINATIONS``. Hostname matching is exact — no
subdomain wildcards, no userinfo, no non-default-port tricks.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from django.conf import settings

ALLOWED_SCHEMES = ("https",)


def _approved_hosts() -> dict[str, set[str]]:
    """Map scheme -> set of approved hosts from settings; lowercase and whitespace-trimmed."""
    approved: dict[str, set[str]] = {}
    for entry in getattr(settings, "ATHENA_APPROVED_DESTINATIONS", ()):
        scheme, _, host = entry.partition("://")
        approved.setdefault(scheme.lower(), set()).add(host.strip().lower())
    return approved


class DestinationRefused(ValueError):
    """A destination is not on the approved list; the message must stay secret-free."""


def check_destination(url: str) -> str:
    """Return the normalized ``scheme://host`` when approved; raise :class:`DestinationRefused` otherwise.

    Refused reasons are plain and generic by design — they describe the rule, never
    the URL's contents, so a refused URL cannot leak through error text.
    """
    try:
        parts = urlsplit(url.strip())
    except ValueError as error:
        raise DestinationRefused("That destination is not a valid URL.") from error
    scheme = parts.scheme.lower()
    approved = _approved_hosts()
    if scheme not in approved or not parts.hostname:
        raise DestinationRefused("Only approved HTTPS destinations may be contacted.")
    host = parts.hostname.strip().lower()
    if parts.username or parts.password:
        # userinfo is a classic authority-confusion vector; refuse outright.
        raise DestinationRefused("Only approved HTTPS destinations may be contacted.")
    if "@" in (parts.netloc or ""):
        raise DestinationRefused("Only approved HTTPS destinations may be contacted.")
    if host not in approved[scheme]:
        raise DestinationRefused("Only approved HTTPS destinations may be contacted.")
    return f"{scheme}://{host}"
