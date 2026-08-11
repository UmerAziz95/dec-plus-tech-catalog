import re

import openpyxl
from django.db.models import Count, Q

from inventory.models import Basket, BasketCrossCode, BasketItem, BasketItemCrossCode

_TAG_RE = re.compile(r'<[^>]+>')


class BasketService:
    BASKET_PAGE_SIZE = 20
    basket_model = Basket
    basket_item_model = BasketItem

    @staticmethod
    def normalize_cross_brand_fields(brand, brand_number):
        return (brand or '').strip(), (brand_number or '').strip()

    @classmethod
    def count_for_user(cls, user):
        return cls.get_items_queryset(user).count()

    @classmethod
    def get_or_create_basket(cls, brand, brand_number):
        brand, brand_number = cls.normalize_cross_brand_fields(brand, brand_number)
        return cls.basket_model.objects.get_or_create(
            brand=brand,
            brand_number=brand_number,
        )

    @classmethod
    def get_items_queryset(cls, user):
        """Return basket items with duplicates consolidated.
        Uses PostgreSQL DISTINCT ON to keep only one row per unique
        (brand, brand_number, part_number, car_model) combination."""
        return cls.basket_item_model.objects.filter(
            user=user,
        ).select_related('basket', 'car', 'part').order_by(
            'basket__brand', 'basket__brand_number',
            'part__part_number', 'car__car_model', '-id',
        ).distinct(
            'basket__brand', 'basket__brand_number',
            'part__part_number', 'car__car_model',
        )

    @staticmethod
    def clean_search_query(value):
        cleaned = _TAG_RE.sub('', (value or '').strip())
        return cleaned[:255]

    @classmethod
    def filter_items_queryset(cls, user, query):
        queryset = cls.get_items_queryset(user)
        query = cls.clean_search_query(query)
        if not query:
            return queryset
        return queryset.filter(
            Q(basket__brand__icontains=query)
            | Q(basket__brand_number__icontains=query)
            | Q(part__part_number__icontains=query)
            | Q(car__car_model__icontains=query)
            | Q(car__car_id__icontains=query)
        )

    @classmethod
    def item_exists(cls, user, car, part, brand, brand_number):
        brand, brand_number = cls.normalize_cross_brand_fields(brand, brand_number)
        return cls.basket_item_model.objects.filter(
            user=user,
            car=car,
            part=part,
            basket__brand=brand,
            basket__brand_number=brand_number,
        ).exists()

    @classmethod
    def add_item(cls, user, car, part, brand, brand_number):
        brand, brand_number = cls.normalize_cross_brand_fields(brand, brand_number)
        basket, _created = cls.get_or_create_basket(brand, brand_number)
        item, created = cls.basket_item_model.objects.get_or_create(
            user=user,
            car=car,
            part=part,
            basket=basket,
            defaults={'group_id': part.group_id}
        )
        return item, created

    @classmethod
    def prune_empty_baskets(cls):
        orphan_ids = cls.basket_model.objects.annotate(
            item_count=Count('items'),
        ).filter(item_count=0).values_list('pk', flat=True)
        if orphan_ids:
            cls.basket_model.objects.filter(pk__in=list(orphan_ids)).delete()

    @classmethod
    def remove_item(cls, item):
        """Remove an item and all its hidden duplicates sharing the same
        brand, brand_number, part_number, and car_model."""
        basket_id = item.basket_id
        # Delete all underlying copies of this consolidated row
        cls.basket_item_model.objects.filter(
            user=item.user,
            basket_id=item.basket_id,
            part__part_number=item.part.part_number,
            car__car_model=item.car.car_model,
        ).delete()
        if not cls.basket_item_model.objects.filter(basket_id=basket_id).exists():
            cls.basket_model.objects.filter(pk=basket_id).delete()

    @classmethod
    def remove_duplicates(cls, user):
        items = cls.basket_item_model.objects.filter(user=user).order_by('id').values_list(
            'id',
            'car_id',
            'part_id',
            'basket_id',
        )
        seen = set()
        duplicate_ids = []
        for item_id, car_id, part_id, basket_id in items:
            key = (car_id, part_id, basket_id)
            if key in seen:
                duplicate_ids.append(item_id)
            else:
                seen.add(key)

        if not duplicate_ids:
            return 0

        deleted, _ = cls.basket_item_model.objects.filter(user=user, id__in=duplicate_ids).delete()
        cls.prune_empty_baskets()
        return deleted

    @classmethod
    def clear_basket(cls, user):
        basket_ids = list(
            cls.basket_item_model.objects.filter(user=user).values_list('basket_id', flat=True).distinct()
        )
        deleted, _ = cls.basket_item_model.objects.filter(user=user).delete()
        if basket_ids:
            cls.basket_model.objects.annotate(item_count=Count('items')).filter(
                pk__in=basket_ids,
                item_count=0,
            ).delete()
        return deleted

    @classmethod
    def build_export_workbook(cls, user):
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = 'Basket'
        sheet.append(['Cross Brand', 'Cross Code', 'OE Number', 'Car Model'])
        # Use the consolidated queryset so export matches what the user sees
        for item in cls.get_items_queryset(user):
            sheet.append([
                item.basket.brand,
                item.basket.brand_number,
                item.part.part_number,
                item.car.car_model or '',
            ])
        return workbook

    @classmethod
    def get_or_create_baskets_for_pairs(cls, brand_pairs):
        basket_map = {}
        for brand, brand_number in brand_pairs:
            normalized = cls.normalize_cross_brand_fields(brand, brand_number)
            basket, _ = cls.get_or_create_basket(*normalized)
            basket_map[normalized] = basket
        return basket_map

    @classmethod
    def existing_item_keys(cls, user, part_numbers):
        return {
            (car_id, part_number, basket_id)
            for car_id, part_number, basket_id in cls.basket_item_model.objects.filter(
                user=user,
                part__part_number__in=part_numbers,
            ).values_list('car_id', 'part__part_number', 'basket_id')
        }

    @classmethod
    def get_user_basket_groups(cls, user):
        basket_ids = (
            cls.basket_item_model.objects.filter(user=user)
            .values_list('basket_id', flat=True)
            .distinct()
        )
        baskets = list(
            cls.basket_model.objects.filter(pk__in=basket_ids)
            .order_by('brand', 'brand_number')
        )
        # Attach consolidated item counts using the grouped queryset
        for basket in baskets:
            basket.item_count = cls.get_group_items_queryset(user, basket.pk).count()
        return baskets

    @classmethod
    def get_group_items_queryset(cls, user, basket_id):
        """Return group items with duplicates consolidated.
        Uses PostgreSQL DISTINCT ON to keep only one row per unique
        (part_number, car_model) combination within a basket group."""
        return cls.basket_item_model.objects.filter(
            user=user,
            basket_id=basket_id,
        ).select_related('basket', 'car', 'part').order_by(
            'part__part_number', 'car__car_model', '-id',
        ).distinct(
            'part__part_number', 'car__car_model',
        )

    @classmethod
    def filter_group_items_queryset(cls, user, basket_id, query):
        queryset = cls.get_group_items_queryset(user, basket_id)
        query = cls.clean_search_query(query)
        if not query:
            return queryset
        return queryset.filter(
            Q(basket__brand__icontains=query)
            | Q(basket__brand_number__icontains=query)
            | Q(part__part_number__icontains=query)
            | Q(car__car_model__icontains=query)
            | Q(car__car_id__icontains=query)
        )

    @classmethod
    def user_owns_basket_group(cls, user, basket_id):
        return cls.basket_item_model.objects.filter(user=user, basket_id=basket_id).exists()

    @staticmethod
    def parse_page_number(value, default=1):
        try:
            page = int(value)
        except (TypeError, ValueError):
            return default
        return max(1, page)

    @classmethod
    def page_after_delete(cls, requested_page, total_count, per_page=None):
        if per_page is None:
            per_page = cls.BASKET_PAGE_SIZE
        page = cls.parse_page_number(requested_page)
        if total_count <= 0:
            return 1
        max_page = max(1, (total_count + per_page - 1) // per_page)
        return min(page, max_page)

    @staticmethod
    def list_query_params(query, page):
        params = {}
        if query:
            params['q'] = query
        if page > 1:
            params['page'] = page
        return params


class BasketServiceCrossCode(BasketService):
    """
    Cross Code basket: one BasketCrossCode per Brand Name + Brand Number.

    Unique line under each group = Brand (oe_brand) + Part Number (Code),
    matching Cross code example.xlsx Bulk search-result.
    """
    basket_model = BasketCrossCode
    basket_item_model = BasketItemCrossCode

    @classmethod
    def get_items_queryset(cls, user):
        """One visible row per Brand Name + Brand Number + Brand + Code."""
        return cls.basket_item_model.objects.filter(
            user=user,
        ).select_related('basket', 'part').order_by(
            'basket__brand', 'basket__brand_number',
            'part__oe_brand', 'part__part_number', '-id',
        ).distinct(
            'basket__brand', 'basket__brand_number',
            'part__oe_brand', 'part__part_number',
        )

    @classmethod
    def filter_items_queryset(cls, user, query):
        queryset = cls.get_items_queryset(user)
        query = cls.clean_search_query(query)
        if not query:
            return queryset
        return queryset.filter(
            Q(basket__brand__icontains=query)
            | Q(basket__brand_number__icontains=query)
            | Q(part__part_number__icontains=query)
            | Q(part__brand__icontains=query)
            | Q(part__product_no__icontains=query)
            | Q(part__oe_brand__icontains=query)
        )

    @classmethod
    def item_exists(cls, user, part, brand, brand_number, car=None):
        brand, brand_number = cls.normalize_cross_brand_fields(brand, brand_number)
        return cls.basket_item_model.objects.filter(
            user=user,
            part__oe_brand=part.oe_brand or '',
            part__part_number=part.part_number,
            basket__brand=brand,
            basket__brand_number=brand_number,
        ).exists()

    @classmethod
    def add_item(cls, user, part, brand, brand_number, car=None):
        brand, brand_number = cls.normalize_cross_brand_fields(brand, brand_number)
        basket, _created = cls.get_or_create_basket(brand, brand_number)
        existing = cls.basket_item_model.objects.filter(
            user=user,
            basket=basket,
            part__oe_brand=part.oe_brand or '',
            part__part_number=part.part_number,
        ).select_related('part', 'basket').first()
        if existing:
            return existing, False
        item, created = cls.basket_item_model.objects.get_or_create(
            user=user,
            part=part,
            basket=basket,
            defaults={'group_id': part.group_id or part.product_no or '', 'car': car},
        )
        return item, created

    @classmethod
    def remove_item(cls, item):
        """Remove this Brand + Code line (and any hidden duplicates)."""
        basket_id = item.basket_id
        cls.basket_item_model.objects.filter(
            user=item.user,
            basket_id=item.basket_id,
            part__oe_brand=item.part.oe_brand or '',
            part__part_number=item.part.part_number,
        ).delete()
        if not cls.basket_item_model.objects.filter(basket_id=basket_id).exists():
            cls.basket_model.objects.filter(pk=basket_id).delete()

    @classmethod
    def remove_duplicates(cls, user):
        items = cls.basket_item_model.objects.filter(user=user).order_by('id').values_list(
            'id',
            'part__oe_brand',
            'part__part_number',
            'basket_id',
        )
        seen = set()
        duplicate_ids = []
        for item_id, oe_brand, part_number, basket_id in items:
            key = (oe_brand or '', part_number, basket_id)
            if key in seen:
                duplicate_ids.append(item_id)
            else:
                seen.add(key)

        if not duplicate_ids:
            return 0

        deleted, _ = cls.basket_item_model.objects.filter(user=user, id__in=duplicate_ids).delete()
        cls.prune_empty_baskets()
        return deleted

    @classmethod
    def build_export_workbook(cls, user):
        """
        Export like Cross code example.xlsx Bulk search-result:
        Cross Brand | Cross Code | Brand | Part Number
        Includes one Product Brand / Product No row per family, then each Code.
        """
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = 'Basket'
        sheet.append(['Cross Brand', 'Cross Code', 'Brand', 'Part Number'])

        seen_product_rows = set()
        for item in cls.get_items_queryset(user):
            cross_brand = item.basket.brand
            cross_code = item.basket.brand_number
            product_brand = item.part.brand or ''
            product_no = item.part.product_no or ''
            product_key = (cross_brand, cross_code, product_brand, product_no)
            if product_brand and product_no and product_key not in seen_product_rows:
                seen_product_rows.add(product_key)
                sheet.append([cross_brand, cross_code, product_brand, product_no])
            sheet.append([
                cross_brand,
                cross_code,
                item.part.oe_brand or '',
                item.part.part_number,
            ])
        return workbook

    @classmethod
    def existing_item_keys(cls, user, part_numbers):
        """Keys are (oe_brand, part_number, basket_id)."""
        return {
            (oe_brand or '', part_number, basket_id)
            for oe_brand, part_number, basket_id in cls.basket_item_model.objects.filter(
                user=user,
                part__part_number__in=part_numbers,
            ).values_list('part__oe_brand', 'part__part_number', 'basket_id')
        }

    @classmethod
    def get_group_items_queryset(cls, user, basket_id):
        return cls.basket_item_model.objects.filter(
            user=user,
            basket_id=basket_id,
        ).select_related('basket', 'part').order_by(
            'part__oe_brand', 'part__part_number', '-id',
        ).distinct(
            'part__oe_brand', 'part__part_number',
        )

    @classmethod
    def filter_basket_groups(cls, user, query):
        """Basket page lists unique Brand Name + Brand Number groups."""
        groups = cls.get_user_basket_groups(user)
        query = cls.clean_search_query(query)
        if not query:
            return groups
        q = query.lower()
        return [
            group for group in groups
            if q in (group.brand or '').lower()
            or q in (group.brand_number or '').lower()
        ]

    @classmethod
    def filter_group_items_queryset(cls, user, basket_id, query):
        queryset = cls.get_group_items_queryset(user, basket_id)
        query = cls.clean_search_query(query)
        if not query:
            return queryset
        return queryset.filter(
            Q(part__part_number__icontains=query)
            | Q(part__brand__icontains=query)
            | Q(part__product_no__icontains=query)
            | Q(part__oe_brand__icontains=query)
        )
