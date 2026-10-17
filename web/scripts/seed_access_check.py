"""Disposable data for browser_access_check.py: one superuser, three ordinary people, two empty projects.

SYNTHETIC. Run from web/ with ATHENA_DATA_HOME pointing at a throwaway directory and DJANGO_DEBUG=1:
    ACCESS_CHECK_PWFILE=pw.txt ACCESS_CHECK_SEED=seed.json python scripts/seed_access_check.py
The password is read from the file named by ACCESS_CHECK_PWFILE (create it yourself); it is never printed.
"""
import json
import os
import sys

sys.path.insert(0, os.getcwd())
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "athena_web.settings")

import django  # noqa: E402

django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from django.core.management import call_command  # noqa: E402

from projects.models import Project  # noqa: E402

call_command("migrate", verbosity=0)
password = open(os.environ["ACCESS_CHECK_PWFILE"]).read().strip()
User = get_user_model()
User.objects.create_superuser("admin1", password=password)
for name in ("alice", "bob", "carol"):
    User.objects.create_user(name, password=password)
alpha = Project.objects.create(name="Alpha bags (synthetic)", modality="image", task="t", labels=["x"])
beta = Project.objects.create(name="Beta bags (synthetic)", modality="image", task="t", labels=["y"])
with open(os.environ["ACCESS_CHECK_SEED"], "w") as handle:
    json.dump({"alpha": str(alpha.id), "beta": str(beta.id)}, handle)
