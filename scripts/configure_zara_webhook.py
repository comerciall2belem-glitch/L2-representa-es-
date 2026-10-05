"""Configure Meta webhooks from existing environment secrets; never send messages.

Run in the service environment:
    python scripts/configure_zara_webhook.py --apply

Requires WHATSAPP_APP_ID, WHATSAPP_BUSINESS_ACCOUNT_ID and the five integration
variables. Uses Meta Application Subscriptions and WABA subscribed_apps APIs.
References:
https://developers.facebook.com/docs/graph-api/reference/application/subscriptions/
https://www.postman.com/meta/whatsapp-business-platform/folder/ozgs3jn/webhook-subscriptions
"""
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sys
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from zara import configured_value

CALLBACK = 'https://l2-one-homologacao.onrender.com/api/zara/webhook'
EXPECTED_PHONE = '5591980142628'


class SetupError(Exception):
    pass


def request_json(client, method, url, token, data=None):
    response = client.request(method, url, headers={'Authorization': 'Bearer ' + token},
                              data=data)
    try:
        result = response.json()
    except ValueError:
        raise SetupError('Resposta inválida da Meta; configuração não confirmada.') from None
    if not response.is_success:
        error = result.get('error', {}) if isinstance(result, dict) else {}
        code = error.get('code')
        # Never print server error messages, tokens, URLs or raw payloads.
        safe_code = str(code) if isinstance(code, int) else 'não informado'
        raise SetupError(f'Meta recusou a operação (HTTP {response.status_code}, código {safe_code}).')
    return result


def check_endpoint(client, verify, secret):
    challenge = 'zara-integration-probe'
    result = client.get(CALLBACK, params={'hub.mode': 'subscribe',
        'hub.verify_token': verify, 'hub.challenge': challenge})
    if result.status_code != 200 or result.text != challenge:
        raise SetupError(f'Webhook GET ainda não validado (HTTP {result.status_code}).')
    rejected = client.get(CALLBACK, params={'hub.mode': 'subscribe',
        'hub.verify_token': verify + '-invalid', 'hub.challenge': challenge})
    if rejected.status_code != 403:
        raise SetupError('Webhook não rejeitou token inválido.')
    raw = b'{"object":"whatsapp_business_account","entry":[]}'
    signature = 'sha256=' + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    result = client.post(CALLBACK, content=raw, headers={
        'Content-Type':'application/json', 'X-Hub-Signature-256':signature})
    if result.status_code != 200 or result.json() != {'ok': True}:
        raise SetupError(f'Webhook POST assinado ainda não validado (HTTP {result.status_code}).')
    rejected = client.post(CALLBACK, content=raw, headers={
        'Content-Type':'application/json', 'X-Hub-Signature-256':'sha256=' + '0'*64})
    if rejected.status_code != 403:
        raise SetupError('Webhook não rejeitou assinatura inválida.')
    print('GET, POST assinado e rejeição de credenciais inválidas: OK.')


def run(apply=False):
    values = {key: configured_value(key) for key in (
        'WA_ACCESS_TOKEN','WA_PHONE_NUMBER_ID','WA_GRAPH_VERSION','WA_VERIFY_TOKEN','WA_APP_SECRET')}
    missing = [key for key, value in values.items() if not value]
    if missing:
        raise SetupError('Faltam credenciais reais no ambiente: ' + ', '.join(missing))
    version = values['WA_GRAPH_VERSION']
    if not re.fullmatch(r'v\d+\.\d+', version):
        raise SetupError('Versão da Graph inválida.')
    phone_id = values['WA_PHONE_NUMBER_ID']
    if not re.fullmatch(r'[0-9]+', phone_id):
        raise SetupError('ID do número inválido.')
    graph = 'https://graph.facebook.com/' + version
    token = values['WA_ACCESS_TOKEN']
    app_id = os.getenv('WHATSAPP_APP_ID', '').strip()
    waba_id = os.getenv('WHATSAPP_BUSINESS_ACCOUNT_ID', '').strip()
    with httpx.Client(timeout=20, follow_redirects=False) as client:
        check_endpoint(client, values['WA_VERIFY_TOKEN'], values['WA_APP_SECRET'])
        phone = request_json(client, 'GET', graph + '/' + phone_id + '?fields=id,display_phone_number', token)
        if re.sub(r'\D', '', phone.get('display_phone_number', '')) != EXPECTED_PHONE:
            raise SetupError('O ID informado não corresponde ao número autorizado da L2.')
        print('Número autorizado da L2 conferido na Meta.')
        if not apply:
            print('Diagnóstico concluído. Nenhuma inscrição foi alterada; E2E físico ainda pendente.')
            return
        if not re.fullmatch(r'[0-9]+', app_id) or not re.fullmatch(r'[0-9]+', waba_id):
            raise SetupError('Para registrar via API faltam WHATSAPP_APP_ID e WHATSAPP_BUSINESS_ACCOUNT_ID.')
        # Validate that the configured phone belongs to this WABA before mutations.
        phones = request_json(client, 'GET', graph + '/' + waba_id + '/phone_numbers?fields=id', token)
        if not any(item.get('id') == phone_id for item in phones.get('data', [])):
            raise SetupError('O número não pertence à conta WhatsApp informada.')
        app_token = app_id + '|' + values['WA_APP_SECRET']
        subscription_url = graph + '/' + app_id + '/subscriptions'
        existing = request_json(client, 'GET', subscription_url, app_token)
        for item in existing.get('data', []):
            if item.get('object') == 'whatsapp_business_account' and item.get('callback_url') != CALLBACK:
                raise SetupError('O aplicativo já usa outro webhook; use aplicativo exclusivo de homologação.')
        result = request_json(client, 'POST', subscription_url, app_token, {
            'object':'whatsapp_business_account', 'callback_url':CALLBACK,
            'verify_token':values['WA_VERIFY_TOKEN'], 'fields':'messages'})
        if result.get('success') not in (True, 'true'):
            raise SetupError('A Meta não confirmou a inscrição do aplicativo.')
        subscriptions = request_json(client, 'GET', subscription_url, app_token)
        registered = any(item.get('object') == 'whatsapp_business_account'
            and item.get('callback_url') == CALLBACK
            and any((field.get('name') if isinstance(field, dict) else field) == 'messages'
                    for field in item.get('fields', [])) for item in subscriptions.get('data', []))
        if not registered:
            raise SetupError('Inscrição do aplicativo não confirmada pela leitura da Meta.')
        result = request_json(client, 'POST', graph + '/' + waba_id + '/subscribed_apps', token)
        if result.get('success') not in (True, 'true'):
            raise SetupError('A Meta não confirmou a inscrição da conta WhatsApp.')
        apps = request_json(client, 'GET', graph + '/' + waba_id + '/subscribed_apps', token)
        if not any(item.get('whatsapp_business_api_data', {}).get('id') == app_id
                   or item.get('id') == app_id for item in apps.get('data', [])):
            raise SetupError('Inscrição da conta não confirmada pela leitura da Meta.')
        print('Webhook e inscrição messages registrados e conferidos. Teste no aparelho ainda pendente.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Configuração automatizada da Zara sem expor segredos.')
    parser.add_argument('--apply', action='store_true', help='Registrar o webhook e a conta na Meta.')
    args = parser.parse_args()
    try:
        run(args.apply)
    except SetupError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    except Exception:
        print('Não foi possível concluir a conexão. Nenhum segredo foi exibido; configuração não confirmada.',
              file=sys.stderr)
        sys.exit(1)
