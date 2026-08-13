from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse

from inventory.models import ImportBatch, Part, PartCrossCode
from inventory.services.bulk_search_service import BulkSearchServiceCrossCode
from inventory.services.import_services import ExcelImportService, ImportBatchDeleteService
from inventory.services.part_number_utils import (
    crosscode_wildcard_to_like,
    sanitize_crosscode_bulk_part,
    sanitize_crosscode_search,
    sanitize_part_number,
)
from inventory.services.part_search_service import PartSearchServiceCrossCode

User = get_user_model()


class PartNumberSanitizationTests(SimpleTestCase):
    def test_removes_documented_special_characters(self):
        self.assertEqual(sanitize_part_number('929-00*0=803()21+B'), '92900080321B')

    def test_strips_whitespace(self):
        self.assertEqual(sanitize_part_number('  MQ900871  '), 'MQ900871')

    def test_returns_empty_for_only_special_characters(self):
        self.assertEqual(sanitize_part_number(')(.,:;/?\\|*+-=@!%&#$_'), '')

    def test_strips_at_hash_dollar_symbols(self):
        self.assertEqual(sanitize_part_number('MQ@#$901069'), 'MQ901069')

    def test_strips_underscore(self):
        self.assertEqual(sanitize_part_number('MR_418807'), 'MR418807')

    def test_handles_none(self):
        self.assertEqual(sanitize_part_number(None), '')


class CrossCodeSanitizationTests(SimpleTestCase):
    def test_interactive_strips_symbols_and_spaces_keeps_star(self):
        self.assertEqual(
            sanitize_crosscode_search('43 022*!@#A01', keep_star=True),
            '43022*A01',
        )

    def test_interactive_uppercases(self):
        self.assertEqual(sanitize_crosscode_search('d1086'), 'D1086')

    def test_bulk_removes_star(self):
        cleaned, had_star = sanitize_crosscode_bulk_part('43022*A01')
        self.assertTrue(had_star)
        self.assertEqual(cleaned, '43022A01')

    def test_bulk_no_star(self):
        cleaned, had_star = sanitize_crosscode_bulk_part('d-1086')
        self.assertFalse(had_star)
        self.assertEqual(cleaned, 'D1086')

    def test_wildcard_to_like(self):
        self.assertEqual(crosscode_wildcard_to_like('43022*A01'), '43022%A01')


class CrossCodeSearchRulesTests(TestCase):
    def setUp(self):
        PartCrossCode.objects.create(
            brand='NIBK', product_no='P1', oe_brand='MIT', part_number='D1086', group_id='P1',
        )
        PartCrossCode.objects.create(
            brand='NIBK', product_no='P2', oe_brand='MIT', part_number='D10867418', group_id='P2',
        )
        PartCrossCode.objects.create(
            brand='NIBK', product_no='P3', oe_brand='MIT', part_number='MR955727', group_id='P3',
        )
        PartCrossCode.objects.create(
            brand='NIBK', product_no='P4', oe_brand='MIT', part_number='43022XA01', group_id='P4',
        )
        PartCrossCode.objects.create(
            brand='NIBK', product_no='P5', oe_brand='MIT', part_number='43022YA01', group_id='P5',
        )

    def test_unique_exact_does_not_ask(self):
        needs, candidates, query = PartSearchServiceCrossCode.needs_disambiguation('MR955727')
        self.assertFalse(needs)
        self.assertEqual(query, 'MR955727')
        self.assertEqual(candidates, [])

    def test_prefix_asks_did_you_mean(self):
        needs, candidates, query = PartSearchServiceCrossCode.needs_disambiguation('D1086')
        self.assertTrue(needs)
        self.assertEqual(query, 'D1086')
        norms = {sanitize_part_number(c) for c in candidates}
        self.assertIn('D1086', norms)
        self.assertIn('D10867418', norms)

    def test_wildcard_asks_did_you_mean(self):
        needs, candidates, query = PartSearchServiceCrossCode.needs_disambiguation('43022*A01')
        self.assertTrue(needs)
        self.assertEqual(query, '43022*A01')
        norms = {sanitize_part_number(c) for c in candidates}
        self.assertIn('43022XA01', norms)
        self.assertIn('43022YA01', norms)

    def test_exact_search_after_confirm(self):
        results = PartSearchServiceCrossCode.build_results('D1086')
        self.assertEqual(len(results), 1)
        self.assertEqual(sanitize_part_number(results[0]['part'].part_number), 'D1086')

        results_long = PartSearchServiceCrossCode.build_results('D10867418')
        self.assertEqual(len(results_long), 1)
        self.assertEqual(sanitize_part_number(results_long[0]['part'].part_number), 'D10867418')

    def test_bulk_misses_star_rows_and_exact_matches_only(self):
        rows = [
            {'brand': 'A', 'brand_number': 'B', 'part_number': 'D1086', 'bulk_miss': False},
            {'brand': 'A', 'brand_number': 'B', 'part_number': '43022*A01', 'bulk_miss': True},
            {'brand': 'A', 'brand_number': 'B', 'part_number': 'D10867418', 'bulk_miss': False},
        ]
        # Simulate parse output for star row already flagged; also run through bulk helper path
        cleaned, had_star = sanitize_crosscode_bulk_part('43022*A01')
        self.assertTrue(had_star)

        results, summary, err = BulkSearchServiceCrossCode.run_bulk_search(
            user=None,
            rows_data=rows,
            save_to_basket=False,
        )
        self.assertIsNone(err)
        statuses = {r['part_number']: r['status'] for r in results}
        self.assertEqual(statuses['D1086'], 'Found')
        self.assertEqual(statuses['D10867418'], 'Found')
        self.assertEqual(statuses['43022*A01'], 'Missed (*)')
        self.assertEqual(summary['found_count'], 2)
        self.assertEqual(summary['missed_count'], 1)


class ImportFileHistoryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='hist@example.com', password='Test@123')
        self.other = User.objects.create_user(email='other@example.com', password='Test@123')
        self.client = Client()
        self.client.force_login(self.user)

        self.cars_batch = ImportBatch.objects.create(
            uploaded_by=self.user,
            original_file_name='cars.xlsx',
            import_type=ImportBatch.TYPE_CARS,
            status=ImportBatch.STATUS_COMPLETED,
            total_rows=10,
            cars_count=8,
        )
        self.cc_batch = ImportBatch.objects.create(
            uploaded_by=self.user,
            original_file_name='crosscode.xlsx',
            import_type=ImportBatch.TYPE_PARTS_CROSSCODE,
            status=ImportBatch.STATUS_COMPLETED,
            total_rows=100,
            parts_count=95,
        )
        ImportBatch.objects.create(
            uploaded_by=self.other,
            original_file_name='secret.xlsx',
            import_type=ImportBatch.TYPE_PARTS_CROSSCODE,
            status=ImportBatch.STATUS_COMPLETED,
            total_rows=5,
            parts_count=5,
        )

    def test_batch_helpers(self):
        self.assertEqual(self.cc_batch.imported_records, 95)
        self.assertEqual(self.cc_batch.section_label, 'Cross Code references')
        self.assertEqual(self.cars_batch.section_label, 'Car models')
        self.assertFalse(self.cc_batch.is_deleting)

        self.cc_batch.status = ImportBatch.STATUS_PROCESSING
        self.cc_batch.progress_note = 'Deleting Cross Code parts… (20,000 removed)'
        self.assertTrue(self.cc_batch.is_deleting)
        self.assertEqual(self.cc_batch.display_status, 'Deleting')

    def test_import_page_shows_catalog_history_table(self):
        resp = self.client.get(reverse('inventory:import_data') + '?catalog=crosscode')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'File import history')
        self.assertContains(resp, 'crosscode.xlsx')
        self.assertContains(resp, 'Cross Code references')
        self.assertNotContains(resp, 'cars.xlsx')
        self.assertNotContains(resp, 'secret.xlsx')

        resp_cars = self.client.get(reverse('inventory:import_data') + '?catalog=cars')
        self.assertEqual(resp_cars.status_code, 200)
        self.assertContains(resp_cars, 'cars.xlsx')
        self.assertContains(resp_cars, 'Car models')
        self.assertNotContains(resp_cars, 'crosscode.xlsx')

    def test_history_json_refresh(self):
        resp = self.client.get(reverse('inventory:import_history') + '?catalog=crosscode')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn('table_html', data)
        self.assertIn('crosscode.xlsx', data['table_html'])
        self.assertNotIn('cars.xlsx', data['table_html'])
        self.assertFalse(data['poll'])


class ImportBatchChunkedDeleteTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='del@example.com', password='Test@123')
        self.client = Client()
        self.client.force_login(self.user)
        self.batch = ImportBatch.objects.create(
            uploaded_by=self.user,
            original_file_name='big-crosscode.xlsx',
            import_type=ImportBatch.TYPE_PARTS_CROSSCODE,
            status=ImportBatch.STATUS_COMPLETED,
            total_rows=5,
            parts_count=5,
        )
        PartCrossCode.objects.bulk_create([
            PartCrossCode(
                brand='NIBK',
                product_no=f'P{i}',
                oe_brand='MIT',
                part_number=f'CODE{i}',
                group_id=f'P{i}',
                import_batch=self.batch,
            )
            for i in range(5)
        ])
        # Unrelated row must survive delete.
        PartCrossCode.objects.create(
            brand='KEEP',
            product_no='KEEP1',
            oe_brand='KEEP',
            part_number='KEEPCODE',
            group_id='KEEP1',
        )

    def test_chunked_execute_removes_only_batch_rows(self):
        ImportBatchDeleteService.CHUNK_SIZE = 2  # force multiple chunks
        counts = ImportBatchDeleteService.execute(self.batch.id)
        self.assertEqual(counts['parts'], 5)
        self.assertFalse(ImportBatch.objects.filter(pk=self.batch.id).exists())
        self.assertEqual(PartCrossCode.objects.filter(part_number__startswith='CODE').count(), 0)
        self.assertTrue(PartCrossCode.objects.filter(part_number='KEEPCODE').exists())

    def test_delete_view_enqueues_background_delete(self):
        url = reverse('inventory:import_batch_delete', args=[self.batch.id])
        with patch.object(ImportBatchDeleteService, 'enqueue') as mock_enqueue:
            resp = self.client.post(url, {'catalog': 'crosscode'})
        self.assertEqual(resp.status_code, 302)
        mock_enqueue.assert_called_once_with(self.batch.id)

    def test_enqueue_marks_processing_then_execute_clears(self):
        ImportBatchDeleteService.CHUNK_SIZE = 2
        with patch('inventory.services.import_services.threading.Thread') as mock_thread:
            ImportBatchDeleteService.enqueue(self.batch.id)
            mock_thread.assert_called_once()
            # Do not start background thread; run execute ourselves.
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.status, ImportBatch.STATUS_PROCESSING)
        self.assertTrue(self.batch.is_deleting)

        counts = ImportBatchDeleteService.execute(self.batch.id)
        self.assertEqual(counts['parts'], 5)
        self.assertFalse(ImportBatch.objects.filter(pk=self.batch.id).exists())

    def test_chunked_delete_parts_catalog_batch(self):
        batch = ImportBatch.objects.create(
            uploaded_by=self.user,
            original_file_name='parts.xlsx',
            import_type=ImportBatch.TYPE_PARTS,
            status=ImportBatch.STATUS_COMPLETED,
            total_rows=3,
            parts_count=3,
        )
        Part.objects.bulk_create([
            Part(group_id='G1', brand='B', part_number=f'PN{i}', import_batch=batch)
            for i in range(3)
        ])
        ImportBatchDeleteService.CHUNK_SIZE = 1
        counts = ImportBatchDeleteService.execute(batch.id)
        self.assertEqual(counts['parts'], 3)
        self.assertFalse(ImportBatch.objects.filter(pk=batch.id).exists())
        self.assertEqual(Part.objects.filter(part_number__startswith='PN').count(), 0)


class CrossCodeImportUpsertTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='cc-upsert@example.com', password='pass')
        self.old_batch = ImportBatch.objects.create(
            uploaded_by=self.user,
            original_file_name='old.xlsx',
            import_type=ImportBatch.TYPE_PARTS_CROSSCODE,
            status=ImportBatch.STATUS_COMPLETED,
        )
        self.new_batch = ImportBatch.objects.create(
            uploaded_by=self.user,
            original_file_name='new.xlsx',
            import_type=ImportBatch.TYPE_PARTS_CROSSCODE,
            status=ImportBatch.STATUS_PROCESSING,
        )
        PartCrossCode.objects.create(
            group_id='PN1',
            brand='NIBK',
            product_no='PN1',
            oe_brand='MITSUBISHI',
            part_number='CODE1',
            import_batch=self.old_batch,
        )

    def test_existing_row_is_updated_not_skipped(self):
        service = ExcelImportService()
        rows = [
            {
                'brand': 'NIBK',
                'product_no': 'PN1',
                'oe_brand': 'MITSUBISHI',
                'part_number': 'CODE1',
                'group_id': 'PN1',
            },
            {
                'brand': 'NIBK',
                'product_no': 'PN2',
                'oe_brand': 'TOYOTA',
                'part_number': 'CODE2',
                'group_id': 'PN2',
            },
        ]
        saved = service._upsert_crosscode_parts_batched(rows, self.new_batch, bs=100)
        self.assertEqual(saved, 2)
        self.assertEqual(PartCrossCode.objects.count(), 2)

        existing = PartCrossCode.objects.get(product_no='PN1', part_number='CODE1')
        self.assertEqual(existing.import_batch_id, self.new_batch.id)

        created = PartCrossCode.objects.get(product_no='PN2', part_number='CODE2')
        self.assertEqual(created.import_batch_id, self.new_batch.id)
        self.assertEqual(created.oe_brand, 'TOYOTA')
