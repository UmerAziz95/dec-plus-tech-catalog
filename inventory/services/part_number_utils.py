import re

from django.db.models.expressions import RawSQL

PART_NUMBER_SANITIZE_REGEX = r'[^A-Za-z0-9]'
_PART_NUMBER_SANITIZE_PATTERN = re.compile(PART_NUMBER_SANITIZE_REGEX)

# Cross Code interactive search strips these (and spaces) but keeps "*".
_CROSSCODE_SYMBOL_PATTERN = re.compile(r'[!@#$%^&()_+=\-{}:"|<>?/.,\\~;\[\]\s]')


def sanitize_part_number(value):
    """Sanitize and uppercase a part number for search matching."""
    if value is None:
        return ''
    return _PART_NUMBER_SANITIZE_PATTERN.sub('', str(value).strip()).upper()


def sanitize_crosscode_search(value, keep_star=True):
    """
    Cross Code search sanitizer.

    Removes symbols !@#$%^&()_+=-{}:"|<>?/.,\\~;][ and spaces.
    Keeps "*" for interactive search when keep_star=True.
    Bulk search uses keep_star=False (and rows that contained "*" are missed).
    """
    if value is None:
        return ''
    text = _CROSSCODE_SYMBOL_PATTERN.sub('', str(value))
    if not keep_star:
        text = text.replace('*', '')
    return text.upper()


def sanitize_crosscode_bulk_part(value):
    """
    Bulk Cross Code part sanitizer.

    Returns (cleaned, had_star). When had_star is True the row must be missed
    automatically — wildcard patterns are not allowed in bulk search.
    """
    if value is None:
        return '', False
    raw = str(value)
    had_star = '*' in raw
    return sanitize_crosscode_search(raw, keep_star=False), had_star


def crosscode_wildcard_to_like(pattern):
    """Turn a sanitized Cross Code pattern with * into a SQL LIKE pattern."""
    parts = str(pattern or '').split('*')
    escaped = []
    for part in parts:
        escaped.append(
            part.replace('\\', '\\\\').replace('%', r'\%').replace('_', r'\_')
        )
    return '%'.join(escaped)


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
