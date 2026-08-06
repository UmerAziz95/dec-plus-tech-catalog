import openpyxl
from django.conf import settings

from inventory.models import (
    BasketItem, BasketItemCrossCode, Car, CarCrossCode, CarGroup, CarGroupCrossCode,
    Part, PartCrossCode,
)
from inventory.services.basket_service import BasketService, BasketServiceCrossCode
from inventory.services.part_number_utils import (
    PART_NUMBER_SANITIZE_REGEX,
    filter_parts_exact,
    filter_parts_contains,
    sanitize_part_number,
)


# Display labels for the Source column on manual search results.
SOURCE_PARTS_CAT = 'parts-cat.com'
SOURCE_OTHER_DB = 'other_db'


class PartSearchService:
    car_model = Car
    car_group_model = CarGroup
    part_model = Part
    basket_item_model = BasketItem
    basket_service = BasketService
    source_label = SOURCE_PARTS_CAT
    # When True, build_results also includes the sibling catalog (Cross Code).
    include_other_catalog = True

    @classmethod
    def build_results(cls, query):
        query = sanitize_part_number(query)
        if not query:
            return []

        primary = cls._build_catalog_results(query)
        if not cls.include_other_catalog:
            return cls._dedupe_results(primary)

        other = PartSearchServiceCrossCode._build_catalog_results(query)
        return cls._dedupe_results(primary + other)

    @classmethod
    def matching_parts_exist(cls, query):
        """True when the part number exists even if no linked vehicle can be shown."""
        query = sanitize_part_number(query)
        if not query:
            return False

        catalogs = [cls]
        if cls.include_other_catalog:
            catalogs.append(PartSearchServiceCrossCode)

        for service in catalogs:
            if filter_parts_exact(query, model=service.part_model).exists():
                return True
            if filter_parts_contains(query, model=service.part_model).exists():
                return True
        return False

    @classmethod
    def suggest_part_numbers(cls, query, limit=40):
        """Return distinct part numbers for autocomplete while typing."""
        query = sanitize_part_number(query)
        if len(query) < 1:
            return []

        models = [cls.part_model]
        if cls.include_other_catalog:
            models.append(PartCrossCode)

        suggestions = []
        seen = set()

        def collect(where_sql, param):
            for model in models:
                rows = (
                    model.objects
                    .extra(where=[where_sql], params=[param])
                    .order_by('part_number')
                    .values_list('part_number', flat=True)
                    .distinct()[:limit]
                )
                for part_number in rows:
                    key = sanitize_part_number(part_number)
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    suggestions.append(part_number)
                    if len(suggestions) >= limit:
                        return True
            return False

        # Prefer prefix matches (fast + natural for autocomplete).
        if collect('part_number_norm LIKE %s', f'{query}%'):
            return suggestions
        # Fall back to contains when prefix finds nothing.
        collect('part_number_norm ILIKE %s', f'%{query}%')
        return suggestions

    @classmethod
    def _build_catalog_results(cls, query):
        """Search one catalog and tag every row with this service's source label."""
        # ── Step 1: Find matching parts ──────────────────────────────
        # Try exact match first (uses B-tree index — instant).
        matching_parts = list(filter_parts_exact(query, model=cls.part_model))

        # Fall back to substring match only if exact match found nothing.
        # Uses GIN trigram index — still fast on 135M rows.
        if not matching_parts:
            matching_parts = list(
                filter_parts_contains(query, model=cls.part_model)[:10000]  # Cap to prevent memory blow-up
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
            cls.car_group_model.objects
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
        # CarGroup.car_id actually stores the Car primary key (as text),
        # not the business Car.car_id string — filter/lookup by pk.
        cars = cls.car_model.objects.filter(id__in=list(car_id_set))
        cars_by_car_id = {str(c.id): c for c in cars}

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
                        'source': cls.source_label,
                    }

                for part in parts_for_group:
                    if part.part_number not in car_data[car.id]['part_numbers']:
                        car_data[car.id]['parts'].append(part)
                        car_data[car.id]['part_numbers'].add(part.part_number)

        results = list(car_data.values())
        results.sort(key=lambda item: item['car'].car_model or '')
        return results

    @staticmethod
    def _result_dedupe_key(item):
        car = item['car']
        return (
            item.get('source') or '',
            (car.car_model or '').strip().lower(),
            (car.steering or '').strip().lower(),
            (car.transmission or '').strip().lower(),
            (car.wd or '').strip().lower(),
            (car.engine or '').strip().lower(),
        )

    @classmethod
    def _dedupe_results(cls, results):
        """Keep one row per unique vehicle fingerprint + source; merge part numbers."""
        unique = {}
        for item in results:
            key = cls._result_dedupe_key(item)
            existing = unique.get(key)
            if existing is None:
                unique[key] = {
                    'car': item['car'],
                    'parts': list(item['parts']),
                    'part_numbers': set(item.get('part_numbers') or (p.part_number for p in item['parts'])),
                    'source': item.get('source') or cls.source_label,
                }
                continue

            for part in item['parts']:
                if part.part_number not in existing['part_numbers']:
                    existing['parts'].append(part)
                    existing['part_numbers'].add(part.part_number)

        merged = list(unique.values())
        merged.sort(key=lambda item: (
            item['car'].car_model or '',
            item.get('source') or '',
        ))
        return merged

    @classmethod
    def add_results_to_basket(cls, user, results, brand, brand_number):
        # Only basket items from this service's own catalog models.
        # Combined search may include the other catalog for display only.
        basket_results = [
            item for item in results
            if isinstance(item.get('car'), cls.car_model)
        ]
        brand, brand_number = cls.basket_service.normalize_cross_brand_fields(brand, brand_number)
        part_numbers = set()
        for item in basket_results:
            for part in item['parts']:
                part_numbers.add(part.part_number)

        if not part_numbers:
            return 0

        existing_keys = cls.basket_service.existing_item_keys(user, part_numbers)
        basket, _created = cls.basket_service.get_or_create_basket(brand, brand_number)

        entries = []
        pending_keys = set()
        batch_size = getattr(settings, 'IMPORT_ROW_BATCH_SIZE', 200)

        for item in basket_results:
            car = item['car']
            for part in item['parts']:
                key = (car.id, part.part_number, basket.id)
                if key in existing_keys or key in pending_keys:
                    continue
                pending_keys.add(key)
                entries.append(cls.basket_item_model(
                    user=user,
                    car=car,
                    part=part,
                    basket=basket,
                    group_id=part.group_id,
                ))

        for offset in range(0, len(entries), batch_size):
            cls.basket_item_model.objects.bulk_create(entries[offset:offset + batch_size], ignore_conflicts=True)

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
            'Source',
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
                item.get('source') or '',
            ])
        return workbook


