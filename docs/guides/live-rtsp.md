# Live RTSP camera support (T10)
#
# Athena can connect to one approved RTSP/RTSPS camera per camera source, probe
# the stream with ffprobe (TCP transport), and show the installer a single JPEG
# preview frame. Continuous live counting and event production are not part of
# T10.
#
# ## Supported surface
#
# | Item | Support |
# | --- | --- |
# | Schemes | `rtsp://`, `rtsps://` |
# | Video codec | H.264 only |
# | Transport | RTSP over TCP |
# | Max resolution for preview | 1920×1080 |
# | Auth | Password from the source credential (T11). Username may be sent with the preview request. **Do not put userinfo in the URL.** |
#
# ## Network placement
#
# - Cameras must be reachable from the Athena host (same LAN, site VLAN, or
#   controlled VPN). Prefer a dedicated camera VLAN.
# - **Do not** expose RTSP ports to the public internet.
# - Only hosts listed in `ATHENA_APPROVED_RTSP_HOSTS` may be contacted. Matching
#   is exact (no subdomain wildcards). Entries are bare hostnames/IPv4 or
#   `rtsp://host` / `rtsps://host` forms; only the host part is kept.
# - Empty allow-list means every preview is refused with category `not_approved`.
#
# ## Configuration
#
# ```bash
# # Comma-separated hosts the console may dial for live preview
# ATHENA_APPROVED_RTSP_HOSTS=cam-front.site.local,10.20.30.40
# ```
#
# Install `ffmpeg` and `ffprobe` on the host that runs the web process (same
# requirement family as recorded-video import).
#
# ## API
#
# - `GET /api/live-rtsp/support` — codecs, schemes, placement notes (no host list).
# - `POST /api/operation-sources/<id>/live-rtsp/preview` (label role) body:
#   `{"url":"rtsp://host/path","username":"optional","include_preview":true}`
#   Success includes `stream` metadata and optional `preview_jpeg_base64`.
#   Failures return `{ "error": "...", "category": "..." }` where category is one of:
#   `invalid_url`, `not_approved`, `credential`, `network`, `unsupported_codec`,
#   `unavailable`, `source_kind`.
#
# ## Installer UI
#
# On the Operations page, **Live RTSP preview (camera sources)** lets an installer
# pick a camera source, set the password via the existing credential API, enter
# the RTSP URL (no password in the URL), and run Connect & preview.
#
# ## Honest limits
#
# - No continuous live execution, tracking, or counting from RTSP (later tasks).
# - Credentials may appear briefly in the local ffprobe/ffmpeg process argv while
#   a preview runs; they are never written to logs, API responses, or the database
#   in plaintext (T11 vault).
# - Chromium-only assumptions of other surfaces do not apply; this path is API +
#   a simple operations card.
