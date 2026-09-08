import openpyxl
from django.conf import settings

from inventory.models import (
    BasketItem, BasketItemCrossCode, Car, CarCrossCode, CarGroup, CarGroupCrossCode,
    ImportBatch, Part, PartCrossCode,
)
from inventory.services.basket_service import BasketService, BasketServiceCrossCode
from inventory.services.part_number_utils import (
    PART_NUMBER_SANITIZE_REGEX,
    crosscode_wildcard_sql,
    crosscode_wildcard_to_like,
    filter_parts_exact,
    filter_parts_contains,
    sanitize_crosscode_search,
    sanitize_part_number,
)


# Display labels for the Source column on Cross Cars search results.
# Mapped from Import Data tools:
#   1–3 (cars / groups / parts) → Catalog import
#   4 (car with parts)          → Car with parts
#   5 (add part manually)       → Manual entry
SOURCE_CATALOG_IMPORT = 'Catalog import'
SOURCE_CAR_WITH_PARTS = 'Car with parts'
SOURCE_MANUAL_ENTRY = 'Manual entry'
# Legacy aliases (older UI / Cross Code catalog tags).
SOURCE_PARTS_CAT = SOURCE_CATALOG_IMPORT
SOURCE_OTHER_DB = 'other_db'

_MANUAL_GROUP_PREFIX = 'MANUAL'
_CWP_GROUP_PREFIX = 'CWP'


