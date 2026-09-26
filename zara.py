"""Zara: inbound WhatsApp Cloud API support with explicit human handoff."""
import hashlib
import hmac
import os
import re

import httpx
from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

router = APIRouter()

WELCOME = ('Olá{nome}! Tudo bem? Me chamo *Zara* e sou a assistente virtual da '
           'L2 Representações. Estou à disposição enquanto a Ana Paula não retorna.\n\n'
           'Como posso te ajudar hoje? 😊')
HANDOFF = ('Entendi perfeitamente! Para te dar o melhor atendimento nesse caso, '
           'vou te conectar agora mesmo com um de nossos especialistas. Só um instante '
           'que já vão te atender por aqui!')
PENDING = 'Vou verificar essa informação com a equipe e retornaremos por aqui assim que estiver disponível.'
PROMPT = ("Você é Zara, assistente virtual da L2 Representações. Responda em português do Brasil, "
          "com frases curtas e tom profissional, natural e amigável. Use *negrito* do WhatsApp "
          "quando útil. Nunca invente catálogo, preços, horários, disponibilidade, prazo, "
          "status de pedido ou política comercial. Você não tem acesso a dados da empresa. "
          "Dê apenas orientações gerais; para informação específica diga que a equipe verificará "
          "e retornará. Não solicite documentos ou dados sensíveis. Limite a resposta a 500 caracteres.")


def setup(con):
    con.execute('''CREATE TABLE IF NOT EXISTS zara_conversations (
        phone TEXT PRIMARY KEY, name TEXT, mode TEXT NOT NULL DEFAULT 'bot',
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CHECK (mode IN ('bot','human')))''')
    con.execute('''CREATE TABLE IF NOT EXISTS zara_messages (
        message_id TEXT PRIMARY KEY, phone TEXT NOT NULL REFERENCES zara_conversations(phone),
        direction TEXT NOT NULL, body TEXT NOT NULL, delivered BOOLEAN NOT NULL DEFAULT false,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), CHECK (direction IN ('in','out')))''')
    con.execute('CREATE INDEX IF NOT EXISTS zara_messages_phone_idx ON zara_messages(phone,created_at DESC)')


def db():
    from server import db as server_db
    return server_db()


def admin(authorization):
    from server import auth
    if auth(authorization) != 'Ana Paula':
        raise HTTPException(403, 'Acesso restrito à administradora')


def config():
    keys = ('WA_VERIFY_TOKEN', 'WA_APP_SECRET', 'WA_ACCESS_TOKEN', 'WA_PHONE_NUMBER_ID')
    values = [os.getenv(k, '') for k in keys]
    if not all(values):
        raise HTTPException(503, 'Integração WhatsApp ainda não configurada')
    return values


def normalize(value):
    return ''.join(ch for ch in value.lower() if not re.match(r'[\u0300-\u036f]', ch))


def requires_handoff(text):
    t = normalize(text)
    return bool(re.search(r'\b(atendente|representante|humano|pessoa|reclamac\w*|problema|erro|'
                          r'faturamento|nota fiscal|devoluc\w*|troca|cancelamento|desconto|'
                          r'condic\w*|prazo de pagamento|orcamento personalizado|negociac\w*|'
                          r'falar com (ana paula|vendedor\w*|equipe))\b', t))


def response_rule(text, name, first):
    from unicodedata import normalize as unicode_normalize
    t = ''.join(c for c in unicode_normalize('NFD', text.lower()) if ord(c) < 0x300 or ord(c) > 0x36f)
    if requires_handoff(t):
        return HANDOFF, True
    greeting = WELCOME.format(nome=f', {name.split()[0]}' if name else '')
    asks_lookup = bool(re.search(r'\b(pedido|rastreio|catalogo|preco|valor|horario|estoque|disponibilidade)\b', t))
    if first:
        return greeting + ('\n\n' + PENDING if asks_lookup else ''), False
    if asks_lookup:
        return PENDING, False
    return None, False


async def ai_answer(text):
    key = os.getenv('OPENAI_API_KEY', '')
    if not key:
        return PENDING
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            result = await client.post('https://api.openai.com/v1/responses',
                headers={'Authorization': f'Bearer {key}'},
                json={'model': os.getenv('ZARA_MODEL', 'gpt-4.1-mini'), 'instructions': PROMPT,
                      'input': text[:1000], 'max_output_tokens': 180, 'store': False})
            result.raise_for_status()
            parts = [item.get('text', '') for output in result.json().get('output', [])
                     for item in output.get('content', []) if item.get('type') == 'output_text']
            answer = ' '.join(parts).strip()
            return answer[:500] if answer else PENDING
    except (httpx.HTTPError, KeyError, ValueError):
        return PENDING


async def send(phone, body):
    _, _, token, phone_id = config()
    version = os.getenv('WA_GRAPH_VERSION', 'v23.0')
    async with httpx.AsyncClient(timeout=12) as client:
        result = await client.post(f'https://graph.facebook.com/{version}/{phone_id}/messages',
            headers={'Authorization': f'Bearer {token}'},
            json={'messaging_product': 'whatsapp', 'to': phone, 'type': 'text',
                  'text': {'body': body[:4096]}})
        result.raise_for_status()
        return result.json()['messages'][0]['id']


@router.get('/api/zara/webhook')
def verify_webhook(mode: str | None = Query(None, alias='hub.mode'),
                   token: str | None = Query(None, alias='hub.verify_token'),
                   challenge: str | None = Query(None, alias='hub.challenge')):
    verify, _, _, _ = config()
    if mode != 'subscribe' or not token or not hmac.compare_digest(token, verify):
        raise HTTPException(403)
    return __import__('fastapi').responses.PlainTextResponse(challenge or '')


