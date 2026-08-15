import re

import openpyxl
from django.db.models import Q
from django.db.models.expressions import RawSQL

from inventory.models import Car

_BRAND_NAME_SQL = "NULLIF(TRIM(split_part(NULLIF(TRIM(car_model), ''), ' ', 1)), '')"
_TAG_RE = re.compile(r'<[^>]+>')
_TOKEN_RE = re.compile(r'[^\s]+')

_SEARCH_FIELDS = (
    'car_id',
    'car_model',
    'engine',
    'transmission',
    'steering',
    'wd',
    'car_parameters',
)


class CarCatalogService:
    FILTER_FIELDS = (
        'brand',
        'q',
        'model',
        'engine',
        'transmission',
        'steering',
        'wd',
        'parameters',
    )

    @staticmethod
    def clean_text(value, max_length):
        cleaned = _TAG_RE.sub('', (value or '').strip())
        return cleaned[:max_length]

    @classmethod
    def tokenize_query(cls, query):
        """Split a search string into whitespace tokens (same rules as manual car-model search)."""
        query = cls.clean_text(query, 255)
        if not query:
            return []
        return _TOKEN_RE.findall(query)

    @classmethod
    def apply_keyword_tokens(cls, queryset, query, fields=_SEARCH_FIELDS):
        """
        Require every token to appear somewhere in the given fields.

        Tokens are AND-ed and order-independent, so "TOYOTA UZZ" matches
        rows that contain both keywords even when other words sit between them.
        """
        tokens = cls.tokenize_query(query)
        if not tokens:
            return queryset

        for token in tokens:
            token_q = Q()
            for field in fields:
                token_q |= Q(**{f'{field}__icontains': token})
            queryset = queryset.filter(token_q)
        return queryset

    @classmethod
    def get_brands(cls):
        return list(
            Car.objects.annotate(
                brand_name=RawSQL(_BRAND_NAME_SQL, []),
            )
            .exclude(brand_name__isnull=True)
            .exclude(brand_name='')
            .values_list('brand_name', flat=True)
            .distinct()
            .order_by('brand_name')
        )

    @classmethod
    def parse_filters(cls, querydict):
        return {
            'brand': cls.clean_text(querydict.get('brand', ''), 100),
            'q': cls.clean_text(querydict.get('q', ''), 255),
            'model': cls.clean_text(querydict.get('model', ''), 255),
            'engine': cls.clean_text(querydict.get('engine', ''), 255),
            'transmission': cls.clean_text(querydict.get('transmission', ''), 50),
            'steering': cls.clean_text(querydict.get('steering', ''), 50),
            'wd': cls.clean_text(querydict.get('wd', ''), 50),
            'parameters': cls.clean_text(querydict.get('parameters', ''), 255),
        }

    @classmethod
    def has_search_criteria(cls, filters):
        return any(filters.get(field) for field in cls.FILTER_FIELDS)

    @classmethod
    def build_queryset(cls, filters):
        queryset = Car.objects.all().order_by('car_id')
        brand = filters.get('brand', '')
        if brand:
            queryset = queryset.filter(
                Q(car_model__istartswith=f'{brand} ') | Q(car_model__iexact=brand)
            )

        query = filters.get('q', '')
        if query:
            queryset = cls.apply_keyword_tokens(queryset, query, fields=_SEARCH_FIELDS)

        model = filters.get('model', '')
        if model:
            queryset = cls.apply_keyword_tokens(queryset, model, fields=('car_model',))

        engine = filters.get('engine', '')
        if engine:
            queryset = queryset.filter(engine__icontains=engine)

        transmission = filters.get('transmission', '')
        if transmission:
            queryset = queryset.filter(transmission__icontains=transmission)

        steering = filters.get('steering', '')
        if steering:
            queryset = queryset.filter(steering__icontains=steering)

        wd = filters.get('wd', '')
        if wd:
            queryset = queryset.filter(wd__icontains=wd)

        parameters = filters.get('parameters', '')
        if parameters:
            queryset = queryset.filter(car_parameters__icontains=parameters)

        return queryset

    @classmethod
    def get_suggestions(cls, filters, query, limit=15):
        """
        Autocomplete suggestions using the same non-contiguous keyword rules
        as Import Data → Add a part manually car-model search.
        """
        query = cls.clean_text(query, 255)
        if len(query) < 1:
            return []

        # Apply brand/other filters, but not the live typing query twice.
        base_filters = dict(filters or {})
        base_filters['q'] = ''
        queryset = cls.build_queryset(base_filters)
        queryset = cls.apply_keyword_tokens(
            queryset,
            query,
            fields=('car_model', 'car_id'),
        )
        return list(
            queryset.exclude(car_model='')
            .values_list('car_model', flat=True)
            .distinct()
            .order_by('car_model')[:limit]
        )

    @staticmethod
    def build_export_workbook(cars):
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = 'Cars'
        sheet.append([
            'CarId',
            'Car Model',
            'Steering',
            'Transmission',
            'WD',
            'Engine',
            'Car Parameters',
            'Additional Note',
        ])
        for car in cars.iterator():
            sheet.append([
                car.car_id,
                car.car_model,
                car.steering,
                car.transmission,
                car.wd,
                car.engine,
                car.car_parameters,
                car.additional_note,
            ])
        return workbook

    @classmethod
    def update_car(cls, car, payload):
        car_model = cls.clean_text(payload.get('car_model', ''), 500)
        if not car_model:
            return False, 'Car model is required.'

        car.car_model = car_model
        car.steering = cls.clean_text(payload.get('steering', ''), 50)
        car.transmission = cls.clean_text(payload.get('transmission', ''), 50)
        car.wd = cls.clean_text(payload.get('wd', ''), 50)
        car.engine = cls.clean_text(payload.get('engine', ''), 255)
        car.car_parameters = cls.clean_text(payload.get('car_parameters', ''), 2000)
        car.additional_note = cls.clean_text(payload.get('additional_note', ''), 2000)
        car.save()
        return True, ''
