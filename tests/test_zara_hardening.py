import asyncio
import json
import os
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
import zara
from scripts.simulate_zara import MemoryDB, payload, signed, mock_smoke


class HardeningTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'WA_VERIFY_TOKEN': 'verify', 'WA_APP_SECRET': 'secret',
            'WA_PHONE_NUMBER_ID': '123', 'WA_ACCESS_TOKEN': 'access'}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        app = FastAPI()
        app.include_router(zara.router)
        @app.get('/health')
        def health():
            return {'ok': True}
        self.client = TestClient(app)

    def post(self, data, path='/api/zara/webhook'):
        raw, headers = signed(data, 'secret')
        return self.client.post(path, content=raw, headers=headers)

    def test_verify_only_needs_verify_token(self):
        del os.environ['WA_ACCESS_TOKEN']
        del os.environ['WA_APP_SECRET']
        for path in ('/webhook', '/api/zara/webhook'):
            r = self.client.get(path, params={'hub.mode': 'subscribe', 'hub.verify_token': 'verify', 'hub.challenge': 'abcd'})
            self.assertEqual((r.status_code, r.text), (200, 'abcd'))
            for token in ('', 'wrong', 'é'):
                self.assertEqual(self.client.get(path, params={'hub.mode': 'subscribe', 'hub.verify_token': token}).status_code, 403)
        self.assertEqual(self.client.get('/webhook', params={'hub.mode': 'subscribe', 'hub.verify_token': 'verify'}).status_code, 400)
        os.environ['WA_VERIFY_TOKEN'] = ''
        self.assertEqual(self.client.get('/webhook').status_code, 503)

    def test_signatures_and_tampering(self):
        raw, headers = signed({'entry': []}, 'secret')
        self.assertEqual(self.client.post('/webhook', content=raw, headers=headers).status_code, 200)
        for signature in ('', 'sha1=abc', 'sha256=' + '0'*64, 'sha256=invalid'):
            r = self.client.post('/webhook', content=raw, headers={'X-Hub-Signature-256': signature})
            self.assertEqual(r.status_code, 403)
        self.assertEqual(self.client.post('/webhook', content=raw + b' ', headers=headers).status_code, 403)
        self.assertEqual(self.post({'entry': []}, '/webhook').status_code, 200)
        del os.environ['WA_APP_SECRET']
        self.assertEqual(self.post({'entry': []}).status_code, 503)

    def test_payload_shape_and_size(self):
        for data in ([], None, {'entry': None}, {'entry': [None]}, {'entry': [{'changes': [{'value': None}]}]}):
            self.assertEqual(self.post(data).status_code, 400)
        self.assertEqual(self.post({'padding': 'a'*256001}).status_code, 413)
        raw, headers = signed({}, 'secret')
        import hashlib, hmac
        raw = b'{bad'
        headers['X-Hub-Signature-256'] = 'sha256=' + hmac.new(b'secret', raw, hashlib.sha256).hexdigest()
        self.assertEqual(self.client.post('/webhook', content=raw, headers=headers).status_code, 400)

    def test_wrong_phone_and_status_events_do_not_send(self):
        with patch.object(zara, 'send') as send, patch.object(zara, 'db') as db:
            self.assertEqual(self.post(payload('999')).status_code, 200)
            data = payload(); value = data['entry'][0]['changes'][0]['value']
            value.pop('messages'); value['statuses'] = [{'status': 'delivered'}]
            self.assertEqual(self.post(data).status_code, 200)
            send.assert_not_called(); db.assert_not_called()

    def test_inbound_failure_returns_retryable_status(self):
        with patch.object(zara, 'db', side_effect=RuntimeError('database down')):
            self.assertEqual(self.post(payload()).status_code, 503)
        self.assertEqual(self.client.get('/health').status_code, 200)

    def test_send_failure_preserves_inbound_and_hands_off(self):
        store = MemoryDB()
        with patch.object(zara, 'db', return_value=store), patch.object(zara, 'send', side_effect=zara.WhatsAppSendError('failed')):
            self.assertEqual(self.post(payload()).status_code, 200)
        self.assertEqual(len(store.messages), 1)
        self.assertEqual(store.mode, 'human')
        self.assertEqual(self.client.get('/health').status_code, 200)

    def test_full_mock_flow(self):
        mock_smoke()

    def test_graph_failures_and_redacted_logs(self):
        real_client = httpx.AsyncClient
        for status, data in ((401, {'error': {'code': 190, 'message': 'access secret'}}),
                             (429, {}), (500, {}), (200, {}), (200, {'messages': []}), (200, {'messages': [{'id': None}]})):
            def client(**kwargs):
                return real_client(transport=httpx.MockTransport(lambda req: httpx.Response(status, json=data)), **kwargs)
            with patch.object(zara.httpx, 'AsyncClient', side_effect=client), self.assertLogs(zara.logger, level='INFO') as logs:
                with self.assertRaises(zara.WhatsAppSendError):
                    asyncio.run(zara.send('5591999999999', 'Private message'))
            output = '\n'.join(logs.output)
            self.assertNotIn('Private message', output)
            self.assertNotIn('5591999999999', output)
            self.assertNotIn('access secret', output)
            self.assertIn('ERROR', output)

    def test_timeout_and_invalid_json(self):
        real_client = httpx.AsyncClient
        def timeout(req):
            raise httpx.ReadTimeout('access secret', request=req)
        for handler in (timeout, lambda req: httpx.Response(200, text='not json')):
            def client(**kwargs):
                return real_client(transport=httpx.MockTransport(handler), **kwargs)
            with patch.object(zara.httpx, 'AsyncClient', side_effect=client):
                with self.assertRaises(zara.WhatsAppSendError):
                    asyncio.run(zara.send('5591999999999', 'Oi'))

