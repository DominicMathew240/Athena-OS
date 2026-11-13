# SPDX-License-Identifier: Apache-2.0
"""Run a connector from a JSON file: ``python -m athena_sdk.connectors.run --config connector.json``.

Prints one JSON object per line on stdout (events and a periodic ``health`` line); logs go to
stderr. SIGTERM / SIGINT stop polling, deliver what is queued (up to ``drain_timeout``), hand the
rest back to the source and exit 0. Exit status 2 means the configuration was unusable.

Credentials are read from the environment only; there is deliberately no password option.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from athena_sdk.connectors.config import ConfigError, build_pipeline, load_config

_print_lock = threading.Lock()


def emit(record: dict[str, Any]) -> None:
    line = json.dumps(record, sort_keys=True, default=str)
    with _print_lock:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def _write_health_file(path: Path, health: dict[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(health, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m athena_sdk.connectors.run", description=__doc__.splitlines()[0])
    parser.add_argument("--config", required=True, help="path to connector.json")
    parser.add_argument("--validate-only", action="store_true", help="check the config and environment, then exit")
    parser.add_argument("--log-level", default="WARNING", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args(argv)
    logging.basicConfig(level=args.log_level, stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    try:
        config, base_dir = load_config(args.config)
        pipeline, ledger = build_pipeline(config, base_dir, on_event=emit)
    except ConfigError as error:
        emit({"event": "config_error", "errors": error.errors})
        return 2
    if args.validate_only:
        ledger.close()
        emit({"event": "config_ok", "connector": config.connector.type, "sink": config.sink.type})
        return 0

    stop = threading.Event()

    def request_stop(signum: int, _frame: object) -> None:
        emit({"event": "stop_requested", "signal": signal.Signals(signum).name})
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    health_file = Path(config.pipeline.health_file).expanduser() if config.pipeline.health_file else None
    if health_file and not health_file.is_absolute():
        health_file = base_dir / health_file

    def report() -> None:
        health = pipeline.health()
        emit({"event": "health", **health})
        if health_file:
            try:
                _write_health_file(health_file, health)
            except OSError as error:
                emit({"event": "health_file_error", "error": str(error)})

    try:
        pipeline.start()
    except Exception as error:
        emit({"event": "start_failed", "error": str(error)})
        ledger.close()
        return 3
    try:
        report()
        while not stop.wait(config.pipeline.health_interval):
            report()
    finally:
        pipeline.stop(drain=True)
        report()
        ledger.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
