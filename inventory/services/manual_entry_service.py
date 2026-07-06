import re

from inventory.models import Car, CarCrossCode, CarGroup, CarGroupCrossCode, Part, PartCrossCode

_TAG_RE = re.compile(r'<[^>]+>')


class ManualEntryService:
    """
    Backs the Import Data page's manual "add one part to an existing car"
    form. The car must already exist (imported beforehand) — this never
    creates a new car, only attaches a part number to one found by its
    exact car model text, via a dedicated group reserved for manual entries
    so re-adding parts to the same car keeps reusing that one group.
    """
    car_model_cls = Car
    car_group_cls = CarGroup
    part_cls = Part
    GROUP_PREFIX = 'MANUAL'

    @staticmethod
    def clean_text(value, max_length):
        cleaned = _TAG_RE.sub('', (value or '').strip())
        return cleaned[:max_length]

    @classmethod
    def suggest_car_models(cls, query, limit=15):
        query = cls.clean_text(query, 255)
        if len(query) < 2:
            return []
        return list(
            cls.car_model_cls.objects
            .filter(car_model__icontains=query)
            .exclude(car_model='')
            .values_list('car_model', flat=True)
            .distinct()
            .order_by('car_model')[:limit]
        )

    @classmethod
    def add_part_to_car(cls, car_model_text, part_number, brand=''):
        car_model_text = cls.clean_text(car_model_text, 500)
        part_number = cls.clean_text(part_number, 100)
        brand = cls.clean_text(brand, 100)

        if not car_model_text:
            return False, 'Car model is required.'
        if not part_number:
            return False, 'Part number is required.'

        car = cls.car_model_cls.objects.filter(car_model__iexact=car_model_text).first()
        if not car:
            return False, f'Car model "{car_model_text}" was not found. Import its car model first, then add parts to it.'

        group_id = f'{cls.GROUP_PREFIX}{car.id}'
        cls.car_group_cls.objects.get_or_create(car_id=str(car.id), group_id=group_id)

        _part, created = cls.part_cls.objects.get_or_create(
            group_id=group_id, part_number=part_number, defaults={'brand': brand},
        )
        if not created:
            return False, f'Part "{part_number}" already exists for "{car_model_text}".'
        return True, ''


class ManualEntryServiceCrossCode(ManualEntryService):
    car_model_cls = CarCrossCode
    car_group_cls = CarGroupCrossCode
    part_cls = PartCrossCode
    GROUP_PREFIX = 'MANUALCC'
