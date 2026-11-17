"""Retry private recording deletions that could not complete after commit."""
from django.core.management.base import BaseCommand, CommandError

from projects.models import OperationRecordingCleanup
from projects.operations.recordings import try_cleanup


class Command(BaseCommand):
    help = "Retry pending private operation-recording storage deletions."

    def handle(self, *args, **options):
        ids = list(OperationRecordingCleanup.objects.order_by('pk').values_list('pk', flat=True))
        failures = sum(not try_cleanup(pk) for pk in ids)
        self.stdout.write(f"Attempted {len(ids)} recording cleanups; {failures} remain pending.")
        if failures:
            raise CommandError("Some recording cleanups could not be completed.")
