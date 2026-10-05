import json
import os
import unittest
from unittest.mock import patch
import httpx
from scripts import configure_zara_webhook as setup


ENV = {'WHATSAPP_ACCESS_TOKEN':'private-access', 'WHATSAPP_PHONE_NUMBER_ID':'123',
       'WHATSAPP_GRAPH_VERSION':'v23.0', 'WHATSAPP_WEBHOOK_VERIFY_TOKEN':'private-verify',
       'WHATSAPP_APP_SECRET':'private-secret', 'WHATSAPP_APP_ID':'456',
       'WHATSAPP_BUSINESS_ACCOUNT_ID':'789'}


class SetupTests(unittest.TestCase):
    def harness(self, wrong_phone=False, wrong_callback=False):
        mutations = []
        state = {'registered':False}
        def handler(req):
            path = req.url.path
            if req.url.host == 'l2-one-homologacao.onrender.com':
                if req.method == 'GET':
                    if req.url.params.get('hub.verify_token') != 'private-verify':
                        return httpx.Response(403)
                    return httpx.Response(200, text=req.url.params['hub.challenge'])
                if req.headers.get('X-Hub-Signature-256') == 'sha256=' + '0'*64:
                    return httpx.Response(403)
                return httpx.Response(200, json={'ok':True})
            if req.method == 'POST':
                mutations.append(path)
                state['registered'] = True
                return httpx.Response(200,json={'success':True})
            if path.endswith('/123'):
                return httpx.Response(200,json={'id':'123', 'display_phone_number':
                    '+55 91 98014-2628' if not wrong_phone else '+55 11 99999-9999'})
            if path.endswith('/phone_numbers'):
                return httpx.Response(200,json={'data':[{'id':'123'}]})
            if path.endswith('/subscriptions'):
                data = [{'object':'whatsapp_business_account','callback_url':
                    'https://other.example/webhook' if wrong_callback else setup.CALLBACK,
                    'fields':[{'name':'messages'}]}] if state['registered'] or wrong_callback else []
                return httpx.Response(200,json={'data':data})
            return httpx.Response(200,json={'data':[{'whatsapp_business_api_data':{'id':'456'}}]})
        real_client = httpx.Client
        client = real_client(transport=httpx.MockTransport(handler))
        return client, mutations

    def test_missing_secrets_never_connect(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(setup.httpx,'Client') as client:
            with self.assertRaises(setup.SetupError): setup.run(True)
            client.assert_not_called()

    def test_apply_checks_phone_then_registers_and_reads_back(self):
        client, mutations = self.harness()
        with patch.dict(os.environ, ENV, clear=True), patch.object(setup.httpx,'Client',return_value=client):
            setup.run(True)
        self.assertEqual(mutations, ['/v23.0/456/subscriptions','/v23.0/789/subscribed_apps'])

    def test_wrong_phone_does_not_change_meta(self):
        client, mutations = self.harness(wrong_phone=True)
        with patch.dict(os.environ, ENV, clear=True), patch.object(setup.httpx,'Client',return_value=client):
            with self.assertRaises(setup.SetupError): setup.run(True)
        self.assertEqual(mutations, [])

    def test_existing_other_callback_is_preserved(self):
        client, mutations = self.harness(wrong_callback=True)
        with patch.dict(os.environ, ENV, clear=True), patch.object(setup.httpx,'Client',return_value=client):
            with self.assertRaises(setup.SetupError): setup.run(True)
        self.assertEqual(mutations, [])

    def test_meta_errors_do_not_expose_payload(self):
        with httpx.Client(transport=httpx.MockTransport(lambda request:
                httpx.Response(401,json={'error':{'code':190,'message':'private-access private-secret'}}))) as client:
            with self.assertRaises(setup.SetupError) as error:
                setup.request_json(client,'GET','https://graph.facebook.com/v23.0/123','private-access')
        self.assertNotIn('private-access',str(error.exception))
        self.assertNotIn('private-secret',str(error.exception))
