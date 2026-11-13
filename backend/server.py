"""Preview entry point: serves the Athena Django console (web/) through uvicorn on port 8001."""

import os
import sys
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT.parent / "web"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "athena_web.settings")

app_host = urlparse(os.environ.get("APP_URL", "")).hostname
if app_host:
    hosts = [h for h in os.environ.get("ATHENA_ALLOWED_HOSTS", "").split(",") if h.strip()]
    if app_host not in hosts:
        os.environ["ATHENA_ALLOWED_HOSTS"] = ",".join([*hosts, app_host])
    origins = [o for o in os.environ.get("ATHENA_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]
    if f"https://{app_host}" not in origins:
        os.environ["ATHENA_CSRF_TRUSTED_ORIGINS"] = ",".join([*origins, f"https://{app_host}"])

import threading  # noqa: E402

from django.core.asgi import get_asgi_application  # noqa: E402

django_app = get_asgi_application()

from django.conf import settings  # noqa: E402
from django.core.management import call_command  # noqa: E402

Path(settings.DATA_HOME).mkdir(parents=True, exist_ok=True)
# uvicorn imports this module inside its event loop, and Django's ORM refuses to run there.
_migrate = threading.Thread(target=call_command, args=("migrate",), kwargs={"interactive": False, "verbosity": 0})
_migrate.start()
_migrate.join()

if settings.DEBUG:
    from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler

    app = ASGIStaticFilesHandler(django_app)
else:
    app = django_app