class PartSearchServiceCrossCode(PartSearchService):
    car_model = CarCrossCode
    car_group_model = CarGroupCrossCode
    part_model = PartCrossCode
    basket_item_model = BasketItemCrossCode
    basket_service = BasketServiceCrossCode
    source_label = SOURCE_OTHER_DB
    # Cross Code search page stays on its own catalog only.
    include_other_catalog = False

    @classmethod
    def build_results(cls, query):
        """Flat Cross Code rows: Product Brand / Product No / Brand / Code."""
        query = sanitize_part_number(query)
        if not query:
            return []

        matching_parts = list(filter_parts_exact(query, model=cls.part_model))
        if not matching_parts:
            matching_parts = list(
                filter_parts_contains(query, model=cls.part_model)[:10000]
            )

        # Also match Product No when Code search finds nothing (or in addition).
        product_matches = list(
            cls.part_model.objects.extra(
                where=["regexp_replace(upper(coalesce(product_no, '')), %s, '', 'g') = %s"],
                params=[PART_NUMBER_SANITIZE_REGEX, query],
            )[:5000]
        )
        if not matching_parts and not product_matches:
            product_matches = list(
                cls.part_model.objects.extra(
                    where=["regexp_replace(upper(coalesce(product_no, '')), %s, '', 'g') ILIKE %s"],
                    params=[PART_NUMBER_SANITIZE_REGEX, f'%{query}%'],
                )[:5000]
            )

        by_id = {}
        for part in matching_parts + product_matches:
            by_id[part.id] = part

        results = []
        for part in by_id.values():
            results.append({
                'part': part,
                'parts': [part],
                'source': cls.source_label,
            })
        results.sort(key=lambda item: (
            item['part'].brand or '',
            item['part'].product_no or '',
            item['part'].oe_brand or '',
            item['part'].part_number or '',
        ))
        return results

    @classmethod
    def add_results_to_basket(cls, user, results, brand, brand_number):
        brand, brand_number = cls.basket_service.normalize_cross_brand_fields(brand, brand_number)
        parts = []
        for item in results:
            if item.get('part') is not None:
                parts.append(item['part'])
            else:
                parts.extend(item.get('parts') or [])

        if not parts:
            return 0

        part_numbers = {p.part_number for p in parts}
        existing_keys = cls.basket_service.existing_item_keys(user, part_numbers)
        basket, _created = cls.basket_service.get_or_create_basket(brand, brand_number)

        entries = []
        pending_keys = set()
        batch_size = getattr(settings, 'IMPORT_ROW_BATCH_SIZE', 200)
        seen_part_ids = set()

        for part in parts:
            if part.id in seen_part_ids:
                continue
            seen_part_ids.add(part.id)
            key = (part.id, basket.id)
            if key in existing_keys or key in pending_keys:
                continue
            pending_keys.add(key)
            entries.append(cls.basket_item_model(
                user=user,
                car=None,
                part=part,
                basket=basket,
                group_id=part.group_id or part.product_no or '',
            ))

        for offset in range(0, len(entries), batch_size):
            cls.basket_item_model.objects.bulk_create(
                entries[offset:offset + batch_size],
                ignore_conflicts=True,
            )
        return len(entries)

    @staticmethod
    def build_export_workbook(raw_query, results):
        normalized = sanitize_part_number(raw_query)
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = 'Cross Code search'
        sheet.append([
            'Search (normalized)',
            'Brand',
            'Code',
            'Source',
        ])
        for item in results:
            part = item.get('part') or (item.get('parts') or [None])[0]
            if not part:
                continue
            sheet.append([
                normalized,
                part.brand or '',
                part.part_number,
                item.get('source') or '',
            ])
        return workbook
