import re
import threading
import uuid
from pathlib import Path

from django.conf import settings
from django.db import close_old_connections, transaction
from django.utils import timezone

from inventory.models import (
    Car, CarCrossCode, CarGroup, CarGroupCrossCode, ImportBatch, ImportRowError,
    Part, PartCrossCode,
)
from inventory.services.spreadsheet_loader import load_workbook

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
    cars_sheet_aliases = ['cars', 'carid', 'csv']
    groups_sheet_aliases = ['groups', 'carid & groupid', 'csv']
    parts_sheet_aliases = ['parts', 'groupid & part_number', 'csv']
    crosscode_sheet_aliases = [
        'cross code', 'crosscode', 'cross code example', 'product brand', 'csv',
    ]
    car_with_parts_sheet_aliases = ['car with parts', 'car_with_parts', 'cars and parts', 'cars & parts', 'csv']

    # Cars added via the "Car with parts" flow don't come with real group
    # data, so each car gets one dedicated group (keyed off its own pk) to
    # hang its parts on. Re-running the import for the same car reuses this
    # group instead of creating a new one each time.
    CAR_WITH_PARTS_GROUP_PREFIX = 'CWP'

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
            workbook = load_workbook(path)
            sheet_map = {name.lower().strip(): name for name in workbook.sheetnames}
            batch_size = _batch_size()

            if batch.import_type == ImportBatch.TYPE_CARS:
                service._run_cars_import(batch, workbook, sheet_map, batch_size)
            elif batch.import_type == ImportBatch.TYPE_GROUPS:
                service._run_groups_import(batch, workbook, sheet_map, batch_size)
            elif batch.import_type == ImportBatch.TYPE_PARTS:
                service._run_parts_import(batch, workbook, sheet_map, batch_size)
            elif batch.import_type == ImportBatch.TYPE_CAR_WITH_PARTS:
                service._run_car_with_parts_import(batch, workbook, sheet_map, batch_size)
            elif batch.import_type == ImportBatch.TYPE_CARS_CROSSCODE:
                service._run_cars_crosscode_import(batch, workbook, sheet_map, batch_size)
            elif batch.import_type == ImportBatch.TYPE_GROUPS_CROSSCODE:
                service._run_groups_crosscode_import(batch, workbook, sheet_map, batch_size)
            elif batch.import_type == ImportBatch.TYPE_PARTS_CROSSCODE:
                service._run_parts_crosscode_import(batch, workbook, sheet_map, batch_size)
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

    def _run_cars_crosscode_import(self, batch, workbook, sheet_map, batch_size):
        sheet_name = self._resolve_sheet(sheet_map, self.cars_sheet_aliases, workbook)
        if not sheet_name:
            self._add_error('Workbook', 0, 'No worksheet found in the uploaded file.')
            self._finalize_failed(batch)
            return

        self._update_progress(batch, 'Parsing Cross Code car models…')
        parsed = self._parse_cars_sheet(workbook[sheet_name], sheet_name)
        if self.errors:
            self._finalize_failed(batch)
            return

        self._update_progress(batch, 'Saving Cross Code cars…')
        self._upsert_cars_batched(parsed['cars'], batch, batch_size, model=CarCrossCode)
        self._finalize_completed(
            batch,
            total_rows=parsed['total_rows'],
            cars_count=len(parsed['cars']),
            groups_count=0,
            links_count=0,
            parts_count=0,
        )

    def _run_groups_crosscode_import(self, batch, workbook, sheet_map, batch_size):
        sheet_name = self._resolve_sheet(sheet_map, self.groups_sheet_aliases, workbook)
        if not sheet_name:
            self._add_error('Workbook', 0, 'No worksheet found in the uploaded file.')
            self._finalize_failed(batch)
            return

        self._update_progress(batch, 'Parsing Cross Code groups and links…')
        parsed = self._parse_groups_sheet(workbook[sheet_name], sheet_name)
        if self.errors:
            self._finalize_failed(batch)
            return

        self._update_progress(batch, 'Saving Cross Code car–group links…')
        links_created = self._upsert_car_groups_batched(
            parsed['car_group_links'], batch, batch_size,
            car_model=CarCrossCode, group_model=CarGroupCrossCode,
        )
        if parsed['car_group_links'] and links_created == 0:
            self._add_error(
                'Groups',
                0,
                'No car-group links were saved. Import Cross Code car models before groups and links, then run the groups import again.',
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

    def _run_parts_crosscode_import(self, batch, workbook, sheet_map, batch_size):
        sheet_name = self._resolve_sheet(sheet_map, self.crosscode_sheet_aliases, workbook)
        if not sheet_name:
            self._add_error('Workbook', 0, 'No worksheet found in the uploaded file.')
            self._finalize_failed(batch)
            return

        self._update_progress(batch, 'Parsing Cross Code references…')
        parsed = self._parse_crosscode_sheet(workbook[sheet_name], sheet_name)
        if self.errors and not parsed['rows']:
            self._finalize_failed(batch)
            return

        self._update_progress(batch, 'Saving Cross Code references…')
        saved = self._upsert_crosscode_parts_batched(parsed['rows'], batch, batch_size)
        self._finalize_completed(
            batch,
            total_rows=parsed['total_rows'],
            cars_count=0,
            groups_count=len(parsed['product_nos']),
            links_count=0,
            parts_count=saved,
        )

    def _parse_crosscode_sheet(self, sheet, sheet_name):
        """
        Product Brand | Product No | Brand | Code
        Header names are preferred; otherwise columns A–D are used in that order.
        """
        header_aliases = {
            'product_brand': ['product brand', 'productbrand', 'product_brand'],
            'product_no': ['product no', 'productno', 'product_no', 'product number', 'productnumber'],
            'oe_brand': ['brand', 'oe brand', 'oe_brand', 'cross brand'],
            'code': ['code', 'cross code', 'crosscode', 'oe code', 'oe_code', 'part number', 'part_number'],
        }
        indices = {'product_brand': 0, 'product_no': 1, 'oe_brand': 2, 'code': 3}
        first_row = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True), None)
        start_row = 2
        if first_row:
            headers = [str(cell or '').strip().lower() for cell in first_row]
            matched = {}
            for field, aliases in header_aliases.items():
                for idx, header in enumerate(headers):
                    if header in aliases:
                        # Prefer "product brand" over plain "brand"
                        if field == 'oe_brand' and header in ('product brand', 'productbrand', 'product_brand'):
                            continue
                        if field == 'code' and header in ('product no', 'productno', 'product_no', 'product number'):
                            continue
                        matched[field] = idx
                        break
            if 'product_brand' in matched and 'code' in matched:
                indices.update(matched)
            elif any(h in ('product brand', 'product no', 'brand', 'code') for h in headers):
                # Partial header match — still treat row 1 as headers
                indices.update(matched)
            else:
                # No recognizable headers: treat first row as data
                start_row = 1

        rows_by_key = {}
        product_nos = set()
        total_rows = 0
        for row_number, row in enumerate(sheet.iter_rows(min_row=start_row, values_only=True), start=start_row):
            product_brand = self._sanitize_text(row[indices['product_brand']] if len(row) > indices['product_brand'] else '')
            product_no = self._sanitize_text(row[indices['product_no']] if len(row) > indices['product_no'] else '')
            oe_brand = self._sanitize_text(row[indices['oe_brand']] if len(row) > indices['oe_brand'] else '')
            code = self._sanitize_text(row[indices['code']] if len(row) > indices['code'] else '')

            if not any([product_brand, product_no, oe_brand, code]):
                continue
            if not product_brand or not product_no or not code:
                self._add_error(
                    sheet_name, row_number,
                    'Product Brand, Product No, and Code are required.',
                )
                continue
            if None in (product_brand, product_no, oe_brand, code):
                self._add_error(sheet_name, row_number, 'Script tags are not allowed.')
                continue

            key = (product_brand, product_no, oe_brand or '', code)
            rows_by_key[key] = {
                'brand': product_brand,
                'product_no': product_no,
                'oe_brand': oe_brand or '',
                'part_number': code,
                'group_id': product_no,
            }
            product_nos.add(product_no)
            total_rows += 1

        return {
            'rows': list(rows_by_key.values()),
            'product_nos': product_nos,
            'total_rows': total_rows,
        }

    def _upsert_crosscode_parts_batched(self, rows, batch, bs):
        total = len(rows)
        saved = 0
        for start in range(0, total, bs):
            chunk = rows[start:start + bs]
            self._update_progress(batch, f'Cross Code {min(start + bs, total)}/{total}')
            product_nos = {row['product_no'] for row in chunk}
            codes = {row['part_number'] for row in chunk}
            existing = {
                (item.brand, item.product_no, item.oe_brand, item.part_number): item
                for item in PartCrossCode.objects.filter(
                    product_no__in=product_nos,
                    part_number__in=codes,
                )
            }
            to_create = []
            to_claim = []
            for row in chunk:
                key = (row['brand'], row['product_no'], row['oe_brand'], row['part_number'])
                existing_item = existing.get(key)
                if existing_item is not None:
                    # Claim unstamped rows so history delete can undo them.
                    if existing_item.import_batch_id is None:
                        existing_item.import_batch = batch
                        to_claim.append(existing_item)
                    continue
                existing[key] = None
                to_create.append(PartCrossCode(
                    group_id=row['group_id'],
                    brand=row['brand'],
                    product_no=row['product_no'],
                    oe_brand=row['oe_brand'],
                    part_number=row['part_number'],
                    import_batch=batch,
                ))
            if to_claim:
                PartCrossCode.objects.bulk_update(to_claim, ['import_batch'], batch_size=bs)
                saved += len(to_claim)
            if to_create:
                with transaction.atomic():
                    PartCrossCode.objects.bulk_create(to_create, batch_size=bs)
                saved += len(to_create)
        return saved

    def _run_car_with_parts_import(self, batch, workbook, sheet_map, batch_size):
        sheet_name = self._resolve_sheet(sheet_map, self.car_with_parts_sheet_aliases, workbook)
        if not sheet_name:
            self._add_error('Workbook', 0, 'No worksheet found in the uploaded file.')
            self._finalize_failed(batch)
            return

        self._update_progress(batch, 'Parsing cars and parts…')
        parsed = self._parse_car_with_parts_sheet(workbook[sheet_name], sheet_name)
        if self.errors:
            self._finalize_failed(batch)
            return

        if not parsed['rows']:
            self._add_error(
                sheet_name, 0,
                'No valid rows found. Expected column A = car name, column B = part number.',
            )
            self._finalize_failed(batch)
            return

        self._update_progress(batch, 'Saving cars…')
        cars_created, car_lookup = self._upsert_car_with_parts_cars(parsed['car_names'], batch, batch_size)

        self._update_progress(batch, 'Linking cars to their parts group…')
        group_by_car_name = {}
        links_created = self._upsert_car_with_parts_groups(car_lookup, group_by_car_name, batch, batch_size)

        self._update_progress(batch, 'Saving parts…')
        parts_created = self._upsert_car_with_parts_parts(parsed['rows'], group_by_car_name, batch, batch_size)

        self._finalize_completed(
            batch,
            total_rows=parsed['total_rows'],
            cars_count=cars_created,
            groups_count=len(group_by_car_name),
            links_count=links_created,
            parts_count=parts_created,
        )

    def _parse_car_with_parts_sheet(self, sheet, sheet_name):
        rows = []
        seen_pairs = set()
        car_names = set()
        total_rows = 0
        for row_number, row in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
            car_name = self._sanitize_text(row[0] if len(row) > 0 else '')
            part_number = self._sanitize_text(row[1] if len(row) > 1 else '')
            if not car_name and not part_number:
                continue
            if not car_name or not part_number:
                self._add_error(sheet_name, row_number, 'Both car name and part number are required.')
                continue
            if car_name is None or part_number is None:
                self._add_error(sheet_name, row_number, 'Script tags are not allowed.')
                continue
            key = (car_name, part_number)
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            rows.append(key)
            car_names.add(car_name)
            total_rows += 1

        return {'rows': rows, 'car_names': car_names, 'total_rows': total_rows}

    def _upsert_car_with_parts_cars(self, car_names, batch, bs):
        # Duplicate handling: a car name that already exists is reused as-is
        # (no fields overwritten) — only genuinely new car names get created.
        car_names_list = list(car_names)
        existing = set(Car.objects.filter(car_id__in=car_names_list).values_list('car_id', flat=True))
        to_create = [
            Car(car_id=name, car_model=name, import_batch=batch)
            for name in car_names_list if name not in existing
        ]
        for start in range(0, len(to_create), bs):
            Car.objects.bulk_create(to_create[start:start + bs], batch_size=bs, ignore_conflicts=True)
        car_lookup = dict(Car.objects.filter(car_id__in=car_names_list).values_list('car_id', 'id'))
        return len(to_create), car_lookup

    def _upsert_car_with_parts_groups(self, car_lookup, group_by_car_name, batch, bs):
        car_pks = list(car_lookup.values())
        # CarGroup.car_id is a TextField, so values_list comes back as str —
        # compare against str(car_pk), not the raw int, or every link looks
        # "new" and gets recounted (bulk_create's ignore_conflicts keeps the
        # DB correct either way, but links_count would over-report).
        existing_links = set(CarGroup.objects.filter(car_id__in=car_pks).values_list('car_id', 'group_id'))
        to_create = []
        for car_name, car_pk in car_lookup.items():
            group_id = f'{self.CAR_WITH_PARTS_GROUP_PREFIX}{car_pk}'
            group_by_car_name[car_name] = group_id
            if (str(car_pk), group_id) not in existing_links:
                to_create.append(CarGroup(car_id=car_pk, group_id=group_id, import_batch=batch))
        for start in range(0, len(to_create), bs):
            CarGroup.objects.bulk_create(to_create[start:start + bs], batch_size=bs, ignore_conflicts=True)
        return len(to_create)

    def _upsert_car_with_parts_parts(self, rows, group_by_car_name, batch, bs):
        resolved = [
            (group_by_car_name[car_name], part_number)
            for car_name, part_number in rows
            if car_name in group_by_car_name
        ]
        total = len(resolved)
        created_count = 0
        for start in range(0, total, bs):
            chunk = resolved[start:start + bs]
            self._update_progress(batch, f'Parts {min(start + bs, total)}/{total}')
            group_ids = {group_id for group_id, _ in chunk}
            part_nums = {part_number for _, part_number in chunk}
            self._remove_duplicate_parts(Part, group_ids, part_nums)
            existing = set(
                Part.objects.filter(group_id__in=group_ids, part_number__in=part_nums).values_list('group_id', 'part_number')
            )
            to_create = []
            for group_id, part_number in chunk:
                key = (group_id, part_number)
                if key in existing:
                    continue
                existing.add(key)
                to_create.append(Part(group_id=group_id, brand='', part_number=part_number, import_batch=batch))
            if to_create:
                with transaction.atomic():
                    Part.objects.bulk_create(to_create, batch_size=bs)
                created_count += len(to_create)
        return created_count

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

        # Drop duplicate car_id rows in the file (last occurrence wins).
        deduped = {}
        for row in cars:
            deduped[row['car_id']] = row
        cars = list(deduped.values())

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
        # Keep one row per (group_id, part_number); last brand wins.
        parts_by_key = {}
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
                parts_by_key[(group_id, part_number)] = (group_id, brand or '', part_number)
                group_ids.add(group_id)
                total_rows += 1

        return {
            'group_ids': group_ids,
            'parts': set(parts_by_key.values()),
            'total_rows': total_rows,
        }

    def _upsert_cars_batched(self, cars, batch, bs, model=Car):
        total = len(cars)
        for start in range(0, total, bs):
            chunk = cars[start:start + bs]
            self._update_progress(batch, f'Cars {min(start + bs, total)}/{total}')
            car_ids = [row['car_id'] for row in chunk]
            existing_map = {item.car_id: item for item in model.objects.filter(car_id__in=car_ids)}
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
                    to_create.append(model(
                        car_id=row['car_id'],
                        car_model=row['car_model'],
                        steering=row['steering'],
                        transmission=row['transmission'],
                        wd=row['wd'],
                        engine=row['engine'],
                        car_parameters=row['car_parameters'],
                        additional_note=row['additional_note'],
                        metadata_json=row['metadata_json'],
                        import_batch=batch,
                    ))
            with transaction.atomic():
                if to_create:
                    model.objects.bulk_create(to_create, batch_size=bs, ignore_conflicts=True)
                if to_update:
                    model.objects.bulk_update(
                        to_update,
                        ['car_model', 'steering', 'transmission', 'wd', 'engine', 'car_parameters', 'additional_note', 'metadata_json'],
                        batch_size=bs,
                    )
        return total

    def _upsert_car_groups_batched(self, links, batch, bs, car_model=Car, group_model=CarGroup):
        car_lookup = dict(car_model.objects.values_list('car_id', 'id'))
        # Deduplicate file/DB pairs before insert.
        unique_links = []
        seen = set()
        for car_id, group_id in links:
            key = (car_id, group_id)
            if key in seen:
                continue
            seen.add(key)
            unique_links.append(key)

        links_list = unique_links
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
                objects.append(group_model(car_id=car_pk, group_id=group_id, import_batch=batch))
            if not objects:
                continue
            with transaction.atomic():
                group_model.objects.bulk_create(objects, batch_size=bs, ignore_conflicts=True)
            links_created += len(objects)
        return links_created

    def _remove_duplicate_parts(self, model, group_ids, part_nums):
        """Keep one DB row per (group_id, part_number) for keys in this import."""
        if not group_ids or not part_nums:
            return
        from django.db.models import Count, Min

        duplicates = (
            model.objects
            .filter(group_id__in=group_ids, part_number__in=part_nums)
            .values('group_id', 'part_number')
            .annotate(row_count=Count('id'), keep_id=Min('id'))
            .filter(row_count__gt=1)
        )
        for item in duplicates.iterator(chunk_size=500):
            model.objects.filter(
                group_id=item['group_id'],
                part_number=item['part_number'],
            ).exclude(id=item['keep_id']).delete()

    def _upsert_parts_batched(self, parts, batch, bs, model=Part):
        parts_list = list(parts)
        total = len(parts_list)
        saved = 0
        for start in range(0, total, bs):
            chunk = parts_list[start:start + bs]
            self._update_progress(batch, f'Parts {min(start + bs, total)}/{total}')
            group_ids = set()
            part_nums = set()
            resolved = []
            seen_keys = set()
            for group_id, brand, part_number in chunk:
                key = (group_id, part_number)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                group_ids.add(group_id)
                part_nums.add(part_number)
                resolved.append((group_id, brand, part_number))
            if not resolved:
                continue

            self._remove_duplicate_parts(model, group_ids, part_nums)

            existing_map = {
                (item.group_id, item.part_number): item
                for item in model.objects.filter(group_id__in=group_ids, part_number__in=part_nums)
            }
            to_create = []
            to_claim = []
            for group_id, brand, part_number in resolved:
                key = (group_id, part_number)
                existing_item = existing_map.get(key)
                if existing_item is not None:
                    if existing_item.import_batch_id is None:
                        existing_item.import_batch = batch
                        to_claim.append(existing_item)
                    continue
                existing_map[key] = None
                to_create.append(model(
                    group_id=group_id,
                    brand=brand,
                    part_number=part_number,
                    import_batch=batch,
                ))
            if to_claim:
                model.objects.bulk_update(to_claim, ['import_batch'], batch_size=bs)
                saved += len(to_claim)
            if to_create:
                with transaction.atomic():
                    model.objects.bulk_create(to_create, batch_size=bs)
                saved += len(to_create)
        return saved

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

    # ── Sample file builders ──────────────────────────────────────────
    # One header row + one data row, matching exactly what each import
    # type's parser expects, so a user can download, fill in, re-upload.

    # Sample IDs are deliberately unrealistic (SAMPLE- prefixes) rather than
    # plausible-looking real IDs — a plausible ID can collide with an actual
    # row already in the database, and the Cars import updates existing
    # matches in place, silently overwriting real fields with sample data.
    @staticmethod
    def build_cars_sample_workbook():
        import openpyxl
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = 'CarId'
        sheet.append(['CarId', 'Car Model', 'Steering', 'Transmission', 'WD', 'Engine', 'Car parameters', 'Additional Note'])
        sheet.append(['SAMPLE-CAR-001', 'SAMPLE CAR MODEL NAME 2020-2024', 'LHD', 'AT', '4WD', 'SAMPLE 2.0L', 'SAMPLE PARAMS', 'SAMPLE NOTE'])
        return workbook

    @staticmethod
    def build_groups_sample_workbook():
        import openpyxl
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = 'carId & GroupId'
        sheet.append(['carId', 'GroupId'])
        sheet.append(['SAMPLE-CAR-001', 'SAMPLE-GROUP-001'])
        return workbook

    @staticmethod
    def build_parts_sample_workbook():
        import openpyxl
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = 'groupId & part_number'
        sheet.append(['groupId', 'Brand', 'part_number'])
        sheet.append(['SAMPLE-GROUP-001', 'SampleBrand', 'SAMPLE-PART-001'])
        return workbook

    @staticmethod
    def build_crosscode_sample_workbook():
        """Sample matching the Cross Code Product Brand / Product No / Brand / Code file."""
        import openpyxl
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = 'Cross Code'
        sheet.append(['Product Brand', 'Product No', 'Brand', 'Code'])
        sheet.append(['NIBK', 'PN0150W', 'MITSUBISHI', '4605A049'])
        sheet.append(['NIBK', 'PN0150W', 'MITSUBISHI', 'MR407376'])
        sheet.append(['NIBK', 'PN0202', 'CHRYSLER', '05174 327AB'])
        sheet.append(['NIBK', 'PN0150S', 'CHRYSLER (USA)', '68003 610AA'])
        sheet.append(['NIBK', 'PN0801', 'CHRYSLER', '4897218AB'])
        return workbook

    @staticmethod
    def build_car_with_parts_sample_workbook():
        import openpyxl
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = 'Car with parts'
        sheet.append(['Car Name', 'Part Number'])
        sheet.append(['SAMPLE CAR MODEL NAME 2020-2024', 'SAMPLE-PART-001'])
        return workbook
