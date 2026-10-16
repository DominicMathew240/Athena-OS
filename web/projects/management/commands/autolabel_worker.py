from __future__ import annotations

import os
import signal
import threading
import time

from django.conf import settings
from django.core.management.base import BaseCommand

from projects.autolabel import get_autolabeler, is_enabled
from projects.autolabel.runner import (
    WORKER_LOADING,
    WORKER_READY,
    claim_next_job,
    fail_stale_jobs,
    record_heartbeat,
    run_job,
)


class Command(BaseCommand):
    help = "Process queued auto-label jobs (SAM + CLIP). Run one worker per deployment."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--once", action="store_true", help="Process the queue once, then exit.")
        parser.add_argument("--poll", type=float, default=2.0, help="Seconds between queue checks.")

    def handle(self, *args, **options) -> None:
        if not is_enabled():
            self.stderr.write("Auto-labelling is disabled (ATHENA_AUTOLABEL_BACKEND).")
            return
        stopping = False

        def stop(*_: object) -> None:
            nonlocal stopping
            stopping = True

        if not settings.AUTOLABEL["ALLOW_DOWNLOAD"]:
            os.environ["HF_HUB_OFFLINE"] = "1"
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        labeler = get_autolabeler()

        state = WORKER_LOADING

        def beat() -> None:
            while not stopping:
                record_heartbeat(state)
                time.sleep(10)

        threading.Thread(target=beat, daemon=True, name="autolabel-heartbeat").start()
        warm_up = getattr(labeler, "warm_up", None)
        if warm_up:
            self.stdout.write("Loading models…")
            warm_up()
        state = WORKER_READY
        record_heartbeat(state)
        self.stdout.write(f"Auto-label worker ready ({labeler.name}).")
        while not stopping:
            fail_stale_jobs()
            job = claim_next_job()
            if job is None:
                if options["once"]:
                    return
                time.sleep(options["poll"])
                continue
            finished = run_job(job, labeler)
            self.stdout.write(
                f"Job {finished.pk}: {finished.status}, {finished.boxes_proposed} boxes "
                f"across {finished.processed_assets}/{finished.total_assets} images."
            )
