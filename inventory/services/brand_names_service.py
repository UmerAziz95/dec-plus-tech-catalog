from django.db.models import Count
from django.utils import timezone

from inventory.models import PartCrossCode


class BrandNamesServiceCrossCode:
    """
    Unique brand values from Cross Code Excel imports stored on PartCrossCode.

    - product_brand → ``brand`` column (Excel "Product Brand", e.g. NIBK)
    - oe_brand → ``oe_brand`` column (Excel "Brand", e.g. MITSUBISHI)

    Renaming updates every matching row in one pass.
    """
    model = PartCrossCode
    FIELD_PRODUCT_BRAND = 'product_brand'
    FIELD_OE_BRAND = 'oe_brand'
    FIELD_MAP = {
        FIELD_PRODUCT_BRAND: 'brand',
        FIELD_OE_BRAND: 'oe_brand',
    }

    @classmethod
    def resolve_db_field(cls, field):
        field = (field or cls.FIELD_PRODUCT_BRAND).strip().lower()
        return cls.FIELD_MAP.get(field, 'brand'), (
            field if field in cls.FIELD_MAP else cls.FIELD_PRODUCT_BRAND
        )

    @classmethod
    def list_brands(cls, query='', field=FIELD_PRODUCT_BRAND):
        db_field, _ = cls.resolve_db_field(field)
        queryset = (
            cls.model.objects
            .exclude(**{db_field: ''})
            .values(db_field)
            .annotate(part_count=Count('id'))
            .order_by(db_field)
        )
        query = (query or '').strip()
        if query:
            queryset = queryset.filter(**{f'{db_field}__icontains': query})

        rows = []
        for item in queryset:
            rows.append({
                'brand': item[db_field],
                'part_count': item['part_count'],
            })
        return rows

    @classmethod
    def rename_brand(cls, old_name, new_name, field=FIELD_PRODUCT_BRAND):
        db_field, kind = cls.resolve_db_field(field)
        old_name = (old_name or '').strip()
        new_name = (new_name or '').strip()
        label = 'Product Brand' if kind == cls.FIELD_PRODUCT_BRAND else 'Brand'

        if not old_name:
            return False, f'Original {label} is required.', 0
        if not new_name:
            return False, f'New {label} is required.', 0
        if old_name == new_name:
            return False, f'New {label} must be different from the current one.', 0

        updated = cls.model.objects.filter(**{db_field: old_name}).update(
            **{db_field: new_name, 'updated_at': timezone.now()},
        )
        if not updated:
            return False, f'No Cross Code rows found with {label} "{old_name}".', 0
        return True, '', updated
