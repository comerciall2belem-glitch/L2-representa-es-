import unittest
from unittest.mock import patch
import commercial_homologation as h

class GuardTests(unittest.TestCase):
    def test_disabled_makes_no_connection(self):
        with patch.dict('os.environ',{'L2_COMMERCIAL_SMOKE_SEED':'no'}):
            self.assertFalse(h.seed(lambda:self.fail('Unexpected DB access'),str))
    def test_service_or_database_mismatch_never_connects(self):
        for service,database in [('production',h.DATABASE),(h.SERVICE,'l2one')]:
            with patch.dict('os.environ',{'L2_COMMERCIAL_SMOKE_SEED':'yes','RENDER_SERVICE_ID':service,'L2_EXPECTED_DATABASE':database}):
                with self.assertRaises(RuntimeError):h.seed(lambda:self.fail('Unexpected DB access'),str)
    def test_credentials_required_before_connection(self):
        with patch.dict('os.environ',{'L2_COMMERCIAL_SMOKE_SEED':'yes','RENDER_SERVICE_ID':h.SERVICE,'L2_EXPECTED_DATABASE':h.DATABASE,'L2_SMOKE_MANAGER_PASSWORD':'','L2_SMOKE_REP_PASSWORD':''}):
            with self.assertRaises(RuntimeError):h.seed(lambda:self.fail('Unexpected DB access'),str)
