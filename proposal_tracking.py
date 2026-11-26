"""Public CPQ documents, seller alerts, and contact-based inactivity."""
import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, BackgroundTasks, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import Field, model_validator
from psycopg.types.json import Jsonb

import cpq

router = APIRouter()
Scope = Literal['propostas', 'negocios']
Channel = Literal['whatsapp', 'email', 'link_direto']
Action = Literal['ligacao', 'email_enviado', 'email_recebido', 'reuniao_agendada',
                 'reuniao_realizada', 'proposta_enviada', 'interacao_comercial']
ACTIONS = list(Action.__args__)
CACHE = {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
         'X-Robots-Tag': 'noindex, nofollow', 'X-Content-Type-Options': 'nosniff'}


def setup(con):
    con.execute(Path(__file__).with_name('migrations').joinpath('001_proposal_tracking.sql').read_text())


class Rules(cpq.Strict):
    dias_atencao: int = Field(default=7, ge=1, le=3650, strict=True)
    dias_risco: int = Field(default=14, ge=1, le=3650, strict=True)
    dias_critico: int = Field(default=30, ge=1, le=3650, strict=True)
    acoes_validas: list[Action] = Field(default_factory=lambda: ACTIONS.copy(), max_length=len(ACTIONS))

    @model_validator(mode='after')
    def ordered_thresholds(self):
        if not self.dias_atencao < self.dias_risco < self.dias_critico:
            raise ValueError('Use prazos crescentes para atenção, risco e crítico')
        if len(set(self.acoes_validas)) != len(self.acoes_validas):
            raise ValueError('Ações repetidas')
        return self


class Contact(cpq.Strict):
    acao: Action
    canal: Literal['telefone','email','whatsapp','presencial','video','link_direto','outro']
    evidencia: str = Field(min_length=3, max_length=1000)


def db():
    return cpq._services['db']()


def rules_for(con, user, scope):
    row = con.execute('SELECT dias_atencao,dias_risco,dias_critico,acoes_validas FROM regras_inatividade WHERE usuario=%s AND escopo=%s',
                      (user, scope)).fetchone()
    return Rules(dias_atencao=row[0], dias_risco=row[1], dias_critico=row[2], acoes_validas=row[3]) if row else Rules()


@router.get('/api/cpq/inactivity/{scope}')
def get_rules(scope: Scope, authorization: str | None = Header(default=None)):
    user = cpq.access(authorization)
    with db() as con:
        return rules_for(con, user, scope)


@router.put('/api/cpq/inactivity/{scope}')
def set_rules(scope: Scope, data: Rules, authorization: str | None = Header(default=None)):
    user = cpq.access(authorization)
    with db() as con:
        con.execute('''INSERT INTO regras_inatividade VALUES (%s,%s,%s,%s,%s,%s,now())
            ON CONFLICT(usuario,escopo) DO UPDATE SET dias_atencao=excluded.dias_atencao,
            dias_risco=excluded.dias_risco,dias_critico=excluded.dias_critico,
            acoes_validas=excluded.acoes_validas,updated_at=now()''',
            (user, scope, data.dias_atencao, data.dias_risco, data.dias_critico, Jsonb(data.acoes_validas)))
    return data


def record_contact(con, kind, identifier, user, action, channel, evidence):
    con.execute('''INSERT INTO contatos_inatividade(tipo,registro_id,autor,acao,canal,evidencia)
        VALUES (%s,%s,%s,%s,%s,%s)''', (kind, identifier, user, action, channel, evidence))


@router.post('/api/cpq/contact/{scope}/{identifier}')
def add_contact(scope: Scope, identifier: str, data: Contact, authorization: str | None = Header(default=None)):
    user = cpq.access(authorization)
    with db() as con:
        if scope == 'propostas':
            cpq.scoped_quote(con, identifier, user)
            kind = 'cpq_quote'
        else:
            kind = 'opportunity'
            opportunity = cpq.get(con, kind, identifier, True)
            cpq._services['check_client_scope'](con, user, opportunity['clientId'])
            if cpq._services['is_seller'](con, user) and opportunity.get('owner') != user:
                raise HTTPException(403, 'Negócio fora da sua carteira')
        record_contact(con, kind, identifier, user, data.acao, data.canal, data.evidencia)
    return {'saved': True}


