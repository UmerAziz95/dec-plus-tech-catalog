import re

from django.db.models.expressions import RawSQL

PART_NUMBER_SANITIZE_REGEX = r'[^A-Za-z0-9]'
_PART_NUMBER_SANITIZE_PATTERN = re.compile(PART_NUMBER_SANITIZE_REGEX)


def sanitize_part_number(value):
    if value is None:
        return ''
    return _PART_NUMBER_SANITIZE_PATTERN.sub('', str(value).strip())


def annotate_part_number_normalized(queryset):
    return queryset.annotate(
        part_number_norm=RawSQL(
            "regexp_replace(part_number, %s, '', 'g')",
            (PART_NUMBER_SANITIZE_REGEX,),
        )
    )
