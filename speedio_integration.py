"""Speedio CNPJ lookup. Credentials stay on the server; no lead is persisted here."""
import base64
import json
import urllib.error
import urllib.parse
import urllib.request

ENDPOINT = 'https://api-get-leads.speedio.com.br/search_enriched_leads/cnpj'


class SpeedioError(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


def lookup_cnpj(cnpj, username, password, opener=None):
    if not username or not password:
        raise SpeedioError(503, 'Credenciais da Speedio não configuradas no servidor')
    if len(cnpj) != 14 or not cnpj.isdigit():
        raise SpeedioError(400, 'Informe um CNPJ válido')
    url = ENDPOINT + '?' + urllib.parse.urlencode({'cnpjs': json.dumps([cnpj])})
    token = base64.b64encode(f'{username}:{password}'.encode()).decode()
    request = urllib.request.Request(url, headers={
        'Authorization': 'Basic ' + token, 'Accept': 'application/json',
    })
    try:
        with (opener or urllib.request.urlopen)(request, timeout=10) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise SpeedioError(502, 'Resposta da Speedio excedeu o limite')
        records = json.loads(raw)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise SpeedioError(502, 'Credenciais da Speedio recusadas') from exc
        if exc.code == 429:
            raise SpeedioError(503, 'Limite de consultas da Speedio atingido; tente mais tarde') from exc
        raise SpeedioError(502, 'Consulta à Speedio indisponível') from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise SpeedioError(502, 'Não foi possível consultar a Speedio') from exc
    if not isinstance(records, list) or not records or not isinstance(records[0], dict):
        raise SpeedioError(404, 'CNPJ não encontrado na Speedio')
    row = next((item for item in records if isinstance(item, dict) and
                ''.join(filter(str.isdigit, str(item.get('cnpj', '')))) == cnpj), None)
    if not row:
        raise SpeedioError(404, 'CNPJ não encontrado na Speedio')
    location = row.get('location') if isinstance(row.get('location'), dict) else {}
    state = str(location.get('uf') or '').upper()
    if state not in ('PA', 'AP'):
        raise SpeedioError(422, 'A empresa consultada não está em PA ou AP')
    name = str(row.get('razao_social') or row.get('nome_fantasia') or '').strip()
    if not name:
        raise SpeedioError(422, 'A Speedio não informou a razão social')
    phones = row.get('telefones') if isinstance(row.get('telefones'), dict) else {}
    validated = phones.get('telefones_validados')
    phone = str(validated[0]) if isinstance(validated, list) and validated else ''
    return {'id': 'speedio:' + cnpj, 'name': name[:180], 'taxId': cnpj,
            'state': state, 'city': str(location.get('city') or '')[:120],
            'contact': '', 'phone': phone[:30], 'origin': 'Speedio'}