def proposal_status(quote, views, today):
    if quote.get('approvalStatus') == 'Recusada' or quote.get('rejectedAt'):
        return 'Recusada'
    if quote.get('acceptedAt'):
        return 'Aprovada'
    if quote['validUntil'] < str(today):
        return 'Expirada'
    if views:
        return 'Visualizada'
    return 'Enviada' if quote.get('sentAt') else 'Rascunho'


def contact_details(con, kind, records, user, scope):
    rules = rules_for(con, user, scope)
    ids = [r['id'] for r in records]
    created = dict(con.execute('SELECT id,created_at FROM entities WHERE kind=%s AND id=ANY(%s)', (kind, ids)).fetchall())
    latest = con.execute('''SELECT DISTINCT ON (registro_id) registro_id, ocorrido_em, canal
        FROM contatos_inatividade WHERE tipo=%s AND registro_id=ANY(%s) AND acao=ANY(%s)
        ORDER BY registro_id, ocorrido_em DESC, id DESC''',
        (kind, ids, rules.acoes_validas)).fetchall()
    by_id = {row[0]: (row[1], row[2]) for row in latest}
    output = {}
    for record in records:
        baseline = record.get('createdAt') or record.get('registrationDate')
        if kind == 'cpq_quote' and 'proposta_enviada' in rules.acoes_validas and record.get('sentAt'):
            baseline = record['sentAt']
            fallback_channel = {'WhatsApp':'whatsapp','E-mail':'email','Link':'link_direto'}.get(record.get('sentChannel'))
        else:
            fallback_channel = None
        last, channel = by_id.get(record['id'], (None, None))
        try:
            since = datetime.fromisoformat(baseline) if baseline else created.get(record['id'])
            if not since and record.get('stageHistory'):
                first = record['stageHistory'][0].get('at')
                since = datetime.fromisoformat(first) if first else None
        except (ValueError, TypeError):
            since = None
        if since and not since.tzinfo:
            since = since.replace(tzinfo=cpq.TZ)
        if since and (not last or since > last):
            last, channel = since, fallback_channel
        days = max(0, (datetime.now(timezone.utc) - last).days) if last else None
        level = next((label for label, limit in (
            ('Crítico', rules.dias_critico), ('Em risco', rules.dias_risco), ('Atenção', rules.dias_atencao)
        ) if days is not None and days >= limit), 'Em dia') if last else None
        output[record['id']] = {'since': last.isoformat() if last else None, 'channel': channel,
                                'days': days, 'level': level}
    return {'rules': rules.model_dump(), 'records': output}


@router.get('/api/cpq/activity/{scope}')
def activity(scope: Scope, authorization: str | None = Header(default=None)):
    user = cpq.access(authorization)
    if scope == 'propostas':
        quotes = cpq.list_quotes(authorization)
        with db() as con:
            facts = con.execute('''SELECT proposta_id,numero,total_visualizacoes,ultima_visualizacao_em,ultimo_canal
                FROM propostas WHERE proposta_id=ANY(%s)''', ([q['id'] for q in quotes],)).fetchall()
            contacts = contact_details(con, 'cpq_quote', quotes, user, scope)
        tracking = {row[0]: row[1:] for row in facts}
        today = cpq.now().date()
        for quote in quotes:
            number, views, last, channel = tracking.get(quote['id'], (None, 0, None, None))
            quote.update(number=number, views=views, viewedAt=last, viewedChannel=channel,
                         proposalStatus=proposal_status(quote, views, today))
        return {**contacts, 'quotes': quotes}
    with db() as con:
        records = cpq._services['scoped_rows'](con, 'opportunity', user)
        if cpq._services['is_seller'](con, user):
            records = [row for row in records if row.get('owner') == user]
        return contact_details(con, 'opportunity', records, user, scope)


def ensure_metadata(con, quote):
    con.execute('INSERT INTO propostas(proposta_id) VALUES (%s) ON CONFLICT DO NOTHING', (quote['id'],))
    return con.execute('SELECT numero FROM propostas WHERE proposta_id=%s', (quote['id'],)).fetchone()[0]


