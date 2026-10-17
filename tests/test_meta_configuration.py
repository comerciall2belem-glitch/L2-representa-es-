import asyncio
import hashlib
import hmac
import json
import os
from unittest.mock import patch, AsyncMock, Mock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from meta_configuration import value,status
from whatsapp_media import provider_config
import zara

ENV={'WHATSAPP_API_URL':'https://graph.facebook.com/v20.0','WHATSAPP_PHONE_NUMBER_ID':'123456','WHATSAPP_ACCESS_TOKEN':'test-token','WHATSAPP_VERIFY_TOKEN':'test-verify','WHATSAPP_APP_SECRET':'test-secret'}

def test_new_names_and_legacy_aliases():
    assert value('WA_VERIFY_TOKEN',ENV)=='test-verify'
    assert value('WA_GRAPH_VERSION',ENV)=='v20.0'
    assert value('WA_VERIFY_TOKEN',{'WHATSAPP_WEBHOOK_VERIFY_TOKEN':'legacy'})=='legacy'
    assert value('WA_GRAPH_VERSION',{'WHATSAPP_GRAPH_VERSION':'v21.0'})=='v21.0'
    assert status(ENV)['ready']

def test_media_uses_same_configuration_as_webhook():
    with patch.dict(os.environ,ENV,clear=True): assert provider_config()==('test-token','123456','v20.0')

@pytest.mark.parametrize('url',['http://graph.facebook.com/v20.0','https://example.com/v20.0','https://graph.facebook.com/v20.0?token=secret','https://graph.facebook.com.evil.test/v20.0','https://user:secret@graph.facebook.com/v20.0'])
def test_api_url_cannot_redirect_credentials(url):
    env=dict(ENV,WHATSAPP_API_URL=url)
    with pytest.raises(ValueError): value('WA_GRAPH_VERSION',env)
    assert not status(env)['ready']

@pytest.mark.parametrize('key',list(ENV))
def test_missing_official_setting_reports_not_ready(key):
    env=dict(ENV);env.pop(key)
    assert not status(env)['ready']


def client():
    app=FastAPI();app.include_router(zara.router);return TestClient(app)

def test_get_webhook_handshake_uses_requested_verify_variable():
    with patch.dict(os.environ,ENV,clear=True):
        response=client().get('/webhook',params={'hub.mode':'subscribe','hub.verify_token':'test-verify','hub.challenge':'98765'})
        assert response.status_code==200 and response.text=='98765'
        assert client().get('/webhook',params={'hub.mode':'subscribe','hub.verify_token':'wrong','hub.challenge':'98765'}).status_code==403


def test_post_requires_signature_and_accepts_signed_empty_delivery():
    raw=json.dumps({'object':'whatsapp_business_account','entry':[]}).encode()
    signature='sha256='+hmac.new(b'test-secret',raw,hashlib.sha256).hexdigest()
    with patch.dict(os.environ,ENV,clear=True):
        assert client().post('/webhook',content=raw).status_code==403
        assert client().post('/webhook',content=raw,headers={'X-Hub-Signature-256':signature}).status_code==200


def test_active_send_uses_bearer_token_and_official_host_only():
    result=Mock(status_code=200);result.json.return_value={'messages':[{'id':'wamid.test'}]}
    http=AsyncMock();http.post.return_value=result
    factory=Mock();factory.return_value.__aenter__=AsyncMock(return_value=http);factory.return_value.__aexit__=AsyncMock(return_value=None)
    with patch.dict(os.environ,ENV,clear=True),patch('zara.httpx.AsyncClient',factory):
        assert asyncio.run(zara.send('5591999999999','Mensagem de teste'))=='wamid.test'
    args,kwargs=http.post.call_args
    assert args[0]=='https://graph.facebook.com/v20.0/123456/messages'
    assert kwargs['headers']['Authorization']=='Bearer test-token'
