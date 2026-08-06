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
    def suggest_car_models(cls, query, limit=50):
        query = cls.clean_text(query, 255)
        if len(query) < 1:
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


class ManualEntryServiceCrossCode:
    """
    Manual add for Cross Code's Product Brand / Product No / Brand / Code rows.
    """
    part_cls = PartCrossCode

    @staticmethod
    def clean_text(value, max_length):
        cleaned = _TAG_RE.sub('', (value or '').strip())
        return cleaned[:max_length]

    @classmethod
    def suggest_product_nos(cls, query, limit=50):
        query = cls.clean_text(query, 255)
        if len(query) < 1:
            return []
        return list(
            cls.part_cls.objects
            .filter(product_no__icontains=query)
            .exclude(product_no='')
            .values_list('product_no', flat=True)
            .distinct()
            .order_by('product_no')[:limit]
        )

    @classmethod
    def suggest_product_brands(cls, query, limit=50):
        query = cls.clean_text(query, 255)
        if len(query) < 1:
            return []
        return list(
            cls.part_cls.objects
            .filter(brand__icontains=query)
            .exclude(brand='')
            .values_list('brand', flat=True)
            .distinct()
            .order_by('brand')[:limit]
        )

    @classmethod
    def add_cross_code_row(cls, product_brand, product_no, oe_brand, code):
        product_brand = cls.clean_text(product_brand, 100)
        product_no = cls.clean_text(product_no, 100)
        oe_brand = cls.clean_text(oe_brand, 100)
        code = cls.clean_text(code, 100)

        if not product_brand:
            return False, 'Product Brand is required.'
        if not product_no:
            return False, 'Product No is required.'
        if not code:
            return False, 'Code is required.'

        _part, created = cls.part_cls.objects.get_or_create(
            brand=product_brand,
            product_no=product_no,
            oe_brand=oe_brand,
            part_number=code,
            defaults={'group_id': product_no},
        )
        if not created:
            return False, f'Row already exists: {product_brand} / {product_no} / {oe_brand or "—"} / {code}.'
        return True, ''

    # Kept so older car-model suggestion URL still responds safely.
    @classmethod
    def suggest_car_models(cls, query, limit=50):
        return cls.suggest_product_nos(query, limit=limit)

    @classmethod
    def add_part_to_car(cls, car_model_text, part_number, brand=''):
        # Legacy signature: treat car_model as Product No and brand as Product Brand.
        return cls.add_cross_code_row(
            product_brand=brand or 'MANUAL',
            product_no=car_model_text,
            oe_brand='',
            code=part_number,
        )
