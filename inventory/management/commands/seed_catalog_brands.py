from django.core.management.base import BaseCommand

from inventory.models import Car

TEST_BRANDS = (
    (
        'TOYOTA',
        (
            ('TSTTOY0001', 'TOYOTA COROLLA E210 [TEST01] 2019-2024', 'LHD', 'AT', '2WD', '2ZR-FE', '1800/2WD'),
            ('TSTTOY0002', 'TOYOTA CAMRY XV70 [TEST02] 2018-2024', 'LHD', 'AT', '2WD', '2.5L', '2500/FWD'),
            ('TSTTOY0003', 'TOYOTA RAV4 XA50 [TEST03] 2019-2024', 'LHD', 'CVT', 'AWD', '2.5L', '2500/AWD'),
            ('TSTTOY0004', 'TOYOTA HILUX AN120 [TEST04] 2015-2024', 'RHD', 'MT', '4WD', '2.8D', '2800/4WD'),
            ('TSTTOY0005', 'TOYOTA LAND CRUISER J300 [TEST05] 2022-2024', 'LHD', 'AT', '4WD', '3.5T', '3500/4WD'),
        ),
    ),
    (
        'HONDA',
        (
            ('TSTHON0001', 'HONDA CIVIC FE1 [TEST01] 2022-2024', 'LHD', 'CVT', 'FWD', '1.5T', '1500/FWD'),
            ('TSTHON0002', 'HONDA ACCORD CV3 [TEST02] 2023-2024', 'LHD', 'AT', 'FWD', '1.5T', '1500/FWD'),
            ('TSTHON0003', 'HONDA CR-V RW [TEST03] 2023-2024', 'LHD', 'CVT', 'AWD', '1.5T', '1500/AWD'),
            ('TSTHON0004', 'HONDA FIT GK5 [TEST04] 2014-2020', 'RHD', 'CVT', 'FWD', '1.5L', '1500/FWD'),
        ),
    ),
    (
        'NISSAN',
        (
            ('TSTNIS0001', 'NISSAN ALTIMA L34 [TEST01] 2019-2024', 'LHD', 'CVT', 'FWD', '2.5L', '2500/FWD'),
            ('TSTNIS0002', 'NISSAN X-TRAIL T33 [TEST02] 2022-2024', 'LHD', 'CVT', 'AWD', '2.5L', '2500/AWD'),
            ('TSTNIS0003', 'NISSAN PATROL Y62 [TEST03] 2010-2024', 'RHD', 'AT', '4WD', '5.6L', '5600/4WD'),
            ('TSTNIS0004', 'NISSAN LEAF ZE1 [TEST04] 2018-2024', 'LHD', 'AT', 'FWD', 'EV', 'EV/FWD'),
        ),
    ),
    (
        'FORD',
        (
            ('TSTFOR0001', 'FORD F-150 XIV [TEST01] 2021-2024', 'LHD', 'AT', '4WD', '3.5T', '3500/4WD'),
            ('TSTFOR0002', 'FORD MUSTANG S650 [TEST02] 2024-2024', 'LHD', 'MT', 'RWD', '5.0L', '5000/RWD'),
            ('TSTFOR0003', 'FORD EXPLORER U625 [TEST03] 2020-2024', 'LHD', 'AT', 'AWD', '2.3T', '2300/AWD'),
        ),
    ),
    (
        'BMW',
        (
            ('TSTBMW0001', 'BMW 3 SERIES G20 [TEST01] 2019-2024', 'LHD', 'AT', 'RWD', '330i', '2000/RWD'),
            ('TSTBMW0002', 'BMW X5 G05 [TEST02] 2019-2024', 'LHD', 'AT', 'AWD', 'xDrive40i', '3000/AWD'),
            ('TSTBMW0003', 'BMW M3 G80 [TEST03] 2021-2024', 'LHD', 'AT', 'RWD', 'S58', '3000/RWD'),
        ),
    ),
    (
        'MAZDA',
        (
            ('TSTMAZ0001', 'MAZDA CX-5 KF [TEST01] 2017-2024', 'LHD', 'AT', 'AWD', '2.5L', '2500/AWD'),
            ('TSTMAZ0002', 'MAZDA MX-5 ND [TEST02] 2016-2024', 'LHD', 'MT', 'RWD', '2.0L', '2000/RWD'),
            ('TSTMAZ0003', 'MAZDA3 BP [TEST03] 2019-2024', 'LHD', 'AT', 'FWD', '2.5L', '2500/FWD'),
        ),
    ),
    (
        'SUBARU',
        (
            ('TSTSUB0001', 'SUBARU OUTBACK BT [TEST01] 2020-2024', 'LHD', 'CVT', 'AWD', '2.5L', '2500/AWD'),
            ('TSTSUB0002', 'SUBARU WRX VB [TEST02] 2022-2024', 'LHD', 'MT', 'AWD', '2.4T', '2400/AWD'),
            ('TSTSUB0003', 'SUBARU FORESTER SK [TEST03] 2019-2024', 'LHD', 'CVT', 'AWD', '2.5L', '2500/AWD'),
        ),
    ),
    (
        'HYUNDAI',
        (
            ('TSTHYU0001', 'HYUNDAI TUCSON NX4 [TEST01] 2022-2024', 'LHD', 'AT', 'AWD', '2.5L', '2500/AWD'),
            ('TSTHYU0002', 'HYUNDAI SONATA DN8 [TEST02] 2020-2024', 'LHD', 'AT', 'FWD', '2.5L', '2500/FWD'),
            ('TSTHYU0003', 'HYUNDAI IONIQ 5 [TEST03] 2022-2024', 'LHD', 'AT', 'AWD', 'EV', 'EV/AWD'),
        ),
    ),
    (
        'VOLKSWAGEN',
        (
            ('TSTVWG0001', 'VOLKSWAGEN GOLF MK8 [TEST01] 2020-2024', 'LHD', 'MT', 'FWD', '1.5T', '1500/FWD'),
            ('TSTVWG0002', 'VOLKSWAGEN TIGUAN AD1 [TEST02] 2018-2024', 'LHD', 'AT', 'AWD', '2.0T', '2000/AWD'),
        ),
    ),
    (
        'MERCEDES-BENZ',
        (
            ('TSTMER0001', 'MERCEDES-BENZ C-CLASS W206 [TEST01] 2022-2024', 'LHD', 'AT', 'RWD', 'C300', '2000/RWD'),
            ('TSTMER0002', 'MERCEDES-BENZ GLE W167 [TEST02] 2020-2024', 'LHD', 'AT', 'AWD', 'GLE450', '3000/AWD'),
        ),
    ),
)


