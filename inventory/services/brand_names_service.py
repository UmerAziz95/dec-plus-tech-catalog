from django.db.models import Count

from inventory.models import PartCrossCode


class BrandNamesServiceCrossCode:
    """
    Manages the distinct brand values used across Cross Code Parts.
    Renaming a brand updates every Part row using it in one pass — useful
    for cleaning up typos/variants (e.g. "Mitsubishi" vs "MITSUBISHI").
    """
    model = PartCrossCode

    @classmethod
    def list_brands(cls, query=''):
        queryset = (
            cls.model.objects
            .exclude(brand='')
            .values('brand')
            .annotate(part_count=Count('id'))
            .order_by('brand')
        )
        query = (query or '').strip()
        if query:
            queryset = queryset.filter(brand__icontains=query)
        return list(queryset)

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
        updated = cls.model.objects.filter(brand=old_name).update(brand=new_name)
        if not updated:
            return False, f'No parts found with brand "{old_name}".', 0
        return True, '', updated