def make_link(con, quote, channel):
    token = con.execute('''INSERT INTO proposta_links(token,proposta_id,canal) VALUES (%s,%s,%s)
        ON CONFLICT(proposta_id,canal) DO UPDATE SET canal=excluded.canal RETURNING token''',
        (uuid4(), quote['id'], channel)).fetchone()[0]
    return '/p/' + str(token)


@router.post('/api/cpq/quotes/{identifier}/links/{channel}')
def link(identifier: str, channel: Channel, authorization: str | None = Header(default=None)):
    user = cpq.access(authorization)
    with db() as con:
        cpq.lock(con)
        quote = cpq.scoped_quote(con, identifier, user)
        cpq.current(con, quote)
        cpq.released(quote)
        if quote.get('rejectedAt') or quote.get('convertedOrderId'):
            raise HTTPException(409, 'Proposta encerrada')
        number = ensure_metadata(con, quote)
        if not quote.get('publicCustomer'):
            client = cpq.quote_customer(con, quote)
            quote['publicCustomer'] = {key:client.get(key) or '' for key in ('name','taxId','state','city')}
            cpq.put(con, 'cpq_quote', quote)
        return {'path': make_link(con, quote, channel), 'number': number}


def public_quote(con, token):
    row = con.execute('''SELECT q.payload,p.numero,l.canal,p.chave_ip FROM proposta_links l
        JOIN propostas p USING (proposta_id)
        JOIN entities q ON q.kind=p.tipo AND q.id=p.proposta_id
        WHERE l.token=%s FOR UPDATE OF q''', (token,)).fetchone()
    if not row or row[0]['validUntil'] < str(cpq.now().date()) or row[0]['approvalStatus'] != 'Aprovada' or row[0].get('rejectedAt'):
        raise HTTPException(404, 'Proposta indisponível ou expirada', headers=CACHE)
    return row


