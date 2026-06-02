"""
One-time management command to sync Part and CarGroup data from an
external PostgreSQL database into the local database.

Usage:
    python manage.py sync_from_external_db

Before running, set the external DB credentials via environment variables
or in settings.py  →  DATABASES['external']:

    EXT_DB_NAME, EXT_DB_USER, EXT_DB_PASSWORD, EXT_DB_HOST, EXT_DB_PORT
"""

import time

from django.core.management.base import BaseCommand
from django.db import connections

from inventory.models import Car, CarGroup, Part


BATCH_SIZE = 5000  # rows per bulk_create batch


class Command(BaseCommand):
    help = (
        'One-time sync: fetch Part and CarGroup rows from the external '
        'database and insert them into the local database.'
    )

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _fetch_all(self, cursor):
        """Return all rows from cursor as a list of dicts."""
        columns = [col[0] for col in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def _log(self, msg, style=None):
        if style:
            self.stdout.write(style(msg))
        else:
            self.stdout.write(msg)

    # ------------------------------------------------------------------
    # sync logic
    # ------------------------------------------------------------------
    def _sync_car_groups(self, ext_cursor):
        """Fetch car_groups from external DB and insert into local DB."""
        self._log('\n-- Syncing CarGroup table --------------------------')

        ext_cursor.execute('SELECT car_id, group_id FROM car_groups LIMIT 100')
        rows = self._fetch_all(ext_cursor)
        total = len(rows)
        self._log(f'  Fetched {total:,} rows from external car_groups table.')

        if total == 0:
            self._log('  Nothing to import.', self.style.WARNING)
            return

        created = 0
        objects_buffer = []

        for row in rows:
            group_id = row.get('group_id', '')
            ext_car_id = str(row.get('car_id', ''))
            if not ext_car_id:
                continue
            objects_buffer.append(CarGroup(car_id=ext_car_id, group_id=group_id))

            if len(objects_buffer) >= BATCH_SIZE:
                CarGroup.objects.bulk_create(
                    objects_buffer, batch_size=BATCH_SIZE, ignore_conflicts=True
                )
                created += len(objects_buffer)
                self._log(f'    ... inserted batch ({created:,}/{total:,})')
                objects_buffer = []

        # flush remaining
        if objects_buffer:
            CarGroup.objects.bulk_create(
                objects_buffer, batch_size=BATCH_SIZE, ignore_conflicts=True
            )
            created += len(objects_buffer)

        self._log(
            f'  [OK] CarGroup sync complete: {created:,} inserted.',
            self.style.SUCCESS,
        )

    def _sync_parts(self, ext_cursor):
        """Fetch parts from external DB and insert into local DB."""
        self._log('\n-- Syncing Part table ------------------------------')

        ext_cursor.execute('SELECT group_id, brand_id AS brand, code AS part_number FROM parts LIMIT 100')
        rows = self._fetch_all(ext_cursor)
        total = len(rows)
        self._log(f'  Fetched {total:,} rows from external parts table.')

        if total == 0:
            self._log('  Nothing to import.', self.style.WARNING)
            return

        created = 0
        objects_buffer = []

        for row in rows:
            group_id = row.get('group_id', '')
            brand = row.get('brand', '')
            part_number = row.get('part_number', '')

            if not part_number:
                continue

            objects_buffer.append(
                Part(group_id=group_id, brand=brand, part_number=part_number)
            )

            if len(objects_buffer) >= BATCH_SIZE:
                Part.objects.bulk_create(
                    objects_buffer, batch_size=BATCH_SIZE, ignore_conflicts=True
                )
                created += len(objects_buffer)
                self._log(f'    … inserted batch ({created:,}/{total:,})')
                objects_buffer = []

        # flush remaining
        if objects_buffer:
            Part.objects.bulk_create(
                objects_buffer, batch_size=BATCH_SIZE, ignore_conflicts=True
            )
            created += len(objects_buffer)

        self._log(
            f'  [OK] Part sync complete: {created:,} inserted.',
            self.style.SUCCESS,
        )

    # ------------------------------------------------------------------
    # entry point
    # ------------------------------------------------------------------
    def handle(self, *args, **options):
        self._log(self.style.HTTP_INFO(
            '===================================================\n'
            '  One-time sync from external database\n'
            '==================================================='
        ))

        t0 = time.perf_counter()

        try:
            ext_conn = connections['external']
            ext_cursor = ext_conn.cursor()
            self._log('  [OK] Connected to external database.', self.style.SUCCESS)
        except Exception as exc:
            self._log(
                f'  [ERROR] Could not connect to external database: {exc}',
                self.style.ERROR,
            )
            self._log(
                '  Hint: set EXT_DB_NAME, EXT_DB_USER, EXT_DB_PASSWORD, '
                'EXT_DB_HOST, EXT_DB_PORT env vars or update '
                "DATABASES['external'] in settings.py.",
                self.style.WARNING,
            )
            return

        try:
            self._sync_parts(ext_cursor)
            self._sync_car_groups(ext_cursor)
        finally:
            ext_cursor.close()
            ext_conn.close()

        elapsed = time.perf_counter() - t0
        self._log(self.style.HTTP_INFO(
            f'\n===================================================\n'
            f'  Done in {elapsed:.1f}s\n'
            f'==================================================='
        ))
