import re

from django.db.models import Q

from inventory.models import PartCrossCode
from inventory.services.part_number_utils import (
    crosscode_wildcard_sql,
    crosscode_wildcard_to_like,
    sanitize_crosscode_search,
)

_TAG_RE = re.compile(r'<[^>]+>')


class PartsCatalogServiceCrossCode:
    """
    Standalone parts catalog for Cross Code — unlike Cross Car (where parts
    are only ever edited inline from search results), Cross Code's "Edit
    Parts Numbers" page browses/edits Part records directly, independent of
    any car match.
    """
    model = PartCrossCode

    @staticmethod
    def clean_text(value, max_length):
        cleaned = _TAG_RE.sub('', (value or '').strip())
        return cleaned[:max_length]

    @classmethod
    def build_queryset(cls, query):
        queryset = cls.model.objects.all().order_by('part_number', 'id')
        raw = cls.clean_text(query, 255)
        search = sanitize_crosscode_search(raw, keep_star=True)
        if not search:
            return queryset

        if '*' in search:
            like = crosscode_wildcard_to_like(search)
            where_sql, params = crosscode_wildcard_sql(
                like, ('code', 'product_no', 'brand', 'oe_brand'),
            )
            return queryset.extra(where=[where_sql], params=params)

        return queryset.filter(
            Q(part_number__icontains=raw)
            | Q(brand__icontains=raw)
            | Q(product_no__icontains=raw)
            | Q(oe_brand__icontains=raw)
            | Q(group_id__icontains=raw)
        )

    SUGGEST_FIELDS = ('part_number', 'product_no', 'brand', 'oe_brand')

    @classmethod
    def suggest_values(cls, query, limit=40):
        """Autocomplete Product Brand, Product No, Brand, and Code while typing."""
        raw = cls.clean_text(query, 255)
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
            for field in cls.SUGGEST_FIELDS:
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
    def update_part(cls, part, payload):
        """
        Update one Cross Code row. Product Brand can be changed here for
        single-row edits from search; use Brand Names for bulk renames.
        """
        part_number = cls.clean_text(payload.get('part_number', ''), 100)
        brand = cls.clean_text(payload.get('brand', part.brand or ''), 100)
        product_no = cls.clean_text(payload.get('product_no', part.product_no or ''), 100)
        oe_brand = cls.clean_text(payload.get('oe_brand', part.oe_brand or ''), 100)

        if not part_number:
            return False, 'Code is required.'
        if not brand:
            return False, 'Product Brand is required.'
        if not product_no:
            return False, 'Product No is required.'

        part.part_number = part_number
        part.brand = brand
        part.product_no = product_no
        part.oe_brand = oe_brand
        part.group_id = product_no
        part.save(update_fields=[
            'part_number', 'brand', 'product_no', 'oe_brand', 'group_id', 'updated_at',
        ])
        return True, ''

    @classmethod
    def delete_part(cls, part):
        part_number = part.part_number
        part.delete()
        return part_number