@router.post('/api/zara/webhook')
async def receive_webhook(request: Request, x_hub_signature_256: str | None = Header(None)):
    _, secret, _, expected_id = config()
    raw = await request.body()
    signature = 'sha256=' + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    if not x_hub_signature_256 or not hmac.compare_digest(signature, x_hub_signature_256):
        raise HTTPException(403)
    if len(raw) > 256_000:
        raise HTTPException(413)
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(400)
    for entry in payload.get('entry', []):
        for change in entry.get('changes', []):
            value = change.get('value', {})
            if str(value.get('metadata', {}).get('phone_number_id')) != expected_id:
                continue
            contacts = {c.get('wa_id'): c.get('profile', {}).get('name', '') for c in value.get('contacts', [])}
            for msg in value.get('messages', []):
                phone, mid = str(msg.get('from', '')), str(msg.get('id', ''))
                if not re.fullmatch(r'\d{8,16}', phone) or not mid or len(mid) > 256:
                    continue
                body = (msg.get('text', {}).get('body', '') if msg.get('type') == 'text' else '')[:4000]
                if not body:
                    body = '[Mensagem não textual recebida]'
                name = str(contacts.get(phone, ''))[:120]
                with db() as con:
                    con.execute('''INSERT INTO zara_conversations(phone,name) VALUES(%s,%s)
                        ON CONFLICT(phone) DO UPDATE SET name=COALESCE(NULLIF(EXCLUDED.name,''),zara_conversations.name),updated_at=now()''', (phone, name))
                    inserted = con.execute('''INSERT INTO zara_messages(message_id,phone,direction,body)
                        VALUES(%s,%s,'in',%s) ON CONFLICT DO NOTHING RETURNING message_id''', (mid, phone, body)).fetchone()
                    if not inserted:
                        continue
                    mode = con.execute('SELECT mode FROM zara_conversations WHERE phone=%s FOR UPDATE', (phone,)).fetchone()[0]
                    first = con.execute("SELECT count(*) FROM zara_messages WHERE phone=%s AND direction='in'", (phone,)).fetchone()[0] == 1
                    rule, handoff = response_rule(body, name, first)
                    if not body or body == '[Mensagem não textual recebida]':
                        rule, handoff = HANDOFF, True
                    if handoff:
                        con.execute("UPDATE zara_conversations SET mode='human' WHERE phone=%s", (phone,))
                if mode == 'human':
                    continue
                answer = rule or await ai_answer(body)
                try:
                    out_id = await send(phone, answer)
                except (httpx.HTTPError, KeyError):
                    # Keep the inbound record for the team to inspect and replay safely.
                    continue
                with db() as con:
                    con.execute('''INSERT INTO zara_messages(message_id,phone,direction,body,delivered)
                        VALUES(%s,%s,'out',%s,true) ON CONFLICT DO NOTHING''', (out_id, phone, answer))
    return {'ok': True}


@router.get('/api/zara/conversations')
def conversations(authorization: str | None = Header(None)):
    admin(authorization)
    with db() as con:
        rows = con.execute('''SELECT phone,name,mode,updated_at FROM zara_conversations
                              ORDER BY updated_at DESC LIMIT 100''').fetchall()
    return [{'phone': p, 'name': n, 'mode': m, 'updatedAt': d.isoformat()} for p,n,m,d in rows]


@router.get('/api/zara/conversations/{phone}')
def conversation(phone: str, authorization: str | None = Header(None)):
    admin(authorization)
    with db() as con:
        rows = con.execute('''SELECT message_id,direction,body,created_at FROM zara_messages
                              WHERE phone=%s ORDER BY created_at DESC LIMIT 100''', (phone,)).fetchall()
    return [{'id': i, 'direction': d, 'body': b, 'at': at.isoformat()} for i,d,b,at in reversed(rows)]


@router.post('/api/zara/conversations/{phone}/resume')
def resume(phone: str, authorization: str | None = Header(None)):
    admin(authorization)
    with db() as con:
        row = con.execute("UPDATE zara_conversations SET mode='bot',updated_at=now() WHERE phone=%s RETURNING phone", (phone,)).fetchone()
    if not row:
        raise HTTPException(404)
    return {'ok': True}


@router.post('/api/zara/conversations/{phone}/takeover')
def takeover(phone: str, authorization: str | None = Header(None)):
    admin(authorization)
    with db() as con:
        row = con.execute("UPDATE zara_conversations SET mode='human',updated_at=now() WHERE phone=%s RETURNING phone", (phone,)).fetchone()
    if not row:
        raise HTTPException(404)
    return {'ok': True}


class HumanReply(BaseModel):
    text: str = Field(min_length=1, max_length=4096)


@router.post('/api/zara/conversations/{phone}/reply')
async def reply(phone: str, data: HumanReply, authorization: str | None = Header(None)):
    admin(authorization)
    with db() as con:
        row = con.execute('SELECT mode FROM zara_conversations WHERE phone=%s', (phone,)).fetchone()
    if not row:
        raise HTTPException(404)
    if row[0] != 'human':
        raise HTTPException(409, 'Assuma o atendimento antes de responder')
    try:
        mid = await send(phone, data.text)
    except (httpx.HTTPError, KeyError):
        raise HTTPException(502, 'Não foi possível enviar pelo WhatsApp')
    with db() as con:
        con.execute('''INSERT INTO zara_messages(message_id,phone,direction,body,delivered)
                       VALUES(%s,%s,'out',%s,true) ON CONFLICT DO NOTHING''', (mid, phone, data.text))
        con.execute('UPDATE zara_conversations SET updated_at=now() WHERE phone=%s', (phone,))
    return {'ok': True, 'id': mid}
