from pathlib import Path

import openpyxl
from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Generate sample Excel import test workbooks for manual import testing.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--output-dir',
            type=str,
            default='',
            help='Directory for generated files (default: <project>/import_test_fixtures)',
        )

    def handle(self, *args, **options):
        output_dir = Path(options['output_dir'] or settings.BASE_DIR / 'import_test_fixtures')
        output_dir.mkdir(parents=True, exist_ok=True)

        generators = [
            self._build_01_valid_minimal,
            self._build_02_valid_alias_headers,
            self._build_03_valid_sheet_name_aliases,
            self._build_04_valid_multi_triplets,
            self._build_05_valid_empty_rows,
            self._build_06_valid_sparse_optional_fields,
            self._build_07_valid_upsert_refresh,
            self._build_08_valid_duplicate_parts,
            self._build_09_invalid_missing_sheet,
            self._build_10_invalid_missing_car_headers,
            self._build_11_invalid_header_not_row1,
            self._build_12_invalid_car_row_missing_id,
            self._build_13_invalid_groups_partial_pair,
            self._build_14_invalid_parts_partial_pair,
            self._build_15_invalid_script_tag,
            self._build_16_edge_orphan_links,
            self._build_17_edge_many_row_errors,
            self._build_18_invalid_corrupt,
            self._build_19_stress_small_batch,
            self._build_20_non_xlsx_csv,
        ]

        created = []
        for builder in generators:
            path = builder(output_dir)
            created.append(path)

        self.stdout.write(self.style.SUCCESS(f'Generated {len(created)} files in {output_dir}'))
        for path in created:
            self.stdout.write(f'  {path.name}')

    def _save_workbook(self, output_dir, filename, sheets):
        path = output_dir / filename
        workbook = openpyxl.Workbook()
        default_sheet = workbook.active
        workbook.remove(default_sheet)

        for sheet_name, rows in sheets:
            worksheet = workbook.create_sheet(title=sheet_name)
            for row in rows:
                worksheet.append(row)

        workbook.save(path)
        return path

    def _cars_headers(self):
        return [
            'CarId',
            'Car Model',
            'Steering',
            'Transmission',
            'WD',
            'Engine',
            'Car Parameters',
            'Additional Note',
        ]

    def _cars_row(self, car_id, model, steering='LHD', transmission='AT', wd='2WD', engine='1.8', parameters='ABS', note=''):
        return [car_id, model, steering, transmission, wd, engine, parameters, note]

    def _groups_header(self):
        row = [''] * 8
        for column, label in (
            (0, 'carId'),
            (1, 'GroupID'),
            (3, 'carId'),
            (4, 'GroupID'),
            (6, 'carId'),
            (7, 'GroupID'),
        ):
            row[column] = label
        return row

    def _parts_header(self):
        row = [''] * 11
        for column, label in (
            (0, 'groupId'),
            (1, 'Brand'),
            (2, 'part_number'),
            (4, 'groupId'),
            (5, 'Brand'),
            (6, 'part_number'),
            (8, 'groupId'),
            (9, 'Brand'),
            (10, 'part_number'),
        ):
            row[column] = label
        return row

    def _groups_sheet(self, *rows):
        return [self._groups_header(), *rows]

    def _parts_sheet(self, *rows):
        return [self._parts_header(), *rows]

    def _groups_row(self, pairs):
        row = [''] * 8
        slots = [(0, 1), (3, 4), (6, 7)]
        for index, (car_id, group_id) in enumerate(pairs[:3]):
            left, right = slots[index]
            row[left] = car_id
            row[right] = group_id
        return row

    def _parts_row(self, triplets):
        row = [''] * 11
        slots = [(0, 1, 2), (4, 5, 6), (8, 9, 10)]
        for index, (group_id, brand, part_number) in enumerate(triplets[:3]):
            start = slots[index][0]
            row[start] = group_id
            row[start + 1] = brand
            row[start + 2] = part_number
        return row

    def _minimal_dataset(self):
        cars = [
            self._cars_headers(),
            self._cars_row('CAR-001', 'Lancer', note='Note A'),
            self._cars_row('CAR-002', 'Outlander', steering='RHD', transmission='MT', wd='4WD', engine='2.4', parameters='Sunroof'),
        ]
        groups = self._groups_sheet(
            self._groups_row([('CAR-001', 'GRP-100'), ('CAR-002', 'GRP-200')]),
        )
        parts = self._parts_sheet(
            self._parts_row([
                ('GRP-100', 'OEM', 'PN-1001'),
                ('GRP-200', 'Aftermarket', 'PN-2001'),
            ]),
        )
        return cars, groups, parts

    def _build_01_valid_minimal(self, output_dir):
        cars, groups, parts = self._minimal_dataset()
        return self._save_workbook(output_dir, '01_valid_minimal.xlsx', [
            ('Cars', cars),
            ('Groups', groups),
            ('Parts', parts),
        ])

    def _build_02_valid_alias_headers(self, output_dir):
        cars = [
            ['car_id', 'model', 'steering', 'transmission', 'wd', 'engine', 'parameters', 'addintional note'],
            self._cars_row('CAR-001', 'Lancer', note='Note A'),
            self._cars_row('CAR-002', 'Outlander', steering='RHD', transmission='MT', wd='4WD', engine='2.4', parameters='Sunroof'),
        ]
        _, groups, parts = self._minimal_dataset()
        return self._save_workbook(output_dir, '02_valid_alias_headers.xlsx', [
            ('Cars', cars),
            ('Groups', groups),
            ('Parts', parts),
        ])

    def _build_03_valid_sheet_name_aliases(self, output_dir):
        cars, groups, parts = self._minimal_dataset()
        return self._save_workbook(output_dir, '03_valid_sheet_name_aliases.xlsx', [
            ('carid', cars),
            ('carId & GroupId', groups),
            ('groupId & part_number', parts),
        ])

    def _build_04_valid_multi_triplets(self, output_dir):
        cars = [
            self._cars_headers(),
            self._cars_row('CAR-001', 'Lancer'),
            self._cars_row('CAR-002', 'Outlander'),
        ]
        groups = self._groups_sheet(
            self._groups_row([
                ('CAR-001', 'GRP-101'),
                ('CAR-001', 'GRP-102'),
                ('CAR-002', 'GRP-103'),
            ]),
        )
        parts = self._parts_sheet(
            self._parts_row([
                ('GRP-101', 'OEM', 'PN-101'),
                ('GRP-102', 'OEM', 'PN-102'),
                ('GRP-103', 'Aftermarket', 'PN-103'),
            ]),
        )
        return self._save_workbook(output_dir, '04_valid_multi_triplets.xlsx', [
            ('Cars', cars),
            ('Groups', groups),
            ('Parts', parts),
        ])

    def _build_05_valid_empty_rows(self, output_dir):
        cars = [
            self._cars_headers(),
            self._cars_row('CAR-001', 'Lancer'),
            ['', '', '', '', '', '', '', ''],
            self._cars_row('CAR-002', 'Outlander'),
        ]
        groups = self._groups_sheet(
            self._groups_row([('CAR-001', 'GRP-100')]),
            ['', '', '', '', '', '', '', ''],
            self._groups_row([('CAR-002', 'GRP-200')]),
        )
        parts = self._parts_sheet(
            self._parts_row([('GRP-100', 'OEM', 'PN-1001')]),
            ['', '', '', '', '', '', '', '', '', '', ''],
            self._parts_row([('GRP-200', 'Aftermarket', 'PN-2001')]),
        )
        return self._save_workbook(output_dir, '05_valid_empty_rows.xlsx', [
            ('Cars', cars),
            ('Groups', groups),
            ('Parts', parts),
        ])

    def _build_06_valid_sparse_optional_fields(self, output_dir):
        cars = [
            self._cars_headers(),
            ['CAR-001', '', '', '', '', '', '', ''],
            ['CAR-002', 'Outlander', '', '', '', '', '', ''],
        ]
        groups = self._groups_sheet(
            self._groups_row([('CAR-001', 'GRP-100'), ('CAR-002', 'GRP-200')]),
        )
        parts = self._parts_sheet(
            self._parts_row([
                ('GRP-100', '', 'PN-1001'),
                ('GRP-200', '', 'PN-2001'),
            ]),
        )
        return self._save_workbook(output_dir, '06_valid_sparse_optional_fields.xlsx', [
            ('Cars', cars),
            ('Groups', groups),
            ('Parts', parts),
        ])

    def _build_07_valid_upsert_refresh(self, output_dir):
        cars = [
            self._cars_headers(),
            self._cars_row('CAR-001', 'Lancer Refresh', steering='RHD', transmission='CVT', wd='AWD', engine='2.0', parameters='Updated', note='Refresh note'),
            self._cars_row('CAR-002', 'Outlander Refresh', steering='LHD', transmission='AT', wd='4WD', engine='3.0', parameters='Updated', note='Refresh note'),
        ]
        groups = self._groups_sheet(
            self._groups_row([('CAR-001', 'GRP-100'), ('CAR-002', 'GRP-200')]),
        )
        parts = self._parts_sheet(
            self._parts_row([
                ('GRP-100', 'OEM', 'PN-1001'),
                ('GRP-200', 'Aftermarket', 'PN-2001'),
            ]),
        )
        return self._save_workbook(output_dir, '07_valid_upsert_refresh.xlsx', [
            ('Cars', cars),
            ('Groups', groups),
            ('Parts', parts),
        ])

    def _build_08_valid_duplicate_parts(self, output_dir):
        cars = [
            self._cars_headers(),
            self._cars_row('CAR-001', 'Lancer'),
        ]
        groups = self._groups_sheet(
            self._groups_row([('CAR-001', 'GRP-100')]),
        )
        parts = self._parts_sheet(
            self._parts_row([('GRP-100', 'OEM', 'PN-DUP')]),
            self._parts_row([('GRP-100', 'AltBrand', 'PN-DUP')]),
        )
        return self._save_workbook(output_dir, '08_valid_duplicate_parts.xlsx', [
            ('Cars', cars),
            ('Groups', groups),
            ('Parts', parts),
        ])

    def _build_09_invalid_missing_sheet(self, output_dir):
        cars, groups, _ = self._minimal_dataset()
        return self._save_workbook(output_dir, '09_invalid_missing_sheet.xlsx', [
            ('Cars', cars),
            ('Groups', groups),
        ])

    def _build_10_invalid_missing_car_headers(self, output_dir):
        cars = [
            ['Car Model', 'Steering', 'Transmission', 'WD', 'Engine', 'Car Parameters', 'Additional Note'],
            self._cars_row('CAR-001', 'Lancer'),
        ]
        _, groups, parts = self._minimal_dataset()
        return self._save_workbook(output_dir, '10_invalid_missing_car_headers.xlsx', [
            ('Cars', cars),
            ('Groups', groups),
            ('Parts', parts),
        ])

    def _build_11_invalid_header_not_row1(self, output_dir):
        cars = [
            ['', '', '', '', '', '', '', ''],
            self._cars_headers(),
            self._cars_row('CAR-001', 'Lancer'),
            self._cars_row('CAR-002', 'Outlander'),
        ]
        _, groups, parts = self._minimal_dataset()
        return self._save_workbook(output_dir, '11_invalid_header_not_row1.xlsx', [
            ('Cars', cars),
            ('Groups', groups),
            ('Parts', parts),
        ])

    def _build_12_invalid_car_row_missing_id(self, output_dir):
        cars = [
            self._cars_headers(),
            self._cars_row('CAR-001', 'Lancer'),
            ['', 'Missing CarId', 'LHD', 'AT', '2WD', '1.8', 'ABS', 'Bad row'],
        ]
        _, groups, parts = self._minimal_dataset()
        return self._save_workbook(output_dir, '12_invalid_car_row_missing_id.xlsx', [
            ('Cars', cars),
            ('Groups', groups),
            ('Parts', parts),
        ])

    def _build_13_invalid_groups_partial_pair(self, output_dir):
        cars, _, parts = self._minimal_dataset()
        groups = self._groups_sheet(
            self._groups_row([('CAR-001', 'GRP-100')]),
            ['CAR-002', '', '', '', '', '', '', ''],
        )
        return self._save_workbook(output_dir, '13_invalid_groups_partial_pair.xlsx', [
            ('Cars', cars),
            ('Groups', groups),
            ('Parts', parts),
        ])

    def _build_14_invalid_parts_partial_pair(self, output_dir):
        cars, groups, _ = self._minimal_dataset()
        parts = self._parts_sheet(
            self._parts_row([('GRP-100', 'OEM', 'PN-1001')]),
            ['GRP-200', 'Aftermarket', '', '', '', '', '', '', '', '', ''],
        )
        return self._save_workbook(output_dir, '14_invalid_parts_partial_pair.xlsx', [
            ('Cars', cars),
            ('Groups', groups),
            ('Parts', parts),
        ])

    def _build_15_invalid_script_tag(self, output_dir):
        cars = [
            self._cars_headers(),
            self._cars_row('CAR-001', '<script>alert(1)</script>'),
        ]
        groups = self._groups_sheet(
            self._groups_row([('CAR-001', 'GRP-100')]),
        )
        parts = self._parts_sheet(
            self._parts_row([('GRP-100', 'OEM', 'PN-1001')]),
        )
        return self._save_workbook(output_dir, '15_invalid_script_tag.xlsx', [
            ('Cars', cars),
            ('Groups', groups),
            ('Parts', parts),
        ])

    def _build_16_edge_orphan_links(self, output_dir):
        cars = [
            self._cars_headers(),
            self._cars_row('CAR-999', 'Ghost Car'),
        ]
        groups = self._groups_sheet(
            self._groups_row([('CAR-001', 'GRP-100')]),
        )
        parts = self._parts_sheet(
            self._parts_row([('GRP-100', 'OEM', 'PN-1001')]),
        )
        return self._save_workbook(output_dir, '16_edge_orphan_links.xlsx', [
            ('Cars', cars),
            ('Groups', groups),
            ('Parts', parts),
        ])

    def _build_17_edge_many_row_errors(self, output_dir):
        cars = [self._cars_headers()]
        for index in range(1, 26):
            cars.append(['', f'Bad row {index}', 'LHD', 'AT', '2WD', '1.8', 'ABS', ''])
        _, groups, parts = self._minimal_dataset()
        return self._save_workbook(output_dir, '17_edge_many_row_errors.xlsx', [
            ('Cars', cars),
            ('Groups', groups),
            ('Parts', parts),
        ])

    def _build_18_invalid_corrupt(self, output_dir):
        path = output_dir / '18_invalid_corrupt.xlsx'
        path.write_bytes(b'NOT_A_REAL_XLSX_FILE')
        return path

    def _build_19_stress_small_batch(self, output_dir):
        cars = [self._cars_headers()]
        groups = [self._groups_header()]
        parts = [self._parts_header()]
        for index in range(1, 121):
            car_id = f'STRESS-{index:03d}'
            group_id = f'STG-{index:03d}'
            part_number = f'SPN-{index:03d}'
            cars.append(self._cars_row(car_id, f'Stress Model {index}', note=f'Row {index}'))
            groups.append(self._groups_row([(car_id, group_id)]))
            parts.append(self._parts_row([(group_id, 'OEM', part_number)]))
        return self._save_workbook(output_dir, '19_stress_small_batch.xlsx', [
            ('Cars', cars),
            ('Groups', groups),
            ('Parts', parts),
        ])

    def _build_20_non_xlsx_csv(self, output_dir):
        path = output_dir / '20_non_xlsx.csv'
        path.write_text(
            'CarId,Car Model,Steering,Transmission,WD,Engine,Car Parameters,Additional Note\n'
            'CAR-001,Lancer,LHD,AT,2WD,1.8,ABS,CSV should fail upload\n',
            encoding='utf-8',
        )
        return path
