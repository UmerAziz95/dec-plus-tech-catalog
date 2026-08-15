import re

from django.db.models import Q

from inventory.models import PartCrossCode

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
        query = cls.clean_text(query, 255)
        if query:
            queryset = queryset.filter(
                Q(part_number__icontains=query)
                | Q(brand__icontains=query)
                | Q(product_no__icontains=query)
                | Q(oe_brand__icontains=query)
                | Q(group_id__icontains=query)
            )
        return queryset

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
