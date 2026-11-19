from __future__ import annotations

import signal
import threading

from django.core.management.base import BaseCommand

from projects.operations.executions import worker_key


class Command(BaseCommand):
    help = (
        "Run queued operation executions. Safe to run several: each worker claims jobs "
        "through a database lease, and a crashed worker's job is recovered once its lease expires."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument("--once", action="store_true", help="Process the queue once, then exit.")
        parser.add_argument("--poll", type=float, default=2.0, help="Seconds between queue checks.")

    def handle(self, *args, **options) -> None:
        from projects.operations.executions import claim_next_execution, fail_abandoned_executions, run_execution

        stopping = threading.Event()
        for received in (signal.SIGTERM, signal.SIGINT):
            signal.signal(received, lambda *_: stopping.set())

        owner = worker_key()
        self.stdout.write(f"Operation worker ready ({owner}).")
        while not stopping.is_set():
            fail_abandoned_executions()
            execution = claim_next_execution(owner)
            if execution is None:
                if options["once"]:
                    return
                stopping.wait(options["poll"])
                continue
            finished = run_execution(execution, owner)
            self.stdout.write(f"Execution {finished.pk}: {finished.status}.")
            if options["once"]:
                return
