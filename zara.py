"""Zara: inbound WhatsApp Cloud API support with explicit human handoff."""
import hashlib
import hmac
import os
import re
import json
import logging
import time
import uuid

from starlette.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse

import httpx
from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

router = APIRouter()
logger = logging.getLogger('uvicorn.error.zara')


def log_event(level, event, **fields):
    # Only explicitly selected operational fields; never log bodies, phones or credentials.
    logger.log(level, json.dumps({'component': 'zara', 'event': event,
                                'severity': logging.getLevelName(level), **fields}))


class WhatsAppSendError(Exception):
    """Sanitized integration failure, safe to handle at the Zara boundary."""


def setting(key):
    value = os.getenv(key, '')
    if not value.strip():
        log_event(logging.ERROR, 'configuration_missing', setting=key)
        raise HTTPException(503, 'Integração WhatsApp ainda não configurada')
    return value


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
    con.execute('ALTER TABLE zara_messages ADD COLUMN IF NOT EXISTS reply_to TEXT')
    con.execute('ALTER TABLE zara_messages ADD COLUMN IF NOT EXISTS panel_confirmed_at TIMESTAMPTZ')
    con.execute('''CREATE TABLE IF NOT EXISTS zara_delivery_events (
        message_id TEXT NOT NULL, phone TEXT NOT NULL, status TEXT NOT NULL,
        event_at BIGINT NOT NULL, received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY(message_id,phone,status),
        CHECK(status IN ('sent','delivered','read','failed')))''')
    con.execute('CREATE INDEX IF NOT EXISTS zara_reply_idx ON zara_messages(reply_to,phone)')


def db():
    from server import db as server_db
    return server_db()


def admin(authorization):
    from server import auth
    if auth(authorization) != 'Ana Paula':
        raise HTTPException(403, 'Acesso restrito à administradora')


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
    except Exception as exc:
        log_event(logging.WARNING, 'ai_fallback', error_type=type(exc).__name__)
        return PENDING


async def send(phone, body):
    started = time.monotonic()
    request_id = uuid.uuid4().hex
    try:
        token, phone_id = setting('WA_ACCESS_TOKEN'), setting('WA_PHONE_NUMBER_ID')
        version = os.getenv('WA_GRAPH_VERSION', 'v23.0')
        if not re.fullmatch(r'v\d+\.\d+', version) or not phone_id.isascii() or not phone_id.isdigit():
            raise ValueError('Invalid Graph configuration')
        async with httpx.AsyncClient(timeout=httpx.Timeout(12, connect=4),
                                     limits=httpx.Limits(max_connections=10)) as client:
            result = await client.post(f'https://graph.facebook.com/{version}/{phone_id}/messages',
                headers={'Authorization': f'Bearer {token}'},
                json={'messaging_product': 'whatsapp', 'to': phone, 'type': 'text',
                      'text': {'body': body[:4096]}})
            if not result.is_success:
                try:
                    error = result.json().get('error', {})
                    codes = {k: error[k] for k in ('code', 'error_subcode')
                             if isinstance(error.get(k), int)}
                except (ValueError, AttributeError, TypeError):
                    codes = {}
                log_event(logging.ERROR, 'graph_http_error', request_id=request_id,
                          http_status=result.status_code,
                          duration_ms=round((time.monotonic()-started)*1000), **codes)
                raise WhatsAppSendError('Graph API recusou o envio')
            mid = result.json()['messages'][0]['id']
            if not isinstance(mid, str) or not mid or len(mid) > 256:
                raise ValueError('Invalid message ID')
            log_event(logging.INFO, 'graph_accepted', request_id=request_id,
                      duration_ms=round((time.monotonic()-started)*1000))
            return mid
    except WhatsAppSendError:
        raise
    except Exception as exc:
        # Do not log exception text: HTTP exceptions may contain tokens or response bodies.
        log_event(logging.ERROR, 'graph_send_failed', request_id=request_id,
                  error_type=type(exc).__name__,
                  duration_ms=round((time.monotonic()-started)*1000))
        raise WhatsAppSendError('Não foi possível enviar pelo WhatsApp') from None


