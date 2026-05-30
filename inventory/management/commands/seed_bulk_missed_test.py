from pathlib import Path

import openpyxl
from django.conf import settings
from django.core.management.base import BaseCommand

from inventory.models import Part

MISSED_PART_NUMBERS = (
    'TSTMISS0001',
    'TSTMISS0002',
    'TSTMISS0003',
)


class Command(BaseCommand):
    help = (
        'Create a sample bulk-upload Excel file with both matching and missed rows '
        'so you can test "Export missed to Excel".'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--output-dir',
            type=str,
            default='',
            help='Directory for the file (default: <project>/import_test_fixtures)',
        )

    def handle(self, *args, **options):
        output_dir = Path(options['output_dir'] or settings.BASE_DIR / 'import_test_fixtures')
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / 'bulk_missed_test_upload.xlsx'

        found_part = Part.objects.order_by('id').values_list('part_number', flat=True).first()
        if not found_part:
            self.stdout.write(self.style.ERROR(
                'No parts in the database. Import parts first, then run this command again.'
            ))
            return

        rows = [
            ['Cross Brand', 'Cross Code', 'Part Number'],
            ['Kanoya', 'C13X20', found_part],
            ['Kanoya', 'C13X20', MISSED_PART_NUMBERS[0]],
            ['Kanoya', 'C13X20', MISSED_PART_NUMBERS[1]],
            ['Brembo', 'TEST99', MISSED_PART_NUMBERS[2]],
            ['', 'C13X20', found_part],
            ['Kanoya', '', MISSED_PART_NUMBERS[0]],
        ]

        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = 'BulkTest'
        for row in rows:
            sheet.append(row)
        workbook.save(path)

        self.stdout.write(self.style.SUCCESS(f'Created {path}'))
        self.stdout.write('')
        self.stdout.write('Rows in file:')
        self.stdout.write(f'  1 found  — part {found_part} (exists in DB)')
        self.stdout.write(f'  5 missed — parts {", ".join(MISSED_PART_NUMBERS)} not in DB, or missing brand/code')
        self.stdout.write('')
        self.stdout.write('How to test:')
        self.stdout.write('  1. Search Part Number > Bulk search > upload this file')
        self.stdout.write('  2. Summary should show Missed numbers: 5')
        self.stdout.write('  3. Click "Export missed to Excel"')
