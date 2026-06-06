import openpyxl
from django.conf import settings
from django.db import connection

from inventory.models import BasketItem, Car, CarGroup, Part
from inventory.services.basket_service import BasketService
from inventory.services.part_number_utils import (
    filter_parts_exact,
    filter_parts_contains,
    sanitize_part_number,
)


class PartSearchService:
    # loading millions of rows into memory on very broad matches.
    @staticmethod
    def build_results(query):
        query = sanitize_part_number(query)
        if not query:
            return []

        # ── Step 1: Find matching parts ──────────────────────────────
        # Try exact match first (uses B-tree index — instant).
        matching_parts = list(filter_parts_exact(query))

        # Fall back to substring match only if exact match found nothing.
        # Uses GIN trigram index — still fast on 135M rows.
        if not matching_parts:
            matching_parts = list(
                filter_parts_contains(query)[:10000]  # Cap to prevent memory blow-up
            )

        if not matching_parts:
            return []

        # ── Step 2: Get group_ids from matching parts ────────────────
        parts_by_group = {}
        for part in matching_parts:
            if part.group_id not in parts_by_group:
                parts_by_group[part.group_id] = []
            parts_by_group[part.group_id].append(part)

        group_ids = list(parts_by_group.keys())

        # ── Step 3: Fetch car_groups → cars in one efficient query ────
        # Uses the composite index idx_cargroups_groupid_carid
        car_groups = (
            CarGroup.objects
            .filter(group_id__in=group_ids)
            .values_list('group_id', 'car_id')
            .distinct()
        )

        car_id_set = set()
        group_to_car_ids = {}
        for group_id, car_id in car_groups:
            car_id_set.add(car_id)
            if group_id not in group_to_car_ids:
                group_to_car_ids[group_id] = []
            group_to_car_ids[group_id].append(car_id)

        if not car_id_set:
            return []

        # ── Step 4: Fetch car details ────────────────────────────────
        cars = Car.objects.filter(car_id__in=list(car_id_set))
        cars_by_car_id = {c.car_id: c for c in cars}

        # ── Step 5: Assemble results ─────────────────────────────────
        car_data = {}
        for group_id, car_ids_for_group in group_to_car_ids.items():
            parts_for_group = parts_by_group.get(group_id, [])
            for car_id in car_ids_for_group:
                car = cars_by_car_id.get(car_id)
                if not car:
                    continue

                if car.id not in car_data:
                    car_data[car.id] = {
                        'car': car,
                        'parts': [],
                        'part_numbers': set(),
                    }

                for part in parts_for_group:
                    if part.part_number not in car_data[car.id]['part_numbers']:
                        car_data[car.id]['parts'].append(part)
                        car_data[car.id]['part_numbers'].add(part.part_number)

        results = list(car_data.values())
        results.sort(key=lambda item: item['car'].car_model or '')

        # Return all results without capping
        return results

    @staticmethod
    def add_results_to_basket(user, results, brand, brand_number):
        brand, brand_number = BasketService.normalize_cross_brand_fields(brand, brand_number)
        part_numbers = set()
        for item in results:
            for part in item['parts']:
                part_numbers.add(part.part_number)

        if not part_numbers:
            return 0

        existing_keys = BasketService.existing_item_keys(user, part_numbers)
        basket, _created = BasketService.get_or_create_basket(brand, brand_number)

        entries = []
        pending_keys = set()
        batch_size = getattr(settings, 'IMPORT_ROW_BATCH_SIZE', 200)

        for item in results:
            car = item['car']
            for part in item['parts']:
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
    def build_export_workbook(raw_query, results):
        normalized = sanitize_part_number(raw_query)
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = 'Part search'
        sheet.append([
            'Search part (normalized)',
            'CarId',
            'Car Model',
            'Steering',
            'Transmission',
            'WD',
            'Engine',
            'Car Parameters',
            'OE part numbers',
        ])
        for item in results:
            car = item['car']
            parts_joined = ', '.join(p.part_number for p in item['parts'])
            sheet.append([
                normalized,
                car.car_id,
                car.car_model or '',
                car.steering or '',
                car.transmission or '',
                car.wd or '',
                car.engine or '',
                car.car_parameters or '',
                parts_joined,
            ])
        return workbook
