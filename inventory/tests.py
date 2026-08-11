from django.test import SimpleTestCase, TestCase

from inventory.models import PartCrossCode
from inventory.services.bulk_search_service import BulkSearchServiceCrossCode
from inventory.services.part_number_utils import (
    crosscode_wildcard_to_like,
    sanitize_crosscode_bulk_part,
    sanitize_crosscode_search,
    sanitize_part_number,
)
from inventory.services.part_search_service import PartSearchServiceCrossCode


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
