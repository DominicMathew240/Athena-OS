# syntax=docker/dockerfile:1
# Console image:        docker build -t athena-console web
# Auto-label worker:    docker build --build-arg WITH_AUTOLABEL=1 -t athena-console-worker web
FROM python:3.13-slim

ARG WITH_AUTOLABEL=0
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ATHENA_DATA_HOME=/data \
    ATHENA_STATIC_ROOT=/app/staticfiles \
    HF_HOME=/data/models

RUN useradd --system --uid 10001 --create-home athena && mkdir -p /data /app && chown athena /data /app
WORKDIR /app

COPY requirements.txt requirements-autolabel.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
 && if [ "$WITH_AUTOLABEL" = "1" ]; then \
      pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu -r requirements-autolabel.txt; \
    fi

COPY --chown=athena . .
# Run as the service user from here on: collectstatic honours the restrictive upload
# permissions (0600/0700), so files it creates as root would be unreadable at runtime.
USER athena
# Collect static files at build time with throwaway settings; real values arrive at runtime.
RUN DJANGO_SECRET_KEY=build-only-build-only-build-only-build-only-build-only \
    ATHENA_ALLOWED_HOSTS=build.invalid ATHENA_DATA_HOME=/tmp/build \
    python manage.py collectstatic --noinput \
 && rm -rf /tmp/build

VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"
ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["web"]
