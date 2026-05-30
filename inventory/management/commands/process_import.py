from django.core.management.base import BaseCommand
from django.db import close_old_connections

from inventory.services.import_services import ExcelImportService


class Command(BaseCommand):
    help = 'Run a queued Excel import batch by ID (same logic as the background worker).'

    def add_arguments(self, parser):
        parser.add_argument('batch_id', type=int)

    def handle(self, *args, **options):
        close_old_connections()
        try:
            ExcelImportService.execute_batch(options['batch_id'])
        finally:
            close_old_connections()