@router.get('/webhook', include_in_schema=False)
@router.get('/api/zara/webhook')
def verify_webhook(mode: str | None = Query(None, alias='hub.mode'),
                   token: str | None = Query(None, alias='hub.verify_token'),
                   challenge: str | None = Query(None, alias='hub.challenge')):
    verify = setting('WA_VERIFY_TOKEN')
    if mode != 'subscribe' or not token or not hmac.compare_digest(token.encode('utf-8'), verify.encode('utf-8')):
        log_event(logging.WARNING, 'verification_rejected')
        raise HTTPException(403)
    if not challenge or len(challenge) > 1024:
        raise HTTPException(400, 'Challenge ausente ou inválido')
    log_event(logging.INFO, 'verification_accepted')
    return PlainTextResponse(challenge)


def validate_payload(payload):
    def mapping(value):
        if not isinstance(value, dict):
            raise ValueError('Expected object')
        return value

    def sequence(value):
        if not isinstance(value, list):
            raise ValueError('Expected list')
        return value

    mapping(payload)
    if payload.get('object', 'whatsapp_business_account') != 'whatsapp_business_account':
        raise ValueError('Unexpected object')
    for entry in sequence(payload.get('entry', [])):
        for change in sequence(mapping(entry).get('changes', [])):
            value = mapping(mapping(change).get('value', {}))
            mapping(value.get('metadata', {}))
            for contact in sequence(value.get('contacts', [])):
                mapping(mapping(contact).get('profile', {}))
                if not isinstance(contact.get('wa_id', ''), str):
                    raise ValueError('Invalid contact ID')
            for status in sequence(value.get('statuses', [])):
                mapping(status)
                for key in ('id', 'recipient_id', 'status', 'timestamp'):
                    if not isinstance(status.get(key, ''), str):
                        raise ValueError('Invalid status field')
            for message in sequence(value.get('messages', [])):
                context = mapping(mapping(message).get('context', {}))
                if not isinstance(context.get('id', ''), str):
                    raise ValueError('Invalid reply context')
                text = mapping(mapping(message).get('text', {}))
                if not isinstance(text.get('body', ''), str):
                    raise ValueError('Invalid text')


