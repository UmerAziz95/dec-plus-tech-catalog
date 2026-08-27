from django.db.models import Count, F, Q
from django.utils import timezone

from inventory.models import PartCrossCode
from inventory.services.part_number_utils import (
    crosscode_wildcard_sql,
    crosscode_wildcard_to_like,
    sanitize_crosscode_search,
)


class BrandNamesServiceCrossCode:
    """
    Unique brand names from Cross Code Excel imports on PartCrossCode.

    Names come from both columns:
    - ``brand`` → Excel "Product Brand" (e.g. NIBK)
    - ``oe_brand`` → Excel "Brand" (e.g. MITSUBISHI)

    The list shows each unique name with how many catalog rows use it.
    Renaming updates every matching row in both columns.
    """
    model = PartCrossCode

    @classmethod
    def _filter_name(cls, queryset, field, query):
        raw = (query or '').strip()
        search = sanitize_crosscode_search(raw, keep_star=True)
        if not search:
            return queryset
        if '*' in search:
            like = crosscode_wildcard_to_like(search)
            where_sql, params = crosscode_wildcard_sql(like, (field,))
            return queryset.extra(where=[where_sql], params=params)
        return queryset.filter(**{f'{field}__icontains': raw})

    @classmethod
    def list_rows(cls, query=''):
        """Unique brand names with the number of Cross Code rows that use each name."""
        query = (query or '').strip()

        brand_qs = cls._filter_name(cls.model.objects.exclude(brand=''), 'brand', query)
        oe_qs = cls._filter_name(cls.model.objects.exclude(oe_brand=''), 'oe_brand', query)

        counts = {}
        for name, row_count in brand_qs.values(name=F('brand')).annotate(
            row_count=Count('id'),
        ).values_list('name', 'row_count'):
            counts[name] = counts.get(name, 0) + row_count
        for name, row_count in oe_qs.values(name=F('oe_brand')).annotate(
            row_count=Count('id'),
        ).values_list('name', 'row_count'):
            counts[name] = counts.get(name, 0) + row_count

        return [
            {'name': name, 'row_count': counts[name]}
            for name in sorted(counts, key=lambda value: value.casefold())
        ]

    @classmethod
    def suggest_names(cls, query, limit=40):
        """Autocomplete unique brand names while typing."""
        raw = (query or '').strip()
        if not sanitize_crosscode_search(raw, keep_star=False):
            return []

        suggestions = []
        seen = set()

        def add_value(value):
            text = (value or '').strip()
            if not text:
                return False
            key = text.casefold()
            if key in seen:
                return False
            seen.add(key)
            suggestions.append(text)
            return len(suggestions) >= limit

        def collect(lookup):
            for field in ('brand', 'oe_brand'):
                rows = (
                    cls.model.objects.exclude(**{field: ''})
                    .filter(**{f'{field}__{lookup}': raw})
                    .order_by(field)
                    .values_list(field, flat=True)
                    .distinct()[:limit]
                )
                for value in rows:
                    if add_value(value):
                        return True
            return False

        if collect('istartswith'):
            return suggestions
        collect('icontains')
        return suggestions

    @classmethod
    def rename_brand(cls, old_name, new_name):
        old_name = (old_name or '').strip()
        new_name = (new_name or '').strip()

        if not old_name:
            return False, 'Original brand name is required.', 0
        if not new_name:
            return False, 'New brand name is required.', 0
        if old_name == new_name:
            return False, 'New brand name must be different from the current one.', 0

        matching = cls.model.objects.filter(Q(brand=old_name) | Q(oe_brand=old_name))
        updated_count = matching.count()
        if not updated_count:
            return False, f'No Cross Code rows found with brand name "{old_name}".', 0

        now = timezone.now()
        cls.model.objects.filter(brand=old_name).update(brand=new_name, updated_at=now)
        cls.model.objects.filter(oe_brand=old_name).update(oe_brand=new_name, updated_at=now)
        return True, '', updated_count
