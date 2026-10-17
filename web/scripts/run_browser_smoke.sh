#!/usr/bin/env bash
# One command: build a disposable database, start the console on a free loopback port, run the browser smoke test
# (scripts/browser_smoke.mjs), then stop the server and delete everything it made.
#
#   bash web/scripts/run_browser_smoke.sh                 # Chromium, desktop
#   bash web/scripts/run_browser_smoke.sh webkit          # Safari engine
#   bash web/scripts/run_browser_smoke.sh webkit "iPhone 13"
#
# Needs (development tools only, none of them is a dependency of the product):
#   * Python with the console's requirements (pip install -r web/requirements.txt numpy)
#   * Node 18+ and Playwright with a browser:
#         npm install --prefix web/scripts/.smoke playwright
#         npx --prefix web/scripts/.smoke playwright install chromium     # add `webkit` for the Safari engine
#     or point PLAYWRIGHT_NODE_MODULES at an existing node_modules folder that contains `playwright`.
# Optional environment:
#   ATHENA_SMOKE_PYTHON   Python to use (default: python3)
#   ATHENA_SMOKE_KEEP=1   keep the temporary folder and print its path (for debugging a failure)
#   ATHENA_SMOKE_REPORT   write the JSON result to this file
#
# Exit code: 0 all checks passed, 1 a check failed, 2 the test could not run. The server binds 127.0.0.1 only and
# uses DJANGO_DEBUG=1 with throw-away data; nothing here touches your real data folder (ATHENA_DATA_HOME is overridden).
set -euo pipefail

BROWSER="${1:-chromium}"
DEVICE="${2:-}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEB="$(dirname "$HERE")"
PYTHON="${ATHENA_SMOKE_PYTHON:-python3}"

command -v node >/dev/null || { echo "Node is not installed (need Node 18+)." >&2; exit 2; }
command -v "$PYTHON" >/dev/null || { echo "Python not found: $PYTHON (set ATHENA_SMOKE_PYTHON)." >&2; exit 2; }

TMP="$(mktemp -d "${TMPDIR:-/tmp}/athena-smoke.XXXXXX")"
SERVER_PID=""
cleanup() {
  if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  if [ "${ATHENA_SMOKE_KEEP:-0}" = "1" ]; then
    echo "kept: $TMP (server log: $TMP/server.log)"
  else
    rm -rf "$TMP"
  fi
}
trap cleanup EXIT INT TERM

# A free loopback port, chosen by the operating system.
PORT="$("$PYTHON" - <<'PY'
import socket
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    print(s.getsockname()[1])
PY
)"

export ATHENA_DATA_HOME="$TMP/data"
export DJANGO_DEBUG=1
export ATHENA_SMOKE_PASSWORD="$("$PYTHON" -c 'import secrets; print(secrets.token_urlsafe(18))')"
unset DJANGO_SECRET_KEY ATHENA_DB_PATH ATHENA_MEDIA_ROOT ATHENA_SECURE_SSL
mkdir -p "$ATHENA_DATA_HOME"

echo "== seeding a disposable database in $TMP (port $PORT)"
SEED_JSON="$(cd "$WEB" && "$PYTHON" scripts/seed_browser_smoke.py)"
PROJECT_ID="$("$PYTHON" -c 'import json,sys; print(json.loads(sys.argv[1])["project_id"])' "$SEED_JSON")"
SLUG="$("$PYTHON" -c 'import json,sys; print(json.loads(sys.argv[1])["slug"])' "$SEED_JSON")"

echo "== starting the console on http://127.0.0.1:$PORT"
(cd "$WEB" && exec "$PYTHON" manage.py runserver "127.0.0.1:$PORT" --noreload) >"$TMP/server.log" 2>&1 &
SERVER_PID=$!
for _ in $(seq 1 60); do
  if curl -fsS -o /dev/null "http://127.0.0.1:$PORT/login/" 2>/dev/null; then break; fi
  kill -0 "$SERVER_PID" 2>/dev/null || { echo "The server stopped at start-up:" >&2; tail -20 "$TMP/server.log" >&2; exit 2; }
  sleep 0.5
done
curl -fsS -o /dev/null "http://127.0.0.1:$PORT/login/" || { echo "The server did not answer in 30 s." >&2; tail -20 "$TMP/server.log" >&2; exit 2; }

echo "== running the browser smoke test ($BROWSER${DEVICE:+, $DEVICE})"
ARGS=(--base "http://127.0.0.1:$PORT" --project-id "$PROJECT_ID" --slug "$SLUG" --browser "$BROWSER")
[ -n "$DEVICE" ] && ARGS+=(--device "$DEVICE")
[ -n "${ATHENA_SMOKE_REPORT:-}" ] && ARGS+=(--report "$ATHENA_SMOKE_REPORT")
set +e
node "$HERE/browser_smoke.mjs" "${ARGS[@]}"
STATUS=$?
set -e
if [ "$STATUS" -ne 0 ]; then
  echo "== the smoke test failed (exit $STATUS). Last server log lines:"
  tail -5 "$TMP/server.log" || true
fi
exit "$STATUS"