@router.post('/webhook', include_in_schema=False)
@router.post('/api/zara/webhook')
async def receive_webhook(request: Request, x_hub_signature_256: str | None = Header(None)):
    secret, expected_id = setting('WA_APP_SECRET'), setting('WA_PHONE_NUMBER_ID')
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 256_000:
            raise HTTPException(413)
    if not x_hub_signature_256 or not re.fullmatch(r'sha256=[0-9a-f]{64}', x_hub_signature_256):
        log_event(logging.WARNING, 'signature_rejected')
        raise HTTPException(403)
    signature = 'sha256=' + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, x_hub_signature_256):
        log_event(logging.WARNING, 'signature_rejected')
        raise HTTPException(403)
    try:
        payload = json.loads(raw)
        validate_payload(payload)
    except (ValueError, TypeError, RecursionError):
        log_event(logging.WARNING, 'payload_rejected')
        raise HTTPException(400, 'Payload inválido')
    for entry in payload.get('entry', []):
        for change in entry.get('changes', []):
            value = change.get('value', {})
            if str(value.get('metadata', {}).get('phone_number_id')) != expected_id:
                continue
            if value.get('statuses'):
                try:
                    await run_in_threadpool(record_statuses, value['statuses'])
                except Exception as exc:
                    log_event(logging.ERROR, 'delivery_storage_failed', error_type=type(exc).__name__)
                    raise HTTPException(503, 'Falha temporária no armazenamento de status') from None
            contacts = {c.get('wa_id'): c.get('profile', {}).get('name', '') for c in value.get('contacts', [])}
            for msg in value.get('messages', []):
                phone, mid = str(msg.get('from', '')), str(msg.get('id', ''))
                if not re.fullmatch(r'\d{8,16}', phone) or not mid or len(mid) > 256:
                    continue
                body = (msg.get('text', {}).get('body', '') if msg.get('type') == 'text' else '')[:4000]
                if not body:
                    body = '[Mensagem não textual recebida]'
                name = str(contacts.get(phone, ''))[:120]
                reply_to = msg.get('context', {}).get('id', '')[:256] or None
                def persist_inbound():
                    with db() as con:
                        con.execute('''INSERT INTO zara_conversations(phone,name) VALUES(%s,%s)
                            ON CONFLICT(phone) DO UPDATE SET name=COALESCE(NULLIF(EXCLUDED.name,''),zara_conversations.name),updated_at=now()''', (phone, name))
                        inserted = con.execute('''INSERT INTO zara_messages(message_id,phone,direction,body,reply_to)
                            VALUES(%s,%s,'in',%s,%s) ON CONFLICT DO NOTHING RETURNING message_id''', (mid, phone, body, reply_to)).fetchone()
                        if not inserted:
                            return None
                        mode = con.execute('SELECT mode FROM zara_conversations WHERE phone=%s FOR UPDATE', (phone,)).fetchone()[0]
                        first = con.execute("SELECT count(*) FROM zara_messages WHERE phone=%s AND direction='in'", (phone,)).fetchone()[0] == 1
                        rule, handoff = response_rule(body, name, first)
                        if not body or body == '[Mensagem não textual recebida]':
                            rule, handoff = HANDOFF, True
                        if handoff:
                            con.execute("UPDATE zara_conversations SET mode='human' WHERE phone=%s", (phone,))
                        return mode, rule
                try:
                    saved = await run_in_threadpool(persist_inbound)
                except Exception as exc:
                    log_event(logging.ERROR, 'inbound_storage_failed', error_type=type(exc).__name__)
                    raise HTTPException(503, 'Falha temporária no armazenamento') from None
                if saved is None:
                    log_event(logging.INFO, 'duplicate_ignored')
                    continue
                mode, rule = saved
                if mode == 'human':
                    continue
                try:
                    answer = rule or await ai_answer(body)
                    out_id = await send(phone, answer)
                except Exception as exc:
                    log_event(logging.ERROR, 'response_failed', error_type=type(exc).__name__)
                    def mark_human():
                        with db() as con:
                            con.execute("UPDATE zara_conversations SET mode='human',updated_at=now() WHERE phone=%s", (phone,))
                    try:
                        await run_in_threadpool(mark_human)
                    except Exception as storage_exc:
                        log_event(logging.ERROR, 'handoff_storage_failed', error_type=type(storage_exc).__name__)
                    continue
                def persist_outbound():
                    with db() as con:
                        con.execute('''INSERT INTO zara_messages(message_id,phone,direction,body,delivered)
                            VALUES(%s,%s,'out',%s,false) ON CONFLICT DO NOTHING''', (out_id, phone, answer))
                        con.execute('UPDATE zara_conversations SET updated_at=now() WHERE phone=%s', (phone,))
                try:
                    await run_in_threadpool(persist_outbound)
                except Exception as exc:
                    log_event(logging.ERROR, 'outbound_storage_failed', error_type=type(exc).__name__)
    return {'ok': True}


def delivery_state(events):
    statuses = set(events)
    # A delayed 'sent'/'failed' must never undo known delivery or reading.
    for status in ('read', 'delivered', 'failed', 'sent'):
        if status in statuses:
            return status
    return 'graph_accepted'


def record_statuses(statuses):
    statuses = [e for e in statuses if e.get('id') and e.get('recipient_id') and e.get('timestamp')
                and e.get('status') in ('sent', 'delivered', 'read', 'failed')]
    if not statuses:
        return
    with db() as con:
        for event in statuses:
            mid, phone, status = event.get('id', ''), event.get('recipient_id', ''), event.get('status', '')
            timestamp = event.get('timestamp', '')
            if (not mid or len(mid) > 256 or not re.fullmatch(r'[0-9]{8,16}', phone)
                    or status not in ('sent', 'delivered', 'read', 'failed')
                    or not re.fullmatch(r'[0-9]{1,12}', timestamp)):
                continue
            # No FK: delivery callbacks can arrive before the send response is persisted.
            con.execute("""INSERT INTO zara_delivery_events(message_id,phone,status,event_at)
                VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING""", (mid, phone, status, int(timestamp)))
            con.execute("""UPDATE zara_messages SET delivered=true
                WHERE message_id=%s AND phone=%s AND direction='out'
                AND EXISTS(SELECT 1 FROM zara_delivery_events
                    WHERE message_id=%s AND phone=%s AND status IN ('delivered','read'))""", (mid, phone, mid, phone))
            con.execute('UPDATE zara_conversations SET updated_at=now() WHERE phone=%s', (phone,))
            log_event(logging.WARNING if status == 'failed' else logging.INFO,
                      'delivery_status_received', delivery_status=status)


