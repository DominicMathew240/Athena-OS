from __future__ import annotations

import signal
import threading
import time

from django.core.management.base import BaseCommand

from projects.training import backend_classes
from projects.training.runner import claim_next_run, fail_orphaned_runs, fail_stale_runs, heartbeat, run_training


class Command(BaseCommand):
    help = "Process queued training runs. Run exactly one training worker per deployment."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--once", action="store_true", help="Process the queue once, then exit.")
        parser.add_argument("--poll", type=float, default=2.0, help="Seconds between queue checks.")

    def handle(self, *args, **options) -> None:
        stopping = False

        def stop(*_: object) -> None:
            nonlocal stopping
            stopping = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)

        availability = {name: cls().check_available().ok for name, cls in backend_classes().items()}
        state = "loading"

        def beat() -> None:
            while not stopping:
                heartbeat(state, availability)
                time.sleep(10)

        failed = fail_orphaned_runs()
        if failed:
            self.stdout.write(f"Marked {failed} run(s) left running by a previous worker as failed.")
        threading.Thread(target=beat, daemon=True, name="train-heartbeat").start()
        state = "ready"
        heartbeat(state, availability)
        usable = [name for name, ok in availability.items() if ok]
        self.stdout.write(f"Training worker ready. Backends usable here: {', '.join(usable) or 'none'}.")
        while not stopping:
            fail_stale_runs()
            run = claim_next_run()
            if run is None:
                if options["once"]:
                    return
                time.sleep(options["poll"])
                continue
            finished = run_training(run)
            self.stdout.write(f"Run {finished.pk}: {finished.status}.")