def public_document_page(title, content):
    return HTMLResponse(
        '<!doctype html><html lang="pt-BR"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{escape(title)}</title><style>'
        'body{font:1rem system-ui;color:#123d44;background:#f4f7f7;margin:0;padding:1rem}'
        'main{max-width:34rem;margin:8vh auto;background:white;padding:clamp(1rem,5vw,2rem);border-radius:12px}'
        'button,input{font:inherit;padding:.7rem;margin:.4rem 0;max-width:100%;box-sizing:border-box}'
        'button{background:#123d44;color:white;border:0;border-radius:6px;cursor:pointer}'
        '</style><main><small>L2 Representações</small>'
        f'<h1>{escape(title)}</h1>{content}</main></html>', headers={
        **CACHE, 'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'"}
    )


@router.get('/p/{token}', response_class=HTMLResponse)
def public_page(token: UUID):
    with db() as con:
        quote, number, _, _ = public_quote(con, token)
    active = not quote.get('acceptedAt')
    return public_document_page(f'Proposta #{number}',
                f'<p>{escape(quote["publicCustomer"]["name"])}</p>'
                f'<form method="post" action="/p/{token}/pdf"><button>Visualizar proposta em PDF</button></form>'
                + (f'<form method="post" action="/p/{token}/aceite"><label>Seu nome<br>'
                   '<input name="name" minlength="3" maxlength="120" required></label><br>'
                   '<button>Confirmar aceite comercial</button></form>' if active else '<p>Aceite registrado.</p>'))


def ip_hash(request, token, private_key):
    # A per-link secret prevents correlation between proposals.
    ip = request.client.host if request.client else ''
    secret = os.environ.get('L2_PROPOSAL_IP_SECRET') or str(private_key)
    return hmac.new(secret.encode(), f'{token}:{ip}'.encode(), hashlib.sha256).hexdigest()


def send_webhook(event):
    url = os.environ.get('L2_PROPOSAL_WEBHOOK_URL', '')
    secret = os.environ.get('L2_PROPOSAL_WEBHOOK_SECRET', '')
    if not url.startswith('https://') or not secret:
        return
    body = json.dumps(event, ensure_ascii=False).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    try:
        httpx.post(url, content=body, headers={'Content-Type':'application/json', 'X-L2-Signature':signature}, timeout=5).raise_for_status()
    except httpx.HTTPError:
        logging.warning('proposal_open_webhook_failed event_id=%s', event['id'])


@router.post('/p/{token}/pdf')
def public_pdf(token: UUID, request: Request, background: BackgroundTasks):
    with db() as con:
        quote, number, channel, private_key = public_quote(con, token)
        agent = request.headers.get('user-agent', '')[:512]
        device = 'mobile' if any(word in agent.lower() for word in ('mobile','android','ipad','iphone')) else 'desktop'
        timestamp = cpq.now()
        identifier = con.execute('''INSERT INTO proposta_logs_abertura
            (proposta_id,vendedor_id,canal,user_agent,dispositivo,ip_hash,visualizado_em)
            VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id''',
            (quote['id'], quote['sellerResponsible'], channel, agent, device, ip_hash(request, token, private_key), timestamp)).fetchone()[0]
        con.execute('''UPDATE propostas SET total_visualizacoes=total_visualizacoes+1,
            ultima_visualizacao_em=%s,ultimo_canal=%s WHERE proposta_id=%s''',
            (timestamp, channel, quote['id']))
        con.execute("SELECT pg_notify('l2_records_changed','')")
        content = cpq.quote_pdf({**quote, 'number': number}, quote['publicCustomer'], str(request.url.replace(path=f'/p/{token}', query='')))
    background.add_task(send_webhook, {'id':identifier, 'event':'proposal.opened', 'proposalId':quote['id'],
                        'number':number, 'seller':quote['sellerResponsible'], 'channel':channel, 'viewedAt':timestamp.isoformat()})
    return Response(content, media_type='application/pdf', headers={**CACHE,'Content-Disposition':f'inline; filename="L2_Proposta_{number}.pdf"'})


@router.post('/p/{token}/aceite', response_class=HTMLResponse)
def public_accept(token: UUID, name: str = Form(min_length=3, max_length=120)):
    name = name.strip()
    if len(name) < 3:
        raise HTTPException(422, 'Informe seu nome')
    with db() as con:
        cpq.lock(con)
        quote, number, channel, _ = public_quote(con, token)
        if not quote.get('acceptedAt'):
            cpq.current(con, quote)
            quote.update(acceptedAt=cpq.now().isoformat(), acceptedBy=name,
                         acceptanceEvidence='Confirmação via link comercial ('+channel+')')
            cpq.put(con, 'cpq_quote', quote)
            cpq.audit(con, quote['sellerResponsible'], quote['id'], 'public_accepted')
    return public_document_page(f'Proposta #{number}', '<p>Aceite registrado. A equipe comercial dará continuidade ao pedido.</p>')


@router.get('/api/cpq/alerts')
def alerts(authorization: str | None = Header(default=None)):
    user = cpq.access(authorization)
    with db() as con:
        client_ids = [c['id'] for c in cpq._services['scoped_rows'](con, 'client', user)]
        rows = con.execute('''SELECT l.id,p.numero,l.canal,q.payload->'publicCustomer'->>'name',l.visualizado_em
            FROM proposta_logs_abertura l JOIN propostas p USING (proposta_id)
            JOIN entities q ON q.kind=p.tipo AND q.id=p.proposta_id
            WHERE l.vendedor_id=%s AND l.lido_em IS NULL
            AND q.payload->>'sellerResponsible'=%s AND (q.payload->>'clientId'='' OR q.payload->>'clientId'=ANY(%s))
            ORDER BY l.id DESC LIMIT 50''', (user,user,client_ids)).fetchall()
    return [dict(zip(('id','number','channel','client','at'), row)) for row in rows]


@router.post('/api/cpq/alerts/read/{identifier}')
def read_alert(identifier: int, authorization: str | None = Header(default=None)):
    user = cpq.access(authorization)
    with db() as con:
        con.execute('UPDATE proposta_logs_abertura SET lido_em=now() WHERE id=%s AND vendedor_id=%s AND lido_em IS NULL',
                    (identifier, user))
    return {'saved': True}
