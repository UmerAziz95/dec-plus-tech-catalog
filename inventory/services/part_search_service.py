import openpyxl
from django.conf import settings

from inventory.models import BasketItem, Car, CarGroup, Part
from inventory.services.basket_service import BasketService
from inventory.services.part_number_utils import annotate_part_number_normalized, sanitize_part_number


class PartSearchService:
    @staticmethod
    def build_results(query):
        query = sanitize_part_number(query)
        if not query:
            return []

        matching_parts = annotate_part_number_normalized(
            Part.objects.all()
        ).filter(part_number_norm__iexact=query)

        if not matching_parts.exists():
            matching_parts = annotate_part_number_normalized(
                Part.objects.all()
            ).filter(part_number_norm__icontains=query)

        group_pks = set(matching_parts.values_list('group_id', flat=True))
        car_groups = CarGroup.objects.filter(
            group_id__in=group_pks
        )

        car_ids = set(car_groups.values_list('car_id', flat=True))
        cars = Car.objects.filter(id__in=car_ids)
        cars_by_car_id = {str(c.id): c for c in cars}

        parts_by_group_pk = {}
        for part in matching_parts:
            group_pk = part.group_id
            if group_pk not in parts_by_group_pk:
                parts_by_group_pk[group_pk] = []
            parts_by_group_pk[group_pk].append(part)

        car_data = {}
        for cg in car_groups:
            car = cars_by_car_id.get(cg.car_id)
            if not car:
                continue
            group_pk = cg.group_id

            if car.id not in car_data:
                car_data[car.id] = {
                    'car': car,
                    'parts': [],
                    'part_numbers': set(),
                }

            if group_pk in parts_by_group_pk:
                for part in parts_by_group_pk[group_pk]:
                    if part.part_number not in car_data[car.id]['part_numbers']:
                        car_data[car.id]['parts'].append(part)
                        car_data[car.id]['part_numbers'].add(part.part_number)

        results = list(car_data.values())
        results.sort(key=lambda item: item['car'].car_model or '')
        return results

    @staticmethod
    def add_results_to_basket(user, results, brand, brand_number):
        brand, brand_number = BasketService.normalize_cross_brand_fields(brand, brand_number)
        part_ids = set()
        for item in results:
            for part in item['parts']:
                part_ids.add(part.id)

        if not part_ids:
            return 0

        existing_keys = BasketService.existing_item_keys(user, part_ids)
        basket, _created = BasketService.get_or_create_basket(brand, brand_number)

        entries = []
        pending_keys = set()
        batch_size = getattr(settings, 'IMPORT_ROW_BATCH_SIZE', 200)

        for item in results:
            car = item['car']
            for part in item['parts']:
                key = (car.id, part.id, basket.id)
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
