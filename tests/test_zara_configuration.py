import os
import unittest
from unittest.mock import patch
import zara


class ConfigurationTests(unittest.TestCase):
    def test_render_aliases_and_canonical_precedence(self):
        with patch.dict(os.environ, {}, clear=True):
            for canonical, alias in zara.SETTING_ALIASES.items():
                os.environ[alias] = ' alias-value '
                self.assertEqual(zara.setting(canonical), 'alias-value')
                os.environ[canonical] = ' canonical-value '
                self.assertEqual(zara.setting(canonical), 'canonical-value')
                del os.environ[canonical]

    def test_configuration_logs_only_presence(self):
        with patch.dict(os.environ, {'WHATSAPP_ACCESS_TOKEN': 'private-token',
                                     'WHATSAPP_APP_SECRET': 'private-secret'}, clear=True):
            with self.assertLogs(zara.logger, level='INFO') as logs:
                zara.log_configuration()
            output = '\n'.join(logs.output)
            self.assertNotIn('private-token', output)
            self.assertNotIn('private-secret', output)
            self.assertIn('wa_app_secret_configured', output)