class Command(BaseCommand):
    help = 'Insert sample cars for multiple catalog brands (for UI testing).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Delete previously seeded test cars (car_id starts with TST).',
        )

    def handle(self, *args, **options):
        if options['clear']:
            deleted, _ = Car.objects.filter(car_id__startswith='TST').delete()
            self.stdout.write(self.style.WARNING(f'Removed {deleted} test car(s).'))
            return

        created = 0
        skipped = 0
        for _brand, rows in TEST_BRANDS:
            for car_id, car_model, steering, transmission, wd, engine, params in rows:
                _, was_created = Car.objects.get_or_create(
                    car_id=car_id,
                    defaults={
                        'car_model': car_model,
                        'steering': steering,
                        'transmission': transmission,
                        'wd': wd,
                        'engine': engine,
                        'car_parameters': params,
                    },
                )
                if was_created:
                    created += 1
                else:
                    skipped += 1

        brands = sorted({brand for brand, _ in TEST_BRANDS})
        self.stdout.write(self.style.SUCCESS(
            f'Done: {created} created, {skipped} already existed. '
            f'Brands: {", ".join(brands)} (+ MITSUBISHI from import).'
        ))
        self.stdout.write('Reload Cars Catalog to see new brands in the sidebar.')
        self.stdout.write('To remove test data: python manage.py seed_catalog_brands --clear')
