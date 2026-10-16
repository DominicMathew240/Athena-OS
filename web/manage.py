from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "athena_web.settings")
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "test":
        # The test suite runs with development settings (no TLS redirect, testserver host).
        os.environ.setdefault("DJANGO_DEBUG", "1")
        os.environ.setdefault("ATHENA_LOG_LEVEL", "CRITICAL")
    else:
        data_home = Path(os.environ.get("ATHENA_DATA_HOME", Path.home() / ".athena-os"))
        data_home.mkdir(parents=True, exist_ok=True, mode=0o700)
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
