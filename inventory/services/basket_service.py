import re

import openpyxl
from django.db.models import Count, Q

from inventory.models import Basket, BasketItem

_TAG_RE = re.compile(r'<[^>]+>')


class BasketService:
    BASKET_PAGE_SIZE = 20

    @staticmethod
    def normalize_cross_brand_fields(brand, brand_number):
        return (brand or '').strip(), (brand_number or '').strip()

    @staticmethod
    def count_for_user(user):
        return BasketService.get_items_queryset(user).count()

    @staticmethod
    def get_or_create_basket(brand, brand_number):
        brand, brand_number = BasketService.normalize_cross_brand_fields(brand, brand_number)
        return Basket.objects.get_or_create(
            brand=brand,
            brand_number=brand_number,
        )

    @staticmethod
    def get_items_queryset(user):
        """Return basket items with duplicates consolidated.
        Uses PostgreSQL DISTINCT ON to keep only one row per unique
        (brand, brand_number, part_number, car_model) combination."""
        return BasketItem.objects.filter(
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

    @staticmethod
    def filter_items_queryset(user, query):
        queryset = BasketService.get_items_queryset(user)
        query = BasketService.clean_search_query(query)
        if not query:
            return queryset
        return queryset.filter(
            Q(basket__brand__icontains=query)
            | Q(basket__brand_number__icontains=query)
            | Q(part__part_number__icontains=query)
            | Q(car__car_model__icontains=query)
            | Q(car__car_id__icontains=query)
        )

    @staticmethod
    def item_exists(user, car, part, brand, brand_number):
        brand, brand_number = BasketService.normalize_cross_brand_fields(brand, brand_number)
        return BasketItem.objects.filter(
            user=user,
            car=car,
            part=part,
            basket__brand=brand,
            basket__brand_number=brand_number,
        ).exists()

    @staticmethod
    def add_item(user, car, part, brand, brand_number):
        brand, brand_number = BasketService.normalize_cross_brand_fields(brand, brand_number)
        basket, _created = BasketService.get_or_create_basket(brand, brand_number)
        item, created = BasketItem.objects.get_or_create(
            user=user,
            car=car,
            part=part,
            basket=basket,
            defaults={'group_id': part.group_id}
        )
        return item, created

    @staticmethod
    def prune_empty_baskets():
        orphan_ids = Basket.objects.annotate(
            item_count=Count('items'),
        ).filter(item_count=0).values_list('pk', flat=True)
        if orphan_ids:
            Basket.objects.filter(pk__in=list(orphan_ids)).delete()

    @staticmethod
    def remove_item(item):
        """Remove an item and all its hidden duplicates sharing the same
        brand, brand_number, part_number, and car_model."""
        basket_id = item.basket_id
        # Delete all underlying copies of this consolidated row
        BasketItem.objects.filter(
            user=item.user,
            basket_id=item.basket_id,
            part__part_number=item.part.part_number,
            car__car_model=item.car.car_model,
        ).delete()
        if not BasketItem.objects.filter(basket_id=basket_id).exists():
            Basket.objects.filter(pk=basket_id).delete()

    @staticmethod
    def remove_duplicates(user):
        items = BasketItem.objects.filter(user=user).order_by('id').values_list(
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

        deleted, _ = BasketItem.objects.filter(user=user, id__in=duplicate_ids).delete()
        BasketService.prune_empty_baskets()
        return deleted

    @staticmethod
    def clear_basket(user):
        basket_ids = list(
            BasketItem.objects.filter(user=user).values_list('basket_id', flat=True).distinct()
        )
        deleted, _ = BasketItem.objects.filter(user=user).delete()
        if basket_ids:
            Basket.objects.annotate(item_count=Count('items')).filter(
                pk__in=basket_ids,
                item_count=0,
            ).delete()
        return deleted

    @staticmethod
    def build_export_workbook(user):
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = 'Basket'
        sheet.append(['Cross Brand', 'Cross Code', 'OE Number', 'Car Model'])
        # Use the consolidated queryset so export matches what the user sees
        for item in BasketService.get_items_queryset(user):
            sheet.append([
                item.basket.brand,
                item.basket.brand_number,
                item.part.part_number,
                item.car.car_model or '',
            ])
        return workbook

    @staticmethod
    def get_or_create_baskets_for_pairs(brand_pairs):
        basket_map = {}
        for brand, brand_number in brand_pairs:
            normalized = BasketService.normalize_cross_brand_fields(brand, brand_number)
            basket, _ = BasketService.get_or_create_basket(*normalized)
            basket_map[normalized] = basket
        return basket_map

    @staticmethod
    def existing_item_keys(user, part_numbers):
        return {
            (car_id, part_number, basket_id)
            for car_id, part_number, basket_id in BasketItem.objects.filter(
                user=user,
                part__part_number__in=part_numbers,
            ).values_list('car_id', 'part__part_number', 'basket_id')
        }

    @staticmethod
    def get_user_basket_groups(user):
        basket_ids = (
            BasketItem.objects.filter(user=user)
            .values_list('basket_id', flat=True)
            .distinct()
        )
        baskets = list(
            Basket.objects.filter(pk__in=basket_ids)
            .order_by('brand', 'brand_number')
        )
        # Attach consolidated item counts using the grouped queryset
        for basket in baskets:
            basket.item_count = BasketService.get_group_items_queryset(user, basket.pk).count()
        return baskets

    @staticmethod
    def get_group_items_queryset(user, basket_id):
        """Return group items with duplicates consolidated.
        Uses PostgreSQL DISTINCT ON to keep only one row per unique
        (part_number, car_model) combination within a basket group."""
        return BasketItem.objects.filter(
            user=user,
            basket_id=basket_id,
        ).select_related('basket', 'car', 'part').order_by(
            'part__part_number', 'car__car_model', '-id',
        ).distinct(
            'part__part_number', 'car__car_model',
        )

    @staticmethod
    def filter_group_items_queryset(user, basket_id, query):
        queryset = BasketService.get_group_items_queryset(user, basket_id)
        query = BasketService.clean_search_query(query)
        if not query:
            return queryset
        return queryset.filter(
            Q(basket__brand__icontains=query)
            | Q(basket__brand_number__icontains=query)
            | Q(part__part_number__icontains=query)
            | Q(car__car_model__icontains=query)
            | Q(car__car_id__icontains=query)
        )

    @staticmethod
    def user_owns_basket_group(user, basket_id):
        return BasketItem.objects.filter(user=user, basket_id=basket_id).exists()

    @staticmethod
    def parse_page_number(value, default=1):
        try:
            page = int(value)
        except (TypeError, ValueError):
            return default
        return max(1, page)

    @staticmethod
    def page_after_delete(requested_page, total_count, per_page=None):
        if per_page is None:
            per_page = BasketService.BASKET_PAGE_SIZE
        page = BasketService.parse_page_number(requested_page)
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
