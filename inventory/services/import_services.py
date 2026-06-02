import re
import threading
import uuid
from pathlib import Path

import openpyxl
from django.conf import settings
from django.db import close_old_connections, transaction
from django.utils import timezone

from inventory.models import Car, CarGroup, ImportBatch, ImportRowError, Part

MAX_IMPORT_ERRORS = 2000


def _batch_size():
    return max(50, int(getattr(settings, 'IMPORT_ROW_BATCH_SIZE', 200)))


def _tmp_dir():
    d = Path(getattr(settings, 'IMPORT_TMP_DIR', settings.BASE_DIR / 'tmp_imports'))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_filename(name):
    base = Path(name or 'upload.xlsx').name
    return re.sub(r'[^a-zA-Z0-9._-]', '_', base)[:180] or 'upload.xlsx'


def _group_column_pairs():
    count = max(1, int(getattr(settings, 'IMPORT_GROUPS_COLUMN_PAIRS', 3)))
    return [(3 * index, 3 * index + 1) for index in range(count)]


def _parts_column_sets():
    count = max(1, int(getattr(settings, 'IMPORT_PARTS_COLUMN_BLOCKS', 6)))
    return [(4 * index, 4 * index + 1, 4 * index + 2) for index in range(count)]


class ExcelImportService:
    cars_sheet_aliases = ['cars', 'carid']
    groups_sheet_aliases = ['groups', 'carid & groupid']
    parts_sheet_aliases = ['parts', 'groupid & part_number']

    cars_headers_map = {
        'car_id': ['carid', 'car_id'],
        'car_model': ['car model', 'model'],
        'steering': ['steering'],
        'transmission': ['transmission'],
        'wd': ['wd'],
        'engine': ['engine'],
        'car_parameters': ['car parameters', 'parameters'],
        'additional_note': ['additional note', 'addintional note', 'note', 'additional_note'],
    }

    def __init__(self):
        self.errors = []

    @staticmethod
    def enqueue(uploaded_file, user, import_type):
        if import_type not in dict(ImportBatch.IMPORT_TYPE_CHOICES):
            raise ValueError('Unsupported import type.')

        batch = ImportBatch.objects.create(
            uploaded_by=user,
            original_file_name=uploaded_file.name,
            import_type=import_type,
            status=ImportBatch.STATUS_PENDING,
        )
        safe = _safe_filename(uploaded_file.name)
        dest = _tmp_dir() / f'{batch.id}_{uuid.uuid4().hex[:12]}_{safe}'
        with open(dest, 'wb') as out:
            for chunk in uploaded_file.chunks():
                out.write(chunk)
        batch.stored_file_path = str(dest)
        batch.save(update_fields=['stored_file_path', 'updated_at'])
        batch.status = ImportBatch.STATUS_PROCESSING
        batch.progress_note = 'Queued'
        batch.save(update_fields=['status', 'progress_note', 'updated_at'])

        def worker():
            close_old_connections()
            try:
                ExcelImportService.execute_batch(batch.id)
            except Exception as exc:
                ImportBatch.objects.filter(pk=batch.id).update(
                    status=ImportBatch.STATUS_FAILED,
                    failure_reason=str(exc),
                    completed_at=timezone.now(),
                )
            finally:
                close_old_connections()

        thread = threading.Thread(target=worker, name=f'import-{batch.id}', daemon=True)
        thread.start()
        return batch.id

    @staticmethod
    def execute_batch(batch_id):
        batch = None
        path = None
        workbook = None
        try:
            batch = ImportBatch.objects.get(pk=batch_id)
            path = batch.stored_file_path
            if not path or not Path(path).is_file():
                batch.status = ImportBatch.STATUS_FAILED
                batch.failure_reason = 'Import file missing on server.'
                batch.completed_at = timezone.now()
                batch.save(update_fields=['status', 'failure_reason', 'completed_at', 'updated_at'])
                return

            service = ExcelImportService()
            service._update_progress(batch, 'Loading workbook (this may take a while for large files)…')
            workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
            sheet_map = {name.lower().strip(): name for name in workbook.sheetnames}
            batch_size = _batch_size()

            if batch.import_type == ImportBatch.TYPE_CARS:
                service._run_cars_import(batch, workbook, sheet_map, batch_size)
            elif batch.import_type == ImportBatch.TYPE_GROUPS:
                service._run_groups_import(batch, workbook, sheet_map, batch_size)
            elif batch.import_type == ImportBatch.TYPE_PARTS:
                service._run_parts_import(batch, workbook, sheet_map, batch_size)
            else:
                service._add_error('Workbook', 0, 'Unsupported import type.')
                service._finalize_failed(batch)
        except ImportBatch.DoesNotExist:
            return
        finally:
            if workbook is not None:
                try:
                    workbook.close()
                except Exception:
                    pass
            if path:
                try:
                    Path(path).unlink(missing_ok=True)
                except OSError:
                    pass
            if batch is not None:
                ImportBatch.objects.filter(pk=batch.id).update(stored_file_path='')

    def _run_cars_import(self, batch, workbook, sheet_map, batch_size):
        sheet_name = self._resolve_sheet(sheet_map, self.cars_sheet_aliases, workbook)
        if not sheet_name:
            self._add_error('Workbook', 0, 'No worksheet found in the uploaded file.')
            self._finalize_failed(batch)
            return

        self._update_progress(batch, 'Parsing car models…')
        parsed = self._parse_cars_sheet(workbook[sheet_name], sheet_name)
        if self.errors:
            self._finalize_failed(batch)
            return

        self._update_progress(batch, 'Saving cars…')
        self._upsert_cars_batched(parsed['cars'], batch, batch_size)
        self._finalize_completed(
            batch,
            total_rows=parsed['total_rows'],
            cars_count=len(parsed['cars']),
            groups_count=0,
            links_count=0,
            parts_count=0,
        )

    def _run_groups_import(self, batch, workbook, sheet_map, batch_size):
        sheet_name = self._resolve_sheet(sheet_map, self.groups_sheet_aliases, workbook)
        if not sheet_name:
            self._add_error('Workbook', 0, 'No worksheet found in the uploaded file.')
            self._finalize_failed(batch)
            return

        self._update_progress(batch, 'Parsing groups and links…')
        parsed = self._parse_groups_sheet(workbook[sheet_name], sheet_name)
        if self.errors:
            self._finalize_failed(batch)
            return

        self._update_progress(batch, 'Saving car–group links…')
        links_created = self._upsert_car_groups_batched(parsed['car_group_links'], batch, batch_size)
        if parsed['car_group_links'] and links_created == 0:
            self._add_error(
                'Groups',
                0,
                'No car-group links were saved. Import car models before groups and links, then run the groups import again.',
            )
            self._finalize_failed(batch)
            return
        self._finalize_completed(
            batch,
            total_rows=parsed['total_rows'],
            cars_count=0,
            groups_count=len(parsed['group_ids']),
            links_count=links_created,
            parts_count=0,
        )

    def _run_parts_import(self, batch, workbook, sheet_map, batch_size):
        sheet_name = self._resolve_sheet(sheet_map, self.parts_sheet_aliases, workbook)
        if not sheet_name:
            self._add_error('Workbook', 0, 'No worksheet found in the uploaded file.')
            self._finalize_failed(batch)
            return

        self._update_progress(batch, 'Parsing part numbers…')
        parsed = self._parse_parts_sheet(workbook[sheet_name], sheet_name)
        if self.errors:
            self._finalize_failed(batch)
            return

        self._update_progress(batch, 'Saving parts…')
        self._upsert_parts_batched(parsed['parts'], batch, batch_size)
        self._finalize_completed(
            batch,
            total_rows=parsed['total_rows'],
            cars_count=0,
            groups_count=len(parsed['group_ids']),
            links_count=0,
            parts_count=len(parsed['parts']),
        )

    def _update_progress(self, batch, note):
        ImportBatch.objects.filter(pk=batch.id).update(progress_note=note[:255], updated_at=timezone.now())
        batch.progress_note = note[:255]

    def _resolve_sheet(self, sheet_map, aliases, workbook=None):
        for alias in aliases:
            key = alias.lower().strip()
            if key in sheet_map:
                return sheet_map[key]
        if workbook is not None and workbook.sheetnames:
            return workbook.sheetnames[0]
        return None

    def _sanitize_text(self, value):
        text = str(value or '').strip()
        if re.search(r'<\s*script', text, re.IGNORECASE):
            return None
        return text

    def _parse_cars_sheet(self, cars_sheet, sheet_name):
        cars = []
        total_rows = 0
        header_row = next(cars_sheet.iter_rows(min_row=1, max_row=1, values_only=True), ())
        header_values = [str(cell or '').strip().lower() for cell in header_row]
        col_map = {}
        for key, aliases in self.cars_headers_map.items():
            for alias in aliases:
                if alias in header_values:
                    col_map[key] = header_values.index(alias)
                    break
            if key not in col_map:
                self._add_error(sheet_name, 1, f'Missing header for "{key}" (tried {aliases})')

        if 'car_id' not in col_map:
            return {'cars': [], 'total_rows': 0}

        for row_number, row in enumerate(cars_sheet.iter_rows(min_row=2, values_only=True), start=2):
            def get_val(key):
                idx = col_map.get(key)
                if idx is not None and idx < len(row):
                    return self._sanitize_text(row[idx])
                return ''

            car_id = get_val('car_id')
            if not car_id:
                if any(row):
                    self._add_error(sheet_name, row_number, 'car_id is required.')
                continue

            row_map = {
                'car_model': get_val('car_model'),
                'steering': get_val('steering'),
                'transmission': get_val('transmission'),
                'wd': get_val('wd'),
                'engine': get_val('engine'),
                'car_parameters': get_val('car_parameters'),
                'additional_note': get_val('additional_note'),
            }
            if any(value is None for value in row_map.values()):
                self._add_error(sheet_name, row_number, 'Script tags are not allowed.')
                continue
            cars.append({
                'car_id': car_id,
                **row_map,
                'metadata_json': {k: v for k, v in row_map.items() if v},
            })
            total_rows += 1

        return {'cars': cars, 'total_rows': total_rows}

    def _parse_groups_sheet(self, groups_sheet, sheet_name):
        group_ids = set()
        car_group_links = set()
        total_rows = 0
        for row_number, row in enumerate(groups_sheet.iter_rows(min_row=2, values_only=True), start=2):
            for col_pair in _group_column_pairs():
                car_id = self._sanitize_text(row[col_pair[0]] if len(row) > col_pair[0] else '')
                group_id = self._sanitize_text(row[col_pair[1]] if len(row) > col_pair[1] else '')
                if not car_id and not group_id:
                    continue
                if not car_id or not group_id:
                    self._add_error(sheet_name, row_number, 'Both car_id and group_id are required in Groups sheet.')
                    continue
                group_ids.add(group_id)
                car_group_links.add((car_id, group_id))
                total_rows += 1

        return {
            'group_ids': group_ids,
            'car_group_links': car_group_links,
            'total_rows': total_rows,
        }

    def _parse_parts_sheet(self, parts_sheet, sheet_name):
        group_ids = set()
        parts = set()
        total_rows = 0
        for row_number, row in enumerate(parts_sheet.iter_rows(min_row=2, values_only=True), start=2):
            for col_set in _parts_column_sets():
                group_id = self._sanitize_text(row[col_set[0]] if len(row) > col_set[0] else '')
                brand = self._sanitize_text(row[col_set[1]] if len(row) > col_set[1] else '')
                part_number = self._sanitize_text(row[col_set[2]] if len(row) > col_set[2] else '')
                if not group_id and not part_number:
                    continue
                if not group_id or not part_number:
                    self._add_error(sheet_name, row_number, 'Both group_id and part_number are required in Parts sheet.')
                    continue
                if brand is None or part_number is None:
                    self._add_error(sheet_name, row_number, 'Script tags are not allowed.')
                    continue
                parts.add((group_id, brand or '', part_number))
                group_ids.add(group_id)
                total_rows += 1

        return {
            'group_ids': group_ids,
            'parts': parts,
            'total_rows': total_rows,
        }

    def _upsert_cars_batched(self, cars, batch, bs):
        total = len(cars)
        for start in range(0, total, bs):
            chunk = cars[start:start + bs]
            self._update_progress(batch, f'Cars {min(start + bs, total)}/{total}')
            car_ids = [row['car_id'] for row in chunk]
            existing_map = {item.car_id: item for item in Car.objects.filter(car_id__in=car_ids)}
            to_create = []
            to_update = []
            for row in chunk:
                obj = existing_map.get(row['car_id'])
                if obj:
                    obj.car_model = row['car_model']
                    obj.steering = row['steering']
                    obj.transmission = row['transmission']
                    obj.wd = row['wd']
                    obj.engine = row['engine']
                    obj.car_parameters = row['car_parameters']
                    obj.additional_note = row['additional_note']
                    obj.metadata_json = row['metadata_json']
                    to_update.append(obj)
                else:
                    to_create.append(Car(
                        car_id=row['car_id'],
                        car_model=row['car_model'],
                        steering=row['steering'],
                        transmission=row['transmission'],
                        wd=row['wd'],
                        engine=row['engine'],
                        car_parameters=row['car_parameters'],
                        additional_note=row['additional_note'],
                        metadata_json=row['metadata_json'],
                    ))
            with transaction.atomic():
                if to_create:
                    Car.objects.bulk_create(to_create, batch_size=bs)
                if to_update:
                    Car.objects.bulk_update(
                        to_update,
                        ['car_model', 'steering', 'transmission', 'wd', 'engine', 'car_parameters', 'additional_note', 'metadata_json'],
                        batch_size=bs,
                    )
        return total

    def _upsert_car_groups_batched(self, links, batch, bs):
        car_lookup = dict(Car.objects.values_list('car_id', 'id'))
        links_list = list(links)
        total_pairs = len(links_list)
        links_created = 0
        for start in range(0, total_pairs, bs):
            chunk = links_list[start:start + bs]
            self._update_progress(batch, f'Links {min(start + bs, total_pairs)}/{total_pairs}')
            objects = []
            for car_id, group_id in chunk:
                car_pk = car_lookup.get(car_id)
                if not car_pk:
                    self._add_error('Groups', 0, f'Unknown relation {car_id} -> {group_id}')
                    continue
                objects.append(CarGroup(car_id=car_pk, group_id=group_id))
            if not objects:
                continue
            with transaction.atomic():
                CarGroup.objects.bulk_create(objects, batch_size=bs, ignore_conflicts=True)
            links_created += len(objects)
        return links_created

    def _upsert_parts_batched(self, parts, batch, bs):
        parts_list = list(parts)
        total = len(parts_list)
        for start in range(0, total, bs):
            chunk = parts_list[start:start + bs]
            self._update_progress(batch, f'Parts {min(start + bs, total)}/{total}')
            group_ids = set()
            part_nums = set()
            resolved = []
            for group_id, brand, part_number in chunk:
                group_ids.add(group_id)
                part_nums.add(part_number)
                resolved.append((group_id, brand, part_number))
            if not resolved:
                continue
            existing = set(
                Part.objects.filter(group_id__in=group_ids, part_number__in=part_nums).values_list('group_id', 'part_number')
            )
            to_create = []
            for group_id, brand, part_number in resolved:
                key = (group_id, part_number)
                if key in existing:
                    continue
                existing.add(key)
                to_create.append(Part(group_id=group_id, brand=brand, part_number=part_number))
            if to_create:
                with transaction.atomic():
                    Part.objects.bulk_create(to_create, batch_size=bs)
        return total

    def _add_error(self, sheet_name, row_number, message):
        if len(self.errors) >= MAX_IMPORT_ERRORS:
            return
        self.errors.append({
            'sheet_name': sheet_name,
            'row_number': row_number,
            'message': message,
        })

    def _persist_row_errors(self, batch):
        if not self.errors:
            return
        ImportRowError.objects.bulk_create(
            [
                ImportRowError(
                    batch=batch,
                    sheet_name=item['sheet_name'],
                    row_number=item['row_number'],
                    message=item['message'],
                )
                for item in self.errors[:MAX_IMPORT_ERRORS]
            ],
            batch_size=500,
        )

    def _finalize_completed(self, batch, total_rows, cars_count, groups_count, links_count, parts_count):
        batch.status = ImportBatch.STATUS_COMPLETED
        batch.total_rows = total_rows
        batch.cars_count = cars_count
        batch.groups_count = groups_count
        batch.links_count = links_count
        batch.parts_count = parts_count
        batch.error_count = len(self.errors)
        batch.progress_note = 'Completed'
        batch.completed_at = timezone.now()
        if self.errors:
            batch.failure_reason = f'Completed with {len(self.errors)} skipped row warning(s).'
        else:
            batch.failure_reason = ''
        batch.save(update_fields=[
            'status', 'total_rows', 'cars_count', 'groups_count', 'links_count',
            'parts_count', 'error_count', 'progress_note', 'completed_at', 'failure_reason', 'updated_at',
        ])
        self._persist_row_errors(batch)

    def _finalize_failed(self, batch):
        batch.status = ImportBatch.STATUS_FAILED
        batch.error_count = len(self.errors)
        batch.completed_at = timezone.now()
        if self.errors:
            batch.failure_reason = self.errors[0]['message'][:2000]
        batch.save(update_fields=['status', 'error_count', 'completed_at', 'failure_reason', 'updated_at'])
        self._persist_row_errors(batch)
        path = batch.stored_file_path
        if path:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass
            batch.stored_file_path = ''
            batch.save(update_fields=['stored_file_path', 'updated_at'])