def message_views(con, phone):
    rows = con.execute("""SELECT message_id,direction,body,created_at,reply_to,panel_confirmed_at
        FROM zara_messages WHERE phone=%s ORDER BY created_at DESC,message_id DESC LIMIT 100""", (phone,)).fetchall()
    events_by_id = {}
    events = con.execute("""SELECT d.message_id,d.status FROM zara_delivery_events d
        WHERE d.phone=%s AND d.message_id IN (SELECT message_id FROM zara_messages
            WHERE phone=%s ORDER BY created_at DESC,message_id DESC LIMIT 100)""", (phone, phone)).fetchall()
    for mid, status in events:
        events_by_id.setdefault(mid, []).append(status)
    replies = {row[0] for row in con.execute("""SELECT DISTINCT reply_to FROM zara_messages
        WHERE phone=%s AND direction='in' AND reply_to IS NOT NULL
        AND reply_to IN (SELECT message_id FROM zara_messages WHERE phone=%s
            ORDER BY created_at DESC,message_id DESC LIMIT 100)""", (phone, phone)).fetchall()}
    items = []
    for mid, direction, body, at, reply_to, confirmed in reversed(rows):
        state = delivery_state(events_by_id.get(mid, [])) if direction == 'out' else 'received'
        replied = direction == 'out' and mid in replies
        ready = direction == 'out' and state in ('delivered', 'read') and replied
        items.append({'id': mid, 'direction': direction, 'body': body, 'at': at.isoformat(),
                      'replyTo': reply_to, 'deliveryStatus': state, 'replyReceived': replied,
                      'e2eReady': ready, 'e2eStatus': 'confirmed' if ready and confirmed else 'pending',
                      'panelConfirmedAt': confirmed.isoformat() if confirmed else None})
    return items


@router.get('/api/zara/conversations')
def conversations(authorization: str | None = Header(None)):
    admin(authorization)
    with db() as con:
        rows = con.execute("""SELECT phone,name,mode,updated_at FROM zara_conversations
                              ORDER BY updated_at DESC LIMIT 100""").fetchall()
        result = []
        for phone, name, mode, at in rows:
            items = message_views(con, phone)
            outgoing = next((item for item in reversed(items) if item['direction'] == 'out'), None)
            result.append({'phone': phone, 'name': name, 'mode': mode, 'updatedAt': at.isoformat(),
                           'lastMessage': items[-1]['body'] if items else '',
                           'deliveryStatus': outgoing['deliveryStatus'] if outgoing else None,
                           'e2eStatus': 'confirmed' if any(i['e2eStatus']=='confirmed' for i in items) else 'pending'})
    return result


@router.get('/api/zara/conversations/{phone}')
def conversation(phone: str, authorization: str | None = Header(None)):
    admin(authorization)
    with db() as con:
        return message_views(con, phone)


@router.post('/api/zara/conversations/{phone}/messages/{mid}/confirm-e2e')
def confirm_e2e(phone: str, mid: str, authorization: str | None = Header(None)):
    admin(authorization)
    with db() as con:
        # Serialize confirmation with the conversation; no arbitrary GET marks E2E complete.
        con.execute('SELECT phone FROM zara_conversations WHERE phone=%s FOR UPDATE', (phone,))
        item = next((item for item in message_views(con, phone) if item['id'] == mid), None)
        if not item:
            raise HTTPException(404, 'Mensagem não encontrada no histórico recente')
        if not item['e2eReady']:
            raise HTTPException(409, 'Aguarde entrega e resposta vinculada à mensagem de teste')
        con.execute("""UPDATE zara_messages SET panel_confirmed_at=COALESCE(panel_confirmed_at,now())
            WHERE message_id=%s AND phone=%s AND direction='out'""", (mid, phone))
        con.execute('UPDATE zara_conversations SET updated_at=now() WHERE phone=%s', (phone,))
    log_event(logging.INFO, 'e2e_panel_confirmed')
    return {'ok': True, 'e2eStatus': 'confirmed'}


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
    except WhatsAppSendError:
        raise HTTPException(502, 'Não foi possível enviar pelo WhatsApp')
    with db() as con:
        con.execute('''INSERT INTO zara_messages(message_id,phone,direction,body,delivered)
                       VALUES(%s,%s,'out',%s,false) ON CONFLICT DO NOTHING''', (mid, phone, data.text))
        con.execute('UPDATE zara_conversations SET updated_at=now() WHERE phone=%s', (phone,))
    return {'ok': True, 'id': mid}
