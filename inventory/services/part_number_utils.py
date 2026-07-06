import re

from django.db.models.expressions import RawSQL

PART_NUMBER_SANITIZE_REGEX = r'[^A-Za-z0-9]'
_PART_NUMBER_SANITIZE_PATTERN = re.compile(PART_NUMBER_SANITIZE_REGEX)


def sanitize_part_number(value):
    """Sanitize and uppercase a part number for search matching."""
    if value is None:
        return ''
    return _PART_NUMBER_SANITIZE_PATTERN.sub('', str(value).strip()).upper()


def annotate_part_number_normalized(queryset):
    """
    Annotate queryset with part_number_norm using the stored generated column.
    The column is maintained automatically by PostgreSQL and indexed with
    both B-tree (exact match) and GIN trigram (substring match).
    """
    return queryset.annotate(
        part_number_norm=RawSQL("part_number_norm", []),
    )


def filter_parts_exact(query, model=None):
    """
    Filter parts by exact normalized match using the stored generated column.
    Uses the B-tree index on part_number_norm — instant on 135M rows.
    """
    if model is None:
        from inventory.models import Part
        model = Part
    return model.objects.extra(
        where=["part_number_norm = %s"],
        params=[query],
    ).order_by('part_number', 'group_id').distinct('part_number', 'group_id')


def filter_parts_contains(query, model=None):
    """
    Filter parts by substring match using the stored generated column.
    Uses the GIN trigram index on part_number_norm — fast on 135M rows.
    """
    if model is None:
        from inventory.models import Part
        model = Part
    return model.objects.extra(
        where=["part_number_norm ILIKE %s"],
        params=[f'%{query}%'],
    ).order_by('part_number', 'group_id').distinct('part_number', 'group_id')
