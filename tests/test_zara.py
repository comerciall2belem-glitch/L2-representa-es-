import hashlib
import hmac
import json
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import zara


class ZaraRulesTests(unittest.TestCase):
    def test_first_contact_uses_name(self):
        answer, transfer = zara.response_rule('Oi', 'Mariana Silva', True)
        self.assertIn('Mariana', answer)
        self.assertFalse(transfer)

    def test_escalation_precedes_welcome(self):
        for message in ('Quero falar com atendente', 'Reclamação do pedido',
                        'Preciso negociar desconto', 'Falar com Ana Paula'):
            with self.subTest(message=message):
                self.assertEqual(zara.response_rule(message, 'Mariana', True), (zara.HANDOFF, True))

    def test_no_unverified_prices(self):
        self.assertEqual(zara.response_rule('Qual o preço?', '', False)[0], zara.PENDING)
        self.assertIn(zara.PENDING, zara.response_rule('Qual o preço?', 'Mariana', True)[0])

    def test_signature_and_verification(self):
        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(zara.router)
        with patch.dict(os.environ, dict(WA_VERIFY_TOKEN='verify', WA_APP_SECRET='secret',
                                         WA_ACCESS_TOKEN='access', WA_PHONE_NUMBER_ID='123')):
            client = TestClient(app)
            self.assertEqual(client.get('/api/zara/webhook?hub.mode=subscribe&hub.verify_token=verify&hub.challenge=1234').text, '1234')
            self.assertEqual(client.get('/api/zara/webhook?hub.mode=subscribe&hub.verify_token=bad').status_code, 403)
            payload = json.dumps({'entry': []}).encode()
            self.assertEqual(client.post('/api/zara/webhook', content=payload).status_code, 403)
            signature = hmac.new(b'secret', payload, hashlib.sha256).hexdigest()
            self.assertEqual(client.post('/api/zara/webhook', content=payload,
                           headers={'X-Hub-Signature-256': 'sha256=' + signature}).status_code, 200)


if __name__ == '__main__':
    unittest.main()
