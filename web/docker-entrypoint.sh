#!/bin/sh
# Container entrypoint: the "web" role applies migrations first; any other command runs as given.
set -eu
if [ "${1:-}" = "web" ]; then
  python manage.py migrate --noinput
  exec python -m gunicorn athena_web.wsgi:application -c gunicorn.conf.py
fi
exec "$@"
