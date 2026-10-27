import unittest
from staging_guard import validate_database_target

class StagingGuardTests(unittest.TestCase):
    def test_expected_database_accepts_matching_url(self):
        validate_database_target('postgresql://u:p@host:5432/l2_one_next_staging_db', 'l2_one_next_staging_db')

    def test_rejects_production_database(self):
        with self.assertRaisesRegex(RuntimeError, 'ambiente esperado'):
            validate_database_target('postgresql://u:p@host:5432/l2one', 'l2_one_next_staging_db')

    def test_invalid_url_fails_closed(self):
        for value in ('', 'sqlite:///l2_one_next_staging_db', 'postgresql:///l2_one_next_staging_db'):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                validate_database_target(value, 'l2_one_next_staging_db')

    def test_no_declared_expectation_preserves_existing_behavior(self):
        validate_database_target('', '')

if __name__ == '__main__':
    unittest.main()
