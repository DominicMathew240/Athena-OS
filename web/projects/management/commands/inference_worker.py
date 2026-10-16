from __future__ import annotations

import signal
import threading
import time

from django.core.management.base import BaseCommand

from projects.inference.runner import (
    claim_next_observation,
    fail_orphaned_observations,
    fail_stale_observations,
    process_observation,
)
from projects.training import backend_classes
from projects.training.lifecycle import INFERENCE_WORKER_KEY, record_worker_heartbeat


class Command(BaseCommand):
    help = "Run queued inference-app images through their approved models. One worker per deployment."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--once", action="store_true", help="Process the queue once, then exit.")
        parser.add_argument("--poll", type=float, default=1.0, help="Seconds between queue checks.")

    def handle(self, *args, **options) -> None:
        stopping = False

        def stop(*_: object) -> None:
            nonlocal stopping
            stopping = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        availability = {name: cls().check_available().ok for name, cls in backend_classes().items()}

        def beat() -> None:
            while not stopping:
                record_worker_heartbeat(INFERENCE_WORKER_KEY, "ready", availability)
                time.sleep(10)

        fail_orphaned_observations()
        record_worker_heartbeat(INFERENCE_WORKER_KEY, "ready", availability)
        threading.Thread(target=beat, daemon=True, name="inference-heartbeat").start()
        self.stdout.write("Inference worker ready.")
        while not stopping:
            fail_stale_observations()
            observation = claim_next_observation()
            if observation is None:
                if options["once"]:
                    return
                time.sleep(options["poll"])
                continue
            done = process_observation(observation)
            self.stdout.write(f"Observation {done.pk}: {done.status}.")
