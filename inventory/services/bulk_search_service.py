from django.conf import settings
import openpyxl

from inventory.models import BasketItem, Car, CarGroup, Part
from inventory.services.basket_service import BasketService
from inventory.services.part_number_utils import sanitize_part_number


class BulkSearchService:
    @staticmethod
    def _parse_header_indices(header_row):
        brand_idx = -1
        brand_number_idx = -1
        part_number_idx = -1

        for i, header in enumerate(header_row):
            if not header:
                continue
            h = str(header).lower().strip()
            if h in ['brand', 'cross brand']:
                brand_idx = i
            elif h in ['brand number', 'cross code', 'code']:
                brand_number_idx = i
            elif h in ['part number', 'part_number']:
                part_number_idx = i

        return brand_idx, brand_number_idx, part_number_idx

    @staticmethod
    def _read_rows(sheet, brand_idx, brand_number_idx, part_number_idx):
        rows_data = []
        part_numbers_to_search = set()

        for row in sheet.iter_rows(min_row=2, values_only=True):
            part_no = row[part_number_idx]
            if not part_no:
                continue

            part_no = str(part_no).strip()
            part_no = sanitize_part_number(part_no)
            if not part_no:
                continue
            brand = str(row[brand_idx]).strip() if brand_idx != -1 and row[brand_idx] else ''
            brand_num = str(row[brand_number_idx]).strip() if brand_number_idx != -1 and row[brand_number_idx] else ''
            brand, brand_num = BasketService.normalize_cross_brand_fields(brand, brand_num)

            rows_data.append({
                'brand': brand,
                'brand_number': brand_num,
                'part_number': part_no,
            })
            part_numbers_to_search.add(part_no)

        return rows_data, part_numbers_to_search

    @staticmethod
    def _load_matches(part_numbers_to_search):
        # Use the stored generated column part_number_norm with B-tree index
        matching_parts = Part.objects.extra(
            where=["part_number_norm IN %s"],
            params=[tuple(part_numbers_to_search)],
        ).order_by('part_number', 'group_id').distinct('part_number', 'group_id')

        parts_by_part_num = {}
        group_pks = set()
        for part in matching_parts:
            # Normalize the part_number to match the search key
            pn = sanitize_part_number(part.part_number)
            if pn not in parts_by_part_num:
                parts_by_part_num[pn] = []
            parts_by_part_num[pn].append(part)
            group_pks.add(part.group_id)

        car_groups = CarGroup.objects.filter(group_id__in=group_pks)

        car_ids = set(car_groups.values_list('car_id', flat=True))
        cars = Car.objects.filter(car_id__in=car_ids)
        cars_by_car_id = {str(c.car_id): c for c in cars}

        cars_by_group_pk = {}
        for cg in car_groups:
            car = cars_by_car_id.get(cg.car_id)
            if not car:
                continue
            if cg.group_id not in cars_by_group_pk:
                cars_by_group_pk[cg.group_id] = []
            cars_by_group_pk[cg.group_id].append(car)

        return parts_by_part_num, cars_by_group_pk

    @staticmethod
    def _existing_basket_keys(user, part_numbers_to_search):
        return BasketService.existing_item_keys(user, part_numbers_to_search)

    @staticmethod
    def _build_basket_entries(user, rows_data, parts_by_part_num, cars_by_group_pk, existing_keys):
        entries = []
        pending_keys = set()
        batch_size = getattr(settings, 'IMPORT_ROW_BATCH_SIZE', 200)
        brand_pairs = {
            BasketService.normalize_cross_brand_fields(
                row_data['brand'],
                row_data['brand_number'],
            )
            for row_data in rows_data
            if row_data['brand'] and row_data['brand_number']
        }
        basket_map = BasketService.get_or_create_baskets_for_pairs(brand_pairs)

        for row_data in rows_data:
            brand, brand_number = BasketService.normalize_cross_brand_fields(
                row_data['brand'],
                row_data['brand_number'],
            )
            part_no = row_data['part_number']

            if not brand or not brand_number:
                continue

            found_parts = parts_by_part_num.get(part_no, [])
            if not found_parts:
                continue

            basket = basket_map[(brand, brand_number)]
            for part in found_parts:
                for car in cars_by_group_pk.get(part.group_id, []):
                    key = (car.id, part.part_number, basket.id)
                    if key in existing_keys or key in pending_keys:
                        continue
                    pending_keys.add(key)
                    entries.append(BasketItem(
                        user=user,
                        car=car,
                        part=part,
                        basket=basket,
                        group_id=part.group_id,
                    ))

        for offset in range(0, len(entries), batch_size):
            BasketItem.objects.bulk_create(entries[offset:offset + batch_size], ignore_conflicts=True)

        return len(entries)

    @staticmethod
    def _matched_car_ids(rows_data, parts_by_part_num, cars_by_group_pk):
        matched_car_ids = set()
        for row_data in rows_data:
            if not row_data['brand'] or not row_data['brand_number']:
                continue
            for part in parts_by_part_num.get(row_data['part_number'], []):
                for car in cars_by_group_pk.get(part.group_id, []):
                    matched_car_ids.add(car.id)
        return matched_car_ids

    @staticmethod
    def _build_results(rows_data, parts_by_part_num, cars_by_group_pk, basket_part_numbers):
        results = []

        for row_data in rows_data:
            part_no = row_data['part_number']
            brand = row_data['brand']
            brand_number = row_data['brand_number']
            found_parts = parts_by_part_num.get(part_no, [])

            if not brand or not brand_number:
                results.append({
                    'brand': brand,
                    'brand_number': brand_number,
                    'part_number': part_no,
                    'car_count': 0,
                    'top_cars': [],
                    'in_basket': False,
                    'status': 'Not Found',
                })
                continue

            if not found_parts:
                results.append({
                    'brand': brand,
                    'brand_number': brand_number,
                    'part_number': part_no,
                    'car_count': 0,
                    'top_cars': [],
                    'in_basket': False,
                    'status': 'Not Found',
                })
                continue

            car_set = set()
            top_cars = []
            for part in found_parts:
                for car in cars_by_group_pk.get(part.group_id, []):
                    if car.id not in car_set:
                        car_set.add(car.id)
                        if len(top_cars) < 3:
                            top_cars.append(car.car_model)

            results.append({
                'brand': brand,
                'brand_number': brand_number,
                'part_number': part_no,
                'car_count': len(car_set),
                'top_cars': top_cars,
                'in_basket': part_no in basket_part_numbers,
                'status': 'Found',
            })

        return results

    @staticmethod
    def _build_summary(rows_data, parts_by_part_num, matched_car_ids, missed_rows, added_to_basket):
        searched_count = len(rows_data)
        found_count = 0
        for row_data in rows_data:
            if row_data['brand'] and row_data['brand_number'] and row_data['part_number'] in parts_by_part_num:
                found_count += 1

        return {
            'searched_count': searched_count,
            'found_count': found_count,
            'car_models_matched': len(matched_car_ids),
            'missed_count': len(missed_rows),
            'added_to_basket': added_to_basket,
            'missed_rows': missed_rows,
        }

    @classmethod
    def parse_upload(cls, excel_file):
        if not excel_file.name.endswith('.xlsx'):
            return None, 'Please upload a valid .xlsx file.'

        try:
            wb = openpyxl.load_workbook(excel_file, data_only=True)
            sheet = wb.active
            header_row = [cell.value for cell in sheet[1]]
            brand_idx, brand_number_idx, part_number_idx = cls._parse_header_indices(header_row)

            if part_number_idx == -1:
                return None, 'Could not find "Part Number" or "Part_number" column in the Excel file.'

            rows_data, _part_numbers = cls._read_rows(sheet, brand_idx, brand_number_idx, part_number_idx)
            return rows_data, None
        except Exception as exc:
            return None, f'Error processing file: {str(exc)}'

    @classmethod
    def run_bulk_search(cls, user, rows_data, save_to_basket=True):
        if not rows_data:
            summary = {
                'searched_count': 0,
                'found_count': 0,
                'car_models_matched': 0,
                'missed_count': 0,
                'added_to_basket': 0,
                'missed_rows': [],
            }
            return [], summary, None

        part_numbers_to_search = {row['part_number'] for row in rows_data if row.get('part_number')}
        parts_by_part_num, cars_by_group_pk = cls._load_matches(part_numbers_to_search)

        added_to_basket = 0
        basket_part_numbers = set()
        if save_to_basket:
            existing_keys = cls._existing_basket_keys(user, part_numbers_to_search)
            added_to_basket = cls._build_basket_entries(
                user,
                rows_data,
                parts_by_part_num,
                cars_by_group_pk,
                existing_keys,
            )
            matched_part_ids = Part.objects.extra(
                where=["part_number_norm IN %s"],
                params=[tuple(part_numbers_to_search)],
            ).values_list('id', flat=True)
            basket_part_numbers = {
                sanitize_part_number(part_number)
                for part_number in BasketItem.objects.filter(
                    user=user,
                    part_id__in=matched_part_ids,
                ).values_list('part__part_number', flat=True)
            }

        matched_car_ids = cls._matched_car_ids(rows_data, parts_by_part_num, cars_by_group_pk)
        results = cls._build_results(rows_data, parts_by_part_num, cars_by_group_pk, basket_part_numbers)

        missed_rows = []
        for row_data in rows_data:
            part_no = row_data['part_number']
            brand = row_data['brand']
            brand_number = row_data['brand_number']
            if not brand or not brand_number or part_no not in parts_by_part_num:
                missed_rows.append(row_data)

        summary = cls._build_summary(rows_data, parts_by_part_num, matched_car_ids, missed_rows, added_to_basket)
        return results, summary, None

    @classmethod
    def process_upload(cls, user, excel_file):
        rows_data, error_message = cls.parse_upload(excel_file)
        if error_message:
            return None, False, None, error_message
        results, summary, err = cls.run_bulk_search(user, rows_data, save_to_basket=True)
        return results, True, summary, err

    @staticmethod
    def build_missed_workbook(missed_rows):
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = 'Missed'
        sheet.append(['Cross Brand', 'Cross Code', 'Part Number'])
        for row in missed_rows:
            sheet.append([
                row.get('brand', ''),
                row.get('brand_number', ''),
                row.get('part_number', ''),
            ])
        return workbook

    @staticmethod
    def build_results_workbook(results):
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = 'Bulk search'
        sheet.append([
            'Cross Brand',
            'Cross Code',
            'Part number',
            'Status',
            'Vehicle count',
            'Sample models',
            'In basket',
        ])
        for row in results:
            top = row.get('top_cars') or []
            sample = '; '.join(top[:10])
            if len(top) > 10:
                sample = f'{sample}...'
            sheet.append([
                str(row.get('brand') or ''),
                str(row.get('brand_number') or ''),
                str(row.get('part_number') or ''),
                str(row.get('status') or ''),
                int(row.get('car_count') or 0),
                sample,
                'Yes' if row.get('in_basket') else 'No',
            ])
        return workbook
