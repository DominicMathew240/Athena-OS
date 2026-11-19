from __future__ import annotations

import signal
import subprocess
import sys
import threading
import time

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = (
        "Keep N operation workers alive as child processes (T19 supervision). --workers 1 "
        "(the default) is the ordinary single-worker setup; more than one is safe because "
        "each worker claims jobs through a database lease."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument("--workers", type=int, default=1, help="How many operation workers to keep alive.")
        parser.add_argument("--poll", type=float, default=2.0, help="Seconds between queue checks (passed on).")

    def handle(self, *args, **options) -> None:
        count = options["workers"]
        if count < 1:
            raise CommandError("Run at least one operation worker.")
        stopping = threading.Event()

        def stop(*_: object) -> None:
            stopping.set()

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)

        command = [sys.executable, "manage.py", "operation_worker", "--poll", str(options["poll"])]
        children: list[subprocess.Popen] = []
        self.stdout.write(f"Operation supervisor keeping {count} worker(s) alive.")
        try:
            while not stopping.is_set():
                while len(children) < count:
                    children.append(subprocess.Popen(command))
                for slot, child in enumerate(children):
                    if child.poll() is not None:
                        self.stdout.write(f"Operation worker {slot + 1} exited ({child.returncode}); restarting it.")
                        children[slot] = subprocess.Popen(command)
                time.sleep(5.0)
        finally:
            self.stdout.write("Stopping operation workers...")
            for child in children:
                if child.poll() is None:
                    child.terminate()
            for child in children:
                try:
                    child.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
