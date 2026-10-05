"""Signed loopback-only webhook simulation, or fully isolated mock smoke test."""
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys
import uuid
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def payload(phone_id='123'):
    return {'object': 'whatsapp_business_account', 'entry': [{'id': 'mock-account',
        'changes': [{'field': 'messages', 'value': {
            'messaging_product': 'whatsapp', 'metadata': {'phone_number_id': phone_id},
            'contacts': [{'wa_id': '5591999999999', 'profile': {'name': 'Cliente Teste'}}],
            'messages': [{'from': '5591999999999', 'id': 'wamid.mock.' + uuid.uuid4().hex,
                          'type': 'text', 'text': {'body': 'Oi'}}]}}]}]}


def signed(data, secret):
    raw = json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode()
    signature = 'sha256=' + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return raw, {'Content-Type': 'application/json', 'X-Hub-Signature-256': signature}


class MemoryDB:
    """Test-only SQL stub. Never loaded by the production server."""
    def __init__(self):
        self.messages = {}
        self.mode = 'bot'
        self.row = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, args=()):
        self.row = None
        if 'INSERT INTO zara_messages' in sql:
            mid, phone, body = args[:3]
            if mid not in self.messages:
                self.messages[mid] = {'body': body, 'direction': 'out' if "'out'" in sql else 'in'}
                self.row = (mid,)
        elif 'SELECT mode' in sql:
            self.row = (self.mode,)
        elif 'SELECT count' in sql:
            self.row = (sum(x['direction'] == 'in' for x in self.messages.values()),)
        elif "SET mode='human'" in sql:
            self.mode = 'human'
        return self

    def fetchone(self):
        return self.row


def mock_smoke():
    from unittest.mock import patch
    import httpx
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import zara
    app = FastAPI()
    app.include_router(zara.router)
    store = MemoryDB()
    requests = []

    def graph(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={'messages': [{'id': 'wamid.mock.reply'}]})

    real_client = httpx.AsyncClient
    def fake_client(**kwargs):
        return real_client(transport=httpx.MockTransport(graph), **kwargs)

    env = {'WA_VERIFY_TOKEN': 'mock-verify', 'WA_APP_SECRET': 'mock-secret',
           'WA_ACCESS_TOKEN': 'mock-access', 'WA_PHONE_NUMBER_ID': '123', 'OPENAI_API_KEY': ''}
    with patch.dict(os.environ, env), patch.object(zara, 'db', return_value=store), \
            patch.object(zara.httpx, 'AsyncClient', side_effect=fake_client):
        client = TestClient(app)
        verify = client.get('/api/zara/webhook', params={'hub.mode': 'subscribe',
            'hub.verify_token': 'mock-verify', 'hub.challenge': '1234'})
        assert verify.status_code == 200 and verify.text == '1234'
        raw, headers = signed(payload(), 'mock-secret')
        response = client.post('/api/zara/webhook', content=raw, headers=headers)
        assert response.status_code == 200, response.text
        assert len(requests) == 1 and len(store.messages) == 2
        assert 'Zara' in requests[0]['text']['body']
        assert client.post('/api/zara/webhook', content=raw, headers=headers).status_code == 200
        assert len(requests) == 1
    print('OK: verificação → assinatura → entrada registrada → resposta Zara → Graph mock → deduplicação')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', help='Webhook local; sem esta opção usa mocks sem rede externa')
    args = parser.parse_args()
    if not args.url:
        mock_smoke()
    else:
        import httpx
        url = urlparse(args.url)
        if url.hostname not in ('127.0.0.1', 'localhost', '::1') or url.scheme != 'http':
            parser.error('Somente URL HTTP de loopback é permitida para evitar envios em produção')
        raw, headers = signed(payload(os.environ['WA_PHONE_NUMBER_ID']), os.environ['WA_APP_SECRET'])
        response = httpx.post(args.url, content=raw, headers=headers, timeout=30, follow_redirects=False)
        print('HTTP', response.status_code)
        response.raise_for_status()

