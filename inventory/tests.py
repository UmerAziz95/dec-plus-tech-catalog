from django.test import SimpleTestCase

from inventory.services.part_number_utils import sanitize_part_number


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