class PartSearchService:
    car_model = Car
    car_group_model = CarGroup
    part_model = Part
    basket_item_model = BasketItem
    basket_service = BasketService
    source_label = SOURCE_CATALOG_IMPORT
    # Cross Cars search page stays on its own catalog only.
    include_other_catalog = False

    @classmethod
    def resolve_part_source(cls, part):
        """
        Map a Cross Cars part to its Import Data tool source label.
        Prefers group-id prefixes (manual / car-with-parts), then import_batch type.
        """
        group_id = (getattr(part, 'group_id', None) or '').strip()
        group_upper = group_id.upper()
        if group_upper.startswith(_MANUAL_GROUP_PREFIX):
            return SOURCE_MANUAL_ENTRY
        if group_upper.startswith(_CWP_GROUP_PREFIX):
            return SOURCE_CAR_WITH_PARTS

        batch = getattr(part, 'import_batch', None)
        if batch is not None:
            import_type = getattr(batch, 'import_type', None)
            if import_type == ImportBatch.TYPE_CAR_WITH_PARTS:
                return SOURCE_CAR_WITH_PARTS
            if import_type in (
                ImportBatch.TYPE_CARS,
                ImportBatch.TYPE_GROUPS,
                ImportBatch.TYPE_PARTS,
            ):
                return SOURCE_CATALOG_IMPORT

        return SOURCE_CATALOG_IMPORT

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
        """Search one catalog and tag every row with its import-tool source label."""
        # ── Step 1: Find matching parts ──────────────────────────────
        # Try exact match first (uses B-tree index — instant).
        matching_parts = list(
            filter_parts_exact(query, model=cls.part_model).select_related('import_batch')
        )

        # Fall back to substring match only if exact match found nothing.
        # Uses GIN trigram index — still fast on 135M rows.
        if not matching_parts:
            matching_parts = list(
                filter_parts_contains(query, model=cls.part_model)
                .select_related('import_batch')[:10000]  # Cap to prevent memory blow-up
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
        # CarGroup.car_id stores the Car primary key as text — coerce to int
        # so orphan/non-numeric values are skipped instead of breaking the join.
        car_pks = []
        for raw_id in car_id_set:
            try:
                car_pks.append(int(str(raw_id).strip()))
            except (TypeError, ValueError):
                continue
        cars = cls.car_model.objects.filter(id__in=car_pks)
        cars_by_car_id = {str(c.id): c for c in cars}

        # ── Step 5: Assemble results (one row per car + source) ───────
        car_data = {}
        for group_id, car_ids_for_group in group_to_car_ids.items():
            parts_for_group = parts_by_group.get(group_id, [])
            for car_id in car_ids_for_group:
                car = cars_by_car_id.get(car_id)
                if not car:
                    continue

                for part in parts_for_group:
                    source = cls.resolve_part_source(part)
                    key = (car.id, source)
                    if key not in car_data:
                        car_data[key] = {
                            'car': car,
                            'parts': [],
                            'part_numbers': set(),
                            'source': source,
                        }

                    bucket = car_data[key]
                    if part.part_number not in bucket['part_numbers']:
                        bucket['parts'].append(part)
                        bucket['part_numbers'].add(part.part_number)

        results = list(car_data.values())
        results.sort(key=lambda item: (item['car'].car_model or '', item.get('source') or ''))
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
    def prepare_query(cls, raw_query):
        """Sanitize for interactive Cross Code search (keeps *)."""
        return sanitize_crosscode_search(raw_query, keep_star=True)

    @classmethod
    def _product_no_exact_exists(cls, query):
        return cls.part_model.objects.extra(
            where=["regexp_replace(upper(coalesce(product_no, '')), %s, '', 'g') = %s"],
            params=[PART_NUMBER_SANITIZE_REGEX, query],
        ).exists()

    @classmethod
    def find_candidate_codes(cls, raw_query, limit=40, unique_by='code'):
        """
        Codes matching a wildcard (*) or prefix query.
        Used for the Did you mean? step.

        Prefix matches Code only. Wildcard matches Code, Brand, and OE Brand.
        Product No is not used, so unrelated family OE codes are not listed.
        unique_by='code' keeps one row per Code; unique_by='code_brand'
        keeps every Brand + Code pair.
        """
        query = sanitize_crosscode_search(raw_query, keep_star=True)
        if not query:
            return []

        if '*' in query:
            like = crosscode_wildcard_to_like(query)
            where_sql, params = crosscode_wildcard_sql(
                like, ('code', 'brand', 'oe_brand'),
            )
        else:
            like = f'{query}%'
            where_sql, params = crosscode_wildcard_sql(like, ('code',))

        fetch_limit = max(limit * 16, 320) if unique_by == 'code_brand' else max(limit * 8, 160)
        rows = (
            cls.part_model.objects
            .extra(where=[where_sql], params=params)
            .order_by('oe_brand', 'part_number')
            .values('part_number', 'oe_brand', 'brand', 'product_no')
            [:fetch_limit]
        )

        candidates = []
        seen = set()
        for row in rows:
            item_key, item = cls._candidate_row(row, unique_by=unique_by)
            if not item_key or item_key in seen:
                continue
            seen.add(item_key)
            candidates.append(item)
            if len(candidates) >= limit:
                break
        return candidates

    @classmethod
    def _candidate_row(cls, row, unique_by='code_brand'):
        part_number = row.get('part_number') or ''
        code_key = sanitize_part_number(part_number)
        if not code_key:
            return None, None
        oe_brand = (row.get('oe_brand') or '').strip()
        product_no = (row.get('product_no') or '').strip()
        if unique_by == 'code':
            key = code_key
        else:
            key = (code_key, oe_brand.casefold())
        return key, {
            'part_number': part_number,
            'oe_brand': oe_brand,
            'brand': (row.get('brand') or '').strip(),
            'product_no': product_no,
        }

    @classmethod
    def needs_disambiguation(cls, raw_query):
        """
        Exact Product No opens matching Product No rows. A complete unique Code with one
        OE Brand and one Product Brand opens matching Code / Product No rows. The same complete
        Code with several OE Brands or several Product Brands shows Did you
        mean (Brand + Code), including every Product Brand that differs.
        A partial Code lists every matching Brand + Code pair, plus every
        Product Brand when it differs.
        """
        query = sanitize_crosscode_search(raw_query, keep_star=True)
        if not query:
            return False, [], query

        if '*' in query:
            candidates = cls._partial_brand_code_candidates(query)
            if not candidates:
                return False, [], query
            return True, candidates, query

        candidates = cls.find_candidate_codes(query)
        exact_unique_code = (
            len(candidates) == 1
            and sanitize_part_number(candidates[0]['part_number']) == query
        )
        if exact_unique_code:
            brand_rows = [
                item for item in cls.find_exact_candidates([query])
                if sanitize_part_number(item['part_number']) == query
            ]
            product_rows = cls._product_brand_values_for_codes([query])
            did_you_mean_rows = cls._mark_pick_brand(
                cls._append_distinct_product_brands(
                    brand_rows, product_rows=product_rows,
                ),
            )
            product_brands = {
                (row.get('brand') or '').strip().casefold()
                for row in product_rows
                if (row.get('brand') or '').strip()
            }
            if len(brand_rows) > 1 or len(product_brands) > 1:
                return True, did_you_mean_rows, query
            return False, [], candidates[0]['part_number']

        if cls._product_no_exact_exists(query):
            return False, [], query

        if not candidates:
            return False, [], query
        return True, cls._partial_brand_code_candidates(query), query

    @classmethod
    def _partial_brand_code_candidates(cls, query):
        rows = cls.find_candidate_codes(query, unique_by='code_brand', limit=80)
        return cls._mark_pick_brand(cls._append_distinct_product_brands(rows))

    @classmethod
    def _product_brand_values_for_codes(cls, code_keys):
        """All Code rows needed to list distinct Product Brands per Code."""
        keys = []
        seen = set()
        for raw in code_keys or []:
            key = sanitize_part_number(raw)
            if not key or key in seen:
                continue
            seen.add(key)
            keys.append(key)
        if not keys:
            return []
        placeholders = ', '.join(['%s'] * len(keys))
        return list(
            cls.part_model.objects.extra(
                where=[f'part_number_norm IN ({placeholders})'],
                params=keys,
            ).order_by('part_number', 'brand', 'product_no')
            .values('part_number', 'brand', 'product_no')
        )

    @classmethod
    def _append_distinct_product_brands(cls, brand_rows, product_rows=None):
        """
        If Product Brand differs from Brand (oe_brand), add a Did you mean
        row for that Product Brand with the same Code.

        Uses every Product Brand stored for each Code, not only the first
        row kept after OE Brand dedupe.
        """
        extra = []
        seen = {
            (
                sanitize_part_number(item.get('part_number')),
                (item.get('oe_brand') or '').strip().casefold(),
            )
            for item in brand_rows
        }
        display_by_code = {}
        code_keys = []
        for item in brand_rows:
            code_key = sanitize_part_number(item.get('part_number'))
            if not code_key or code_key in display_by_code:
                continue
            display_by_code[code_key] = item.get('part_number') or ''
            code_keys.append(code_key)
        if product_rows is None:
            product_rows = cls._product_brand_values_for_codes(code_keys)
        added = set()
        for row in product_rows:
            product_brand = (row.get('brand') or '').strip()
            code_key = sanitize_part_number(row.get('part_number'))
            key = (code_key, product_brand.casefold())
            if not product_brand or not code_key or key in seen or key in added:
                continue
            added.add(key)
            extra.append({
                'part_number': display_by_code.get(code_key) or row.get('part_number') or '',
                'oe_brand': product_brand,
                'brand': product_brand,
                'product_no': (row.get('product_no') or '').strip(),
                'pick_product_brand': True,
            })
        return list(brand_rows) + extra

    @classmethod
    def _mark_pick_brand(cls, candidates):
        marked = []
        for item in candidates:
            row = dict(item)
            row['pick_brand'] = True
            marked.append(row)
        return marked

    @classmethod
    def find_exact_candidates(cls, keys, limit=200):
        """Exact Code / Product No matches only (no prefix, no family expand)."""
        candidates = []
        seen = set()
        search_keys = []
        for raw in keys or []:
            key = sanitize_part_number(raw)
            if key and key not in search_keys:
                search_keys.append(key)

        values = ('part_number', 'oe_brand', 'brand', 'product_no')
        for key in search_keys:
            rows = list(
                cls.part_model.objects.extra(
                    where=["part_number_norm = %s"],
                    params=[key],
                ).order_by('oe_brand', 'part_number').values(*values)[:limit]
            )
            if not rows:
                rows = list(
                    cls.part_model.objects.extra(
                        where=[
                            "regexp_replace(upper(coalesce(product_no, '')), %s, '', 'g') = %s"
                        ],
                        params=[PART_NUMBER_SANITIZE_REGEX, key],
                    ).order_by('oe_brand', 'part_number').values(*values)[:limit]
                )
            for row in rows:
                item_key, item = cls._candidate_row(row)
                if not item_key or item_key in seen:
                    continue
                seen.add(item_key)
                candidates.append(item)
                if len(candidates) >= limit:
                    return candidates
        return candidates

    @classmethod
    def candidates_from_bulk_rows(cls, rows_data, limit=200):
        """Did you mean? rows from a bulk upload (exact matches only)."""
        keys = []
        for row in rows_data or []:
            if row.get('bulk_miss'):
                continue
            key = sanitize_part_number(row.get('part_number') or '')
            if key:
                keys.append(key)
        return cls.find_exact_candidates(keys, limit=limit)

    @staticmethod
    def _product_family_key(part):
        brand = (part.brand or '').strip()
        product_no = (part.product_no or '').strip()
        if not brand or not product_no:
            return None
        return (brand, product_no)

    @classmethod
    def expand_product_families(cls, parts):
        """
        Expand matched rows to the full Product Brand + Product No family.

        Only expands when every matched row belongs to the same non-empty
        Product Brand + Product No. If the same Code+Brand sits in more than
        one product group (PN3469 vs PN3469S), those groups are not merged.
        """
        if not parts:
            return []

        by_id = {part.id: part for part in parts}
        families = {
            key for part in parts
            if (key := cls._product_family_key(part))
        }
        if len(families) == 1:
            brand, product_no = next(iter(families))
            for part in cls.part_model.objects.filter(
                brand=brand, product_no=product_no,
            ).iterator(chunk_size=500):
                by_id[part.id] = part

        return sorted(
            by_id.values(),
            key=lambda part: (
                part.brand or '',
                part.product_no or '',
                part.oe_brand or '',
                part.part_number or '',
            ),
        )

    @classmethod
    def build_results(cls, query, oe_brand=None, product_brand=None):
        """
        Exact Code or Product No matches. A selected Did you mean brand
        matches Product Brand or Brand (oe_brand).
        """
        query = sanitize_crosscode_search(query, keep_star=False)
        oe_brand = (oe_brand or '').strip()
        product_brand = (product_brand or '').strip()
        if not query:
            return []

        # Do not use filter_parts_exact here: it DISTINCT ON (part_number, group_id),
        # which drops extra Brands for the same Code.
        matching_parts = list(
            cls.part_model.objects.extra(
                where=["part_number_norm = %s"],
                params=[query],
            )
        )
        product_matches = list(
            cls.part_model.objects.extra(
                where=["regexp_replace(upper(coalesce(product_no, '')), %s, '', 'g') = %s"],
                params=[PART_NUMBER_SANITIZE_REGEX, query],
            )[:5000]
        )

        by_id = {}
        for part in matching_parts + product_matches:
            by_id[part.id] = part

        matched = list(by_id.values())
        brand_keys = {
            key.casefold()
            for key in (oe_brand, product_brand)
            if key
        }
        if brand_keys:
            matched = [
                part for part in matched
                if (part.oe_brand or '').strip().casefold() in brand_keys
                or (part.brand or '').strip().casefold() in brand_keys
            ]

        matched = sorted(
            matched,
            key=lambda part: (
                part.brand or '',
                part.product_no or '',
                part.oe_brand or '',
                part.part_number or '',
            ),
        )
        return [{
            'part': part,
            'parts': [part],
            'source': cls.source_label,
        } for part in matched]

    @classmethod
    def matching_parts_exist(cls, query):
        query = sanitize_crosscode_search(query, keep_star=False)
        if not query:
            return False
        if filter_parts_exact(query, model=cls.part_model).exists():
            return True
        return cls._product_no_exact_exists(query)

    @classmethod
    def suggest_part_numbers(cls, query, limit=40):
        """Autocomplete suggestions — prefix first, then contains."""
        query = sanitize_crosscode_search(query, keep_star=False)
        if len(query) < 1:
            return []

        suggestions = []
        seen = set()

        def collect(where_sql, param):
            rows = (
                cls.part_model.objects
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

        if collect('part_number_norm LIKE %s', f'{query}%'):
            return suggestions
        collect('part_number_norm ILIKE %s', f'%{query}%')
        return suggestions

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

        line_keys = {
            ((p.oe_brand or ''), p.part_number)
            for p in parts
            if p.part_number
        }
        existing_keys = cls.basket_service.existing_item_keys(
            user,
            [part_number for _oe_brand, part_number in line_keys],
        )
        basket, _created = cls.basket_service.get_or_create_basket(brand, brand_number)

        entries = []
        pending_keys = set()
        batch_size = getattr(settings, 'IMPORT_ROW_BATCH_SIZE', 200)

        for part in parts:
            if not part.part_number:
                continue
            key = ((part.oe_brand or ''), part.part_number, basket.id)
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
        normalized = sanitize_crosscode_search(raw_query, keep_star=False)
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
