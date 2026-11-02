"""Commercial planning API. Additive schema; no external banking calls or mutations.

Existing server sessions remain authoritative. Financial actions require the finance
sector and enrolled TOTP; banking webhooks require an independently provisioned key.
"""
import base64
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import struct
import time
import uuid
from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from psycopg.types.json import Jsonb
from commercial_indicators import months_before, project

router = APIRouter(prefix='/api/strategy/commercial', tags=['Planejamento comercial'])
services = {}
TZ = ZoneInfo('America/Belem')
STAGES = ('sell_in', 'sell_through', 'sell_out')
SCHEMA = """
CREATE TABLE IF NOT EXISTS l2_commercial_profiles (
 client_id TEXT PRIMARY KEY, tax_id TEXT UNIQUE NOT NULL,
 payload JSONB NOT NULL, version INTEGER NOT NULL DEFAULT 1,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS l2_commercial_documents (
 id TEXT PRIMARY KEY, source TEXT NOT NULL, external_id TEXT NOT NULL,
 stage TEXT NOT NULL CHECK(stage IN ('sell_in','sell_through','sell_out')),
 client_id TEXT NOT NULL, order_id TEXT, business_date DATE NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('sale','return')), original_id TEXT REFERENCES l2_commercial_documents(id),
 payload JSONB NOT NULL, created_by TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(source,stage,external_id));
CREATE INDEX IF NOT EXISTS l2_commercial_scope_date ON l2_commercial_documents(client_id,business_date,stage);
CREATE INDEX IF NOT EXISTS l2_commercial_date ON l2_commercial_documents(stage,business_date);
CREATE TABLE IF NOT EXISTS l2_commercial_interactions (
 id TEXT PRIMARY KEY, client_id TEXT NOT NULL, author TEXT NOT NULL,
 payload JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS l2_commercial_interaction_client ON l2_commercial_interactions(client_id,created_at);
CREATE TABLE IF NOT EXISTS l2_commercial_baselines (
 id TEXT PRIMARY KEY, author TEXT NOT NULL, payload JSONB NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS l2_commercial_coverage (
 brand TEXT PRIMARY KEY, history_complete BOOLEAN NOT NULL DEFAULT false,
 evidence TEXT NOT NULL, author TEXT NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS l2_commercial_receivables (
 id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES l2_commercial_documents(id),
 client_id TEXT NOT NULL, bank_account TEXT NOT NULL, reference TEXT NOT NULL,
 due_date DATE NOT NULL, amount NUMERIC(18,2) NOT NULL CHECK(amount>0),
 paid NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK(paid>=0 AND paid<=amount),
 UNIQUE(bank_account,reference));
CREATE TABLE IF NOT EXISTS l2_commercial_bank_transactions (
 id TEXT PRIMARY KEY, account TEXT NOT NULL, external_id TEXT NOT NULL,
 business_date DATE NOT NULL, amount NUMERIC(18,2) NOT NULL,
 reference TEXT NOT NULL, UNIQUE(account,external_id));
CREATE TABLE IF NOT EXISTS l2_commercial_allocations (
 transaction_id TEXT PRIMARY KEY REFERENCES l2_commercial_bank_transactions(id),
 receivable_id TEXT NOT NULL REFERENCES l2_commercial_receivables(id),
 amount NUMERIC(18,2) NOT NULL CHECK(amount>0), author TEXT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS l2_commercial_mfa (
 username TEXT PRIMARY KEY, secret TEXT NOT NULL, enabled BOOLEAN NOT NULL DEFAULT false,
 counter BIGINT NOT NULL DEFAULT -1, failed INTEGER NOT NULL DEFAULT 0,
 blocked_until TIMESTAMPTZ);
"""


def configure(**kwargs):
    services.update(kwargs)


def enabled():
    return os.getenv('L2_COMMERCIAL_PLANNING_ENABLED', 'false').lower() == 'true'


def setup(con):
    for statement in SCHEMA.split(';'):
        if statement.strip():
            con.execute(statement)


def actor(header, level='read'):
    user = services['auth'](header)
    if not enabled():
        raise HTTPException(503, 'Planejamento comercial aguarda ativação neste ambiente')
    sectors = services['sectors_for'](user)
    needed = {'read': {'commercial', 'management', 'admin'},
              'manage': {'management', 'admin'}, 'finance': {'finance'}}[level]
    if not needed & sectors:
        raise HTTPException(403, 'Perfil sem permissão para esta operação')
    return user


def scope_sql(user):
    # Applied by PostgreSQL BEFORE aggregates and exports. Promoters and sellers
    # cannot obtain broad access simply by requesting another client id.
    with services['db']() as con:
        row = con.execute('SELECT role FROM app_users WHERE username=%s AND active', (user,)).fetchone()
    broad = bool(row and (user == 'Euler' or row[0] not in ('Vendedor', 'Representante Comercial', 'Promotor')) and
                 {'management', 'admin'} & services['sectors_for'](user))
    if broad:
        return 'TRUE', []
    return "(c.payload->>'owner'=%s OR EXISTS (SELECT 1 FROM client_seller_authorizations a WHERE a.client_id=c.id AND a.seller_username=%s AND a.active))", [user, user]


def client(con, user, identifier):
    predicate, args = scope_sql(user)
    row = con.execute("SELECT c.payload FROM entities c WHERE c.kind='client' AND c.id=%s AND " + predicate,
                      [identifier] + args).fetchone()
    if not row:
        raise HTTPException(404, 'Porta não encontrada na carteira autorizada')
    return row[0]


def audit(con, user, kind, identifier, action):
    con.execute('INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,%s,%s,%s)',
                (user, kind, identifier, action))
    con.execute("SELECT pg_notify('l2_records_changed','')")


def valid_tax_id(value):
    digits = re.sub(r'\D', '', value)
    if len(set(digits)) <= 1:
        return False
    if len(digits) == 14:
        for length in (12, 13):
            weights = list(range(length - 7, 1, -1)) + list(range(9, 1, -1))
            rest = sum(int(x) * y for x, y in zip(digits[:length], weights)) % 11
            if int(digits[length]) != (0 if rest < 2 else 11 - rest):
                return False
        return True
    if len(digits) == 11:
        for length in (9, 10):
            check = (sum(int(digits[i]) * (length + 1 - i) for i in range(length)) * 10 % 11) % 10
            if int(digits[length]) != check:
                return False
        return True
    return False


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class Profile(Strict):
    expected_version: int = Field(ge=0)
    tax_id: str = Field(min_length=11, max_length=18)
    channel: Literal['Salão de Beleza', 'Perfumaria']
    profile: Literal['Premium', 'Especializado', 'Bairro']
    management: Literal['Familiar', 'Rede', 'Outra']
    focus: Literal['Coloração', 'Tratamento', 'Ambos']
    portfolio: Literal['Estratégico', 'Potencial', 'Combate', 'Tático']
    started_on: date | None = None
    network: str = Field(default='', max_length=180)
    needs: str = Field(default='', max_length=2000)
    @model_validator(mode='after')
    def validate_tax(self):
        if not valid_tax_id(self.tax_id):
            raise ValueError('CPF/CNPJ inválido')
        self.tax_id = re.sub(r'\D', '', self.tax_id)
        if self.started_on and self.started_on > datetime.now(TZ).date():
            raise ValueError('Início de operação não pode ser futuro')
        return self


class Interaction(Strict):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), max_length=100)
    client_id: str = Field(min_length=1, max_length=100)
    needs: str = Field(min_length=1, max_length=2000)
    observation: str = Field(default='', max_length=2000)
    open_question: str = Field(default='', max_length=1000)
    paraphrase: str = Field(default='', max_length=2000)
    confirmed: bool = False
    expectations: str = Field(default='', max_length=2000)
    next_action: str = Field(min_length=1, max_length=1000)
    due: date


class Item(Strict):
    brand: str = Field(min_length=1, max_length=120)
    sku: str = Field(min_length=1, max_length=120)
    amount: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    units: Decimal = Field(gt=0, max_digits=18, decimal_places=4)
    portfolio: Literal['Estratégico', 'Potencial', 'Combate', 'Tático', 'Não classificado'] = 'Não classificado'


class Document(Strict):
    source: str = Field(min_length=1, max_length=100)
    external_id: str = Field(min_length=1, max_length=120)
    stage: Literal['sell_in', 'sell_through', 'sell_out']
    client_id: str = Field(min_length=1, max_length=100)
    order_id: str | None = Field(default=None, max_length=100)
    business_date: date
    kind: Literal['sale', 'return'] = 'sale'
    original_id: str | None = Field(default=None, max_length=100)
    items: list[Item] = Field(min_length=1, max_length=5000)
    currency: Literal['BRL'] = 'BRL'
    @model_validator(mode='after')
    def consistent(self):
        if (self.kind == 'return') != bool(self.original_id):
            raise ValueError('Devolução exige documento original; venda não aceita original')
        if self.business_date > datetime.now(TZ).date():
            raise ValueError('Faturamento futuro não é realizado')
        if len({(i.brand, i.sku) for i in self.items}) != len(self.items):
            raise ValueError('Consolide linhas repetidas do mesmo SKU/marca')
        return self


def store_document(con, user, data):
    client(con, user, data.client_id)
    payload = data.model_dump(mode='json')
    key = str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps([data.source, data.stage, data.external_id])))
    # Serializes identical imports AND all returns to an original document.
    con.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (data.original_id or key,))
    old = con.execute('SELECT payload FROM l2_commercial_documents WHERE id=%s', (key,)).fetchone()
    if old:
        if old[0] != payload:
            raise HTTPException(409, 'Documento já importado com conteúdo diferente')
        return {'id': key, 'already_imported': True}
    if data.order_id:
        order = con.execute("SELECT payload FROM entities WHERE kind='order' AND id=%s", (data.order_id,)).fetchone()
        if not order or order[0].get('clientId') != data.client_id or order[0].get('status') != 'Faturado':
            raise HTTPException(422, 'Vincule um pedido faturado desta porta')
    if data.original_id:
        original = con.execute('SELECT payload FROM l2_commercial_documents WHERE id=%s FOR UPDATE', (data.original_id,)).fetchone()
        if not original or original[0]['kind'] != 'sale' or any(original[0][k] != payload[k] for k in ('client_id', 'stage', 'currency')):
            raise HTTPException(422, 'Documento original incompatível')
        if data.business_date < date.fromisoformat(original[0]['business_date']):
            raise HTTPException(422, 'Devolução anterior ao faturamento')
        previous = [r[0] for r in con.execute('SELECT payload FROM l2_commercial_documents WHERE original_id=%s', (data.original_id,))]
        for item in data.items:
            base = next((x for x in original[0]['items'] if (x['brand'], x['sku']) == (item.brand, item.sku)), None)
            returned = [x for p in previous for x in p['items'] if (x['brand'], x['sku']) == (item.brand, item.sku)]
            if not base or item.units + sum(Decimal(x['units']) for x in returned) > Decimal(base['units']) or item.amount + sum(Decimal(x['amount']) for x in returned) > Decimal(base['amount']):
                raise HTTPException(422, 'Devolução excede quantidade ou valor do saldo original')
    con.execute('INSERT INTO l2_commercial_documents(id,source,external_id,stage,client_id,order_id,business_date,kind,original_id,payload,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (key, data.source, data.external_id, data.stage, data.client_id, data.order_id, data.business_date, data.kind, data.original_id, Jsonb(payload), user))
    audit(con, user, 'commercial_document', key, 'import')
    return {'id': key, 'already_imported': False}


def selected(con, user, end, brand='', channel='', region=''):
    predicate, args = scope_sql(user)
    predicates, filters = [predicate], list(args)
    if channel:
        predicates.append("p.payload->>'channel'=%s"); filters.append(channel)
    if region:
        predicates.append("upper(coalesce(c.payload->>'state',''))=%s"); filters.append(region.upper())
    where = ' AND '.join(predicates)
    clients = con.execute("SELECT c.id,c.payload,p.payload,p.version FROM entities c LEFT JOIN l2_commercial_profiles p ON p.client_id=c.id WHERE c.kind='client' AND " + where + ' ORDER BY c.id', filters).fetchall()
    # SQL restricts document rows to the same authorized, filtered client universe.
    rows = con.execute("SELECT d.id,d.payload FROM l2_commercial_documents d JOIN entities c ON c.kind='client' AND c.id=d.client_id LEFT JOIN l2_commercial_profiles p ON p.client_id=c.id WHERE d.business_date<%s AND " + where + ' ORDER BY d.business_date,d.id', [end] + filters).fetchall()
    docs = []
    for identifier, payload in rows:
        items = [x for x in payload['items'] if not brand or x['brand'] == brand]
        if not items:
            continue
        sign = -1 if payload['kind'] == 'return' else 1
        docs.append({**payload, 'id': identifier, 'items': items,
                     'date': date.fromisoformat(payload['business_date']), 'status': 'posted',
                     'amount': sign * sum(Decimal(x['amount']) for x in items),
                     'units': sign * sum(Decimal(x['units']) for x in items)})
    brands = {i['brand'] for d in docs for i in d['items']} | ({brand} if brand else set())
    covered = {r[0] for r in con.execute('SELECT brand FROM l2_commercial_coverage WHERE history_complete')}
    complete = bool(brands) and brands <= covered
    return clients, docs, complete


def ratio(a, b):
    return a / b if b else None


def calculate(clients, docs, start, end, complete):
    ids = [c[0] for c in clients]
    monthly = project(docs, ids, start, end, complete)
    annual_start = months_before(end, 12)
    annual = project(docs, ids, annual_start, end, complete)
    # Source documents of one order count once, partial invoices do not inflate ticket.
    period_sales = [d for d in docs if start <= d['date'] < end and d['stage'] == 'sell_through' and d['kind'] == 'sale']
    orders = {('order', d['order_id']) if d.get('order_id') else ('document', d['source'], d['external_id']) for d in period_sales}
    monthly['orders'] = len(orders)
    monthly['average_order'] = ratio(monthly['sell_through'], len(orders))
    monthly['productivity_period'] = monthly.pop('productivity')
    monthly['upa_period'] = monthly.pop('upa')
    monthly['productivity_12m'] = annual['productivity']
    monthly['upa_12m'] = annual['upa']
    monthly['average_unit_price_12m'] = annual['average_unit_price']
    monthly['sell_through_12m'] = annual['sell_through']
    monthly['restarts_12m'] = annual['restarts_period']
    stages = {stage: sum((d['amount'] for d in docs if d['stage'] == stage and start <= d['date'] < end), Decimal(0)) for stage in STAGES}
    present = {d['stage'] for d in docs if start <= d['date'] < end}
    stages = {k: v if k in present else None for k, v in stages.items()}
    stages['gmv_sell_out_net'] = stages['sell_out']
    latest = {}
    for d in docs:
        if d['stage'] == 'sell_through' and d['kind'] == 'sale':
            latest[d['client_id']] = max(latest.get(d['client_id'], date.min), d['date'])
    retention = []
    for identifier, c, profile, version in clients:
        last = latest.get(identifier)
        if not last:
            continue
        expiry = months_before(last, -12)
        remaining = (expiry - (end - timedelta(days=1))).days
        if remaining <= 60:
            retention.append({'client_id': identifier, 'name': c.get('name', identifier), 'last_purchase': str(last),
                              'days_to_cessation': remaining, 'status': 'Cessado' if last < annual_start else 'Em risco'})
    products = {}
    regional = {}
    by_client = {x[0]: x for x in clients}
    for d in docs:
        if not start <= d['date'] < end:
            continue
        c = by_client[d['client_id']]
        group = (c[1].get('state', 'Não informado'), (c[2] or {}).get('channel', 'Não informado'))
        row = regional.setdefault(group, {'region': group[0], 'channel': group[1], **{k: None for k in STAGES}})
        row[d['stage']] = (row[d['stage']] or Decimal(0)) + d['amount']
        if d['stage'] != 'sell_through':
            continue
        for item in d['items']:
            k = (item['brand'], item['sku'])
            p = products.setdefault(k, {'brand': k[0], 'sku': k[1], 'amount': Decimal(0), 'units': Decimal(0), 'portfolio': item.get('portfolio', 'Não classificado')})
            sign = -1 if d['kind'] == 'return' else 1
            p['amount'] += sign * Decimal(item['amount']); p['units'] += sign * Decimal(item['units'])
    total = sum(max(p['amount'], 0) for p in products.values())
    cumulative = Decimal(0)
    for p in sorted(products.values(), key=lambda p: p['amount'], reverse=True):
        before = ratio(cumulative, total)
        p['abc'] = 'Sem base' if total <= 0 or p['amount'] <= 0 else 'A' if before < Decimal('.8') else 'B' if before < Decimal('.95') else 'C'
        cumulative += max(p['amount'], 0)
        p['share_percent'] = ratio(max(p['amount'], 0) * 100, total)
    adherence = ratio(sum(max(p['amount'], 0) for p in products.values() if p['portfolio'] in ('Estratégico', 'Potencial')) * 100, total)
    for row in regional.values():
        row['gmv_sell_out_net'] = row['sell_out']
    return {'metrics': monthly, 'stages': stages, 'retention': retention,
            'portfolio': sorted(products.values(), key=lambda p: p['amount'], reverse=True),
            'strategic_potential_share_percent': adherence, 'regional': list(regional.values()),
            'period': {'start': str(start), 'end_exclusive': str(end), 'annual_start': str(annual_start)},
            'coverage': {'history_complete': complete, 'sell_out': 'Parcial: somente documentos recebidos' if 'sell_out' in present else 'Não informado'},
            'formula_version': '2026-11-02-v2', 'generated_at': datetime.now(TZ).isoformat()}


def report(con, user, start, end, brand='', channel='', region=''):
    if start >= end or (end - start).days > 366:
        raise HTTPException(422, 'Período deve conter de 1 a 366 dias; fim é exclusivo')
    clients, docs, complete = selected(con, user, end, brand, channel, region)
    result = calculate(clients, docs, start, end, complete)
    result['filters'] = {'brand': brand, 'channel': channel, 'region': region}
    # Baselines remain private to the creator, including after portfolio changes:
    # snapshots only contain values; never expose another user's historical totals.
    baselines = con.execute('SELECT payload FROM l2_commercial_baselines WHERE author=%s ORDER BY created_at DESC LIMIT 20', (user,)).fetchall()
    result['baselines'] = [x[0] for x in baselines if x[0].get('filters') == result['filters'] and x[0].get('scope_hash') == scope_hash(clients)]
    return result


@router.get('/status')
def status(authorization: str | None = Header(default=None)):
    user = services['auth'](authorization)
    return {'enabled': enabled(), 'finance': 'finance' in services['sectors_for'](user),
            'manage': bool({'management', 'admin'} & services['sectors_for'](user)),
            'bank_api': False, 'boleto_issuance': False, 'bank_message': 'API e emissão dependem de contrato e credenciais do banco'}


@router.get('/ports')
def ports(q: str = '', channel: str = '', offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100), authorization: str | None = Header(default=None)):
    user = actor(authorization)
    predicate, args = scope_sql(user)
    where = "c.kind='client' AND " + predicate
    if q:
        where += " AND (c.payload->>'name' ILIKE %s OR regexp_replace(coalesce(c.payload->>'taxId',''),'[^0-9]','','g') LIKE %s)"
        args += ['%' + q + '%', '%' + re.sub(r'\D', '', q) + '%' if re.search(r'\d', q) else '__no_tax_match__']
    if channel:
        where += " AND p.payload->>'channel'=%s"; args.append(channel)
    with services['db']() as con:
        total = con.execute("SELECT count(*) FROM entities c LEFT JOIN l2_commercial_profiles p ON p.client_id=c.id WHERE " + where, args).fetchone()[0]
        rows = con.execute("SELECT c.id,c.payload,p.payload,p.version FROM entities c LEFT JOIN l2_commercial_profiles p ON p.client_id=c.id WHERE " + where + ' ORDER BY c.payload->>\'name\',c.id LIMIT %s OFFSET %s', args + [limit, offset]).fetchall()
    return {'total': total, 'ports': [{'id': x[0], 'name': x[1].get('name', x[0]), 'tax_id': x[1].get('taxId', ''), 'profile': x[2], 'version': x[3] or 0} for x in rows]}


@router.put('/ports/{identifier}/profile')
def profile(identifier: str, data: Profile, authorization: str | None = Header(default=None)):
    user = actor(authorization)
    services['require_sector'](user, 'clients_edit', 'management', 'admin')
    with services['db']() as con:
        existing_client = client(con, user, identifier)
        existing_tax = re.sub(r'\D','',str(existing_client.get('taxId') or ''))
        if existing_tax and existing_tax != data.tax_id:
            raise HTTPException(409, 'CPF/CNPJ diverge do cadastro principal; corrija a identificação antes de classificar')
        con.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('tax:' + data.tax_id,))
        duplicate = con.execute("SELECT id FROM entities WHERE kind='client' AND id<>%s AND regexp_replace(coalesce(payload->>'taxId',''),'[^0-9]','','g')=%s LIMIT 1", (identifier, data.tax_id)).fetchone()
        duplicate_profile = con.execute('SELECT client_id FROM l2_commercial_profiles WHERE tax_id=%s AND client_id<>%s', (data.tax_id, identifier)).fetchone()
        if duplicate or duplicate_profile:
            raise HTTPException(409, 'CPF/CNPJ já cadastrado em outra porta; solicite unificação')
        con.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('profile:' + identifier,))
        old = con.execute('SELECT version FROM l2_commercial_profiles WHERE client_id=%s FOR UPDATE', (identifier,)).fetchone()
        if (old[0] if old else 0) != data.expected_version:
            raise HTTPException(409, 'Ficha alterada em outro aparelho; consulte novamente')
        payload = data.model_dump(mode='json', exclude={'expected_version'})
        con.execute('INSERT INTO l2_commercial_profiles(client_id,tax_id,payload) VALUES(%s,%s,%s) ON CONFLICT(client_id) DO UPDATE SET tax_id=excluded.tax_id,payload=excluded.payload,version=l2_commercial_profiles.version+1,updated_at=now()', (identifier, data.tax_id, Jsonb(payload)))
        if not existing_tax:
            con.execute("UPDATE entities SET payload=payload||%s,updated_at=now() WHERE kind='client' AND id=%s", (Jsonb({'taxId':data.tax_id}),identifier))
        audit(con, user, 'commercial_profile', identifier, 'save')
    return {'version': data.expected_version + 1}


@router.get('/interactions/{identifier}')
def interactions(identifier: str, authorization: str | None = Header(default=None)):
    user = actor(authorization)
    with services['db']() as con:
        client(con, user, identifier)
        rows = con.execute('SELECT payload,author,created_at FROM l2_commercial_interactions WHERE client_id=%s ORDER BY created_at DESC LIMIT 100', (identifier,)).fetchall()
    return [{'data': x[0], 'author': x[1], 'created_at': x[2]} for x in rows]


@router.post('/interactions')
def interaction(data: Interaction, authorization: str | None = Header(default=None)):
    user = actor(authorization)
    with services['db']() as con:
        client(con, user, data.client_id)
        con.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (data.id,))
        old = con.execute('SELECT payload,author FROM l2_commercial_interactions WHERE id=%s', (data.id,)).fetchone()
        payload = data.model_dump(mode='json')
        if old:
            if old != (payload, user):
                raise HTTPException(409, 'Identificador de interação já utilizado')
        else:
            con.execute('INSERT INTO l2_commercial_interactions(id,client_id,author,payload) VALUES(%s,%s,%s,%s)', (data.id, data.client_id, user, Jsonb(payload)))
            audit(con, user, 'commercial_interaction', data.id, 'create')
    return {'id': data.id}


@router.post('/documents')
def document(data: Document, authorization: str | None = Header(default=None)):
    user = actor(authorization, 'manage')
    with services['db']() as con:
        return store_document(con, user, data)


class OrderStage(Strict):
    stage: Literal['sell_in', 'sell_through', 'sell_out']


@router.post('/orders/{identifier}/recognize')
def recognize(identifier: str, data: OrderStage, authorization: str | None = Header(default=None)):
    user = actor(authorization, 'manage')
    with services['db']() as con:
        con.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('recognize:'+identifier,))
        row = con.execute("SELECT payload FROM entities WHERE kind='order' AND id=%s", (identifier,)).fetchone()
        if not row or row[0].get('status') != 'Faturado':
            raise HTTPException(422, 'Selecione um pedido faturado')
        o = row[0]
        items = {}
        for item in o.get('items', []):
            brand, sku = item.get('brand') or o.get('brand'), item.get('sku')
            if not brand or not sku:
                raise HTTPException(422, 'Pedido sem marca/SKU completo')
            quantity = Decimal(str(item.get('quantity', 0)))
            amount = Decimal(str(item.get('subtotal', quantity * Decimal(str(item.get('unitPrice', 0))))))
            k = (brand, sku)
            target = items.setdefault(k, {'brand': brand, 'sku': sku, 'units': Decimal(0), 'amount': Decimal(0)})
            target['units'] += quantity; target['amount'] += amount
        total = sum(x['amount'] for x in items.values())
        if total != Decimal(str(o.get('amount', 0))):
            raise HTTPException(422, 'Total dos itens difere do faturado; concilie antes de reconhecer')
        day = str(o.get('billedAt') or o.get('date', ''))[:10]
        try:
            normalized = Document(source='L2-order', external_id=identifier, stage=data.stage, client_id=o['clientId'], order_id=identifier, business_date=day, items=list(items.values()))
        except (ValueError, KeyError):
            raise HTTPException(422, 'Pedido sem dados suficientes para reconhecimento')
        # One legacy order may be classified into exactly one stage.
        prior = con.execute("SELECT stage FROM l2_commercial_documents WHERE source='L2-order' AND external_id=%s", (identifier,)).fetchone()
        if prior and prior[0] != data.stage:
            raise HTTPException(409, 'Pedido já reconhecido em outra etapa')
        return store_document(con, user, normalized)


class Coverage(Strict):
    brand: str = Field(min_length=1, max_length=120)
    history_complete: bool
    evidence: str = Field(min_length=20, max_length=2000)


@router.put('/coverage')
def coverage(data: Coverage, authorization: str | None = Header(default=None)):
    user = actor(authorization, 'manage')
    with services['db']() as con:
        con.execute('INSERT INTO l2_commercial_coverage(brand,history_complete,evidence,author) VALUES(%s,%s,%s,%s) ON CONFLICT(brand) DO UPDATE SET history_complete=excluded.history_complete,evidence=excluded.evidence,author=excluded.author,updated_at=now()', (data.brand, data.history_complete, data.evidence, user))
        audit(con, user, 'commercial_coverage', data.brand, 'attest')
    return {'saved': True}


@router.get('/indicators')
def indicators(start: date, end: date, brand: str = '', channel: str = '', region: str = '', authorization: str | None = Header(default=None)):
    user = actor(authorization)
    with services['db']() as con:
        return report(con, user, start, end, brand, channel, region)


class Baseline(Strict):
    name: str = Field(min_length=1, max_length=180)
    start: date
    end: date
    brand: str = Field(default='', max_length=120)
    channel: str = Field(default='', max_length=60)
    region: str = Field(default='', max_length=2)


@router.post('/baselines')
def baseline(data: Baseline, authorization: str | None = Header(default=None)):
    user = actor(authorization, 'manage')
    with services['db']() as con:
        r = report(con, user, data.start, data.end, data.brand, data.channel, data.region)
        payload = {'name': data.name, 'filters': r['filters'], 'period': r['period'], 'metrics': json.loads(json.dumps(r['metrics'], default=str)), 'formula_version': r['formula_version'], 'captured_at': r['generated_at']}
        selected_clients,_,_ = selected(con,user,data.end,data.brand,data.channel,data.region)
        payload['scope_hash'] = scope_hash(selected_clients)
        key = str(uuid.uuid4())
        con.execute('INSERT INTO l2_commercial_baselines(id,author,payload) VALUES(%s,%s,%s)', (key, user, Jsonb(payload)))
        audit(con, user, 'commercial_baseline', key, 'capture')
    return {'id': key, **payload}

# Bank reconciliation: payments never mutate invoice recognition or order revenue.
class Receivable(Strict):
    document_id: str = Field(min_length=1, max_length=100)
    reference: str = Field(min_length=1, max_length=120, pattern=r'^[A-Za-z0-9._/-]+$')
    bank_account: str = Field(min_length=1, max_length=180)
    due_date: date
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)


class PasswordProof(Strict):
    password: str = Field(min_length=1, max_length=256)


class OTP(Strict):
    code: str = Field(pattern=r'^\d{6}$')


def cipher():
    from cryptography.fernet import Fernet
    key = os.getenv('L2_COMMERCIAL_MFA_KEY', '')
    if not key:
        raise HTTPException(503, 'Proteção MFA precisa ser configurada no ambiente')
    try:
        return Fernet(key.encode())
    except ValueError:
        raise HTTPException(503, 'Configuração MFA inválida')


def totp(secret, counter):
    digest = hmac.new(base64.b32decode(secret), struct.pack('>Q', counter), hashlib.sha1).digest()
    offset = digest[-1] & 15
    return f'{(struct.unpack(">I", digest[offset:offset+4])[0] & 0x7fffffff) % 1000000:06d}'


def verify_mfa(user, code, pending=False):
    if not code or not re.fullmatch(r'\d{6}', code):
        raise HTTPException(403, 'Informe o código MFA de seis dígitos')
    accepted, blocked = False, False
    with services['db']() as con:
        row = con.execute('SELECT secret,enabled,counter,failed,blocked_until FROM l2_commercial_mfa WHERE username=%s FOR UPDATE', (user,)).fetchone()
        if not row or (not pending and not row[1]):
            raise HTTPException(403, 'Ative MFA antes de operar a integração bancária')
        if row[4] and row[4] > datetime.now(row[4].tzinfo):
            blocked = True
        else:
            secret = cipher().decrypt(row[0].encode()).decode()
            current = int(time.time()) // 30
            match = next((n for n in (current - 1, current, current + 1) if n > row[2] and hmac.compare_digest(totp(secret, n), code)), None)
            if match is not None:
                con.execute('UPDATE l2_commercial_mfa SET enabled=true,counter=%s,failed=0,blocked_until=NULL WHERE username=%s', (match, user))
                accepted = True
            else:
                con.execute("UPDATE l2_commercial_mfa SET failed=CASE WHEN blocked_until<now() THEN 1 ELSE failed+1 END,blocked_until=CASE WHEN failed>=4 THEN now()+interval '5 minutes' ELSE blocked_until END WHERE username=%s", (user,))
    # Raise AFTER transaction commits to preserve replay protection and attempts.
    if not accepted:
        raise HTTPException(429 if blocked else 403, 'MFA temporariamente bloqueado' if blocked else 'Código MFA inválido ou já utilizado')


@router.post('/security/mfa/enroll')
def mfa_enroll(data: PasswordProof, authorization: str | None = Header(default=None)):
    user = actor(authorization, 'finance')
    with services['db']() as con:
        con.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('mfa:'+user,))
        row = con.execute('SELECT password_hash FROM app_users WHERE username=%s AND active', (user,)).fetchone()
        if not row or not services['password_ok'](data.password, row[0]):
            raise HTTPException(403, 'Confirme sua senha atual')
        old = con.execute('SELECT enabled FROM l2_commercial_mfa WHERE username=%s FOR UPDATE', (user,)).fetchone()
        if old and old[0]:
            raise HTTPException(409, 'MFA já ativado; recuperação exige procedimento administrativo')
        secret = base64.b32encode(secrets.token_bytes(20)).decode()
        encrypted = cipher().encrypt(secret.encode()).decode()
        con.execute('INSERT INTO l2_commercial_mfa(username,secret) VALUES(%s,%s) ON CONFLICT(username) DO UPDATE SET secret=excluded.secret,counter=-1', (user, encrypted))
        audit(con, user, 'commercial_mfa', user, 'enrollment_started')
    from urllib.parse import quote
    return {'secret': secret, 'uri': f'otpauth://totp/L2One:{quote(user)}?secret={secret}&issuer=L2One&digits=6&period=30'}


@router.post('/security/mfa/confirm')
def mfa_confirm(data: OTP, authorization: str | None = Header(default=None)):
    user = actor(authorization, 'finance')
    verify_mfa(user, data.code, pending=True)
    return {'enabled': True}


@router.post('/finance/receivables')
def receivable(data: Receivable, authorization: str | None = Header(default=None), x_totp: str | None = Header(default=None)):
    user = actor(authorization, 'finance')
    verify_mfa(user, x_totp)
    with services['db']() as con:
        row = con.execute('SELECT client_id,payload FROM l2_commercial_documents WHERE id=%s FOR UPDATE', (data.document_id,)).fetchone()
        if not row or row[1]['stage'] not in ('sell_in', 'sell_through') or row[1]['kind'] != 'sale':
            raise HTTPException(422, 'Título exige venda Sell In ou Sell Through reconhecida')
        client(con, user, row[0])
        total = sum(Decimal(x['amount']) for x in row[1]['items'])
        existing = con.execute('SELECT id,document_id,due_date,amount FROM l2_commercial_receivables WHERE bank_account=%s AND reference=%s', (data.bank_account, data.reference)).fetchone()
        if existing:
            if existing[1:] != (data.document_id, data.due_date, data.amount):
                raise HTTPException(409, 'Referência já usada por outro título')
            return {'id': existing[0], 'already_created': True}
        allocated = con.execute('SELECT coalesce(sum(amount),0) FROM l2_commercial_receivables WHERE document_id=%s', (data.document_id,)).fetchone()[0]
        if allocated + data.amount > total:
            raise HTTPException(422, 'Parcelas excedem o valor da venda')
        key = str(uuid.uuid4())
        con.execute('INSERT INTO l2_commercial_receivables(id,document_id,client_id,bank_account,reference,due_date,amount) VALUES(%s,%s,%s,%s,%s,%s,%s)', (key, data.document_id, row[0], data.bank_account, data.reference, data.due_date, data.amount))
        audit(con, user, 'commercial_receivable', key, 'create')
    return {'id': key, 'already_created': False}


@router.get('/finance/receivables')
def receivables(authorization: str | None = Header(default=None)):
    user = actor(authorization, 'finance')
    predicate, args = scope_sql(user)
    with services['db']() as con:
        rows = con.execute("SELECT r.id,r.document_id,r.client_id,c.payload->>'name',r.bank_account,r.reference,r.due_date,r.amount,r.paid FROM l2_commercial_receivables r JOIN entities c ON c.kind='client' AND c.id=r.client_id WHERE " + predicate + ' ORDER BY r.due_date,r.id LIMIT 500', args).fetchall()
    return [dict(zip(('id','document_id','client_id','name','bank_account','reference','due_date','amount','paid'), r), overdue=r[6] < datetime.now(TZ).date() and r[8] < r[7], outstanding=r[7]-r[8]) for r in rows]


def parse_ofx(content):
    """Bounded OFX 1.x SGML / 2.x XML parser. No entity expansion or network."""
    if len(content.encode()) > 2_000_000 or re.search(r'<!DOCTYPE|<!ENTITY', content, re.I):
        raise ValueError('OFX excede limite ou contém declarações XML proibidas')
    def tag(block, name, required=True):
        match = re.search(r'<' + name + r'>\s*([^<\r\n]+)', block, re.I)
        if not match and required:
            raise ValueError('Campo OFX ausente: ' + name)
        return match.group(1).strip() if match else ''
    if tag(content, 'CURDEF') != 'BRL':
        raise ValueError('Extrato deve estar em BRL')
    # Restrict one statement/account per import so transactions never cross accounts.
    if len(re.findall(r'<STMTRS>', content, re.I)) != 1:
        raise ValueError('Importe exatamente um extrato bancário por vez')
    account = '/'.join([tag(content, 'BANKID'), tag(content, 'BRANCHID', False), tag(content, 'ACCTID')])
    blocks = re.findall(r'<STMTTRN>(.*?)</STMTTRN>', content, re.I | re.S)
    if not blocks or len(blocks) > 10000:
        raise ValueError('Extrato vazio ou excede 10.000 lançamentos')
    result = []
    seen = set()
    for block in blocks:
        amount = Decimal(tag(block, 'TRNAMT'))
        if not amount.is_finite() or amount != amount.quantize(Decimal('.01')):
            raise ValueError('Valor OFX inválido')
        identifier = tag(block, 'FITID')
        day = tag(block, 'DTPOSTED')[:8]
        if not re.fullmatch(r'\d{8}', day) or identifier in seen:
            raise ValueError('Data inválida ou FITID duplicado')
        seen.add(identifier)
        reference = tag(block, 'REFNUM', False) or tag(block, 'CHECKNUM', False)
        result.append({'account': account, 'external_id': identifier, 'business_date': date(int(day[:4]), int(day[4:6]), int(day[6:8])), 'amount': amount, 'reference': reference})
    return result


def bank_transaction(con, user, transaction):
    key = str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps([transaction['account'], transaction['external_id']])))
    con.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (key,))
    previous = con.execute('SELECT business_date,amount,reference FROM l2_commercial_bank_transactions WHERE id=%s', (key,)).fetchone()
    if previous:
        if previous != (transaction['business_date'], transaction['amount'], transaction['reference']):
            raise HTTPException(409, 'Lançamento bancário repetido com conteúdo diferente')
        allocation = con.execute('SELECT receivable_id FROM l2_commercial_allocations WHERE transaction_id=%s', (key,)).fetchone()
        return {'id': key, 'status': 'Conciliado' if allocation else 'Pendente', 'duplicate': True}
    con.execute('INSERT INTO l2_commercial_bank_transactions(id,account,external_id,business_date,amount,reference) VALUES(%s,%s,%s,%s,%s,%s)', (key, transaction['account'], transaction['external_id'], transaction['business_date'], transaction['amount'], transaction['reference']))
    candidates = con.execute('SELECT id,amount-paid,client_id FROM l2_commercial_receivables WHERE bank_account=%s AND reference=%s AND amount-paid=%s AND amount>paid FOR UPDATE', (transaction['account'], transaction['reference'], transaction['amount'])).fetchall() if transaction['reference'] and transaction['amount'] > 0 else []
    # Never infer matches from description, name, approximate amount or CPF alone.
    matched = len(candidates) == 1
    if matched:
        candidate = candidates[0]
        if user != 'bank-webhook':
            client(con, user, candidate[2])
        con.execute('UPDATE l2_commercial_receivables SET paid=paid+%s WHERE id=%s', (transaction['amount'], candidate[0]))
        con.execute('INSERT INTO l2_commercial_allocations(transaction_id,receivable_id,amount,author) VALUES(%s,%s,%s,%s)', (key, candidate[0], transaction['amount'], user))
    audit(con, user, 'commercial_bank', key, 'reconcile' if matched else 'pending')
    return {'id': key, 'status': 'Conciliado' if matched else 'Pendente', 'duplicate': False}


class OFX(Strict):
    content: str = Field(min_length=1, max_length=2_000_000)


@router.post('/finance/ofx/preview')
def ofx_preview(data: OFX, authorization: str | None = Header(default=None)):
    actor(authorization, 'finance')
    try:
        return {'transactions': parse_ofx(data.content)}
    except (ValueError, ArithmeticError) as exc:
        raise HTTPException(422, str(exc))


@router.post('/finance/ofx/confirm')
def ofx_confirm(data: OFX, authorization: str | None = Header(default=None), x_totp: str | None = Header(default=None)):
    user = actor(authorization, 'finance')
    verify_mfa(user, x_totp)
    try:
        transactions = parse_ofx(data.content)
    except (ValueError, ArithmeticError) as exc:
        raise HTTPException(422, str(exc))
    with services['db']() as con:
        return {'results': [bank_transaction(con, user, t) for t in transactions]}


class BankEvent(Strict):
    account: str = Field(min_length=1, max_length=180)
    external_id: str = Field(min_length=1, max_length=120)
    business_date: date
    amount: Decimal = Field(max_digits=18, decimal_places=2)
    reference: str = Field(default='', max_length=120)
    currency: Literal['BRL'] = 'BRL'
    @model_validator(mode='after')
    def valid_date(self):
        if self.business_date > datetime.now(TZ).date():
            raise ValueError('Evento bancário futuro não é recebimento confirmado')
        return self


@router.post('/finance/webhook')
async def bank_webhook(request: Request):
    # This is an L2 signed-adapter contract, NOT an invented bank-specific API.
    key = os.getenv('L2_BANK_WEBHOOK_SECRET', '')
    if not enabled() or len(key) < 32:
        raise HTTPException(503, 'Adaptador bancário não configurado')
    body = b''
    async for chunk in request.stream():
        body += chunk
        if len(body) > 16384:
            raise HTTPException(413, 'Evento excede limite')
    stamp = request.headers.get('X-L2-Timestamp', '')
    signature = request.headers.get('X-L2-Signature', '')
    if not stamp.isdigit() or abs(time.time() - int(stamp)) > 300:
        raise HTTPException(401, 'Timestamp inválido')
    expected = hmac.new(key.encode(), stamp.encode() + b'.' + body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(401, 'Assinatura inválida')
    try:
        data = BankEvent.model_validate_json(body)
    except ValueError:
        raise HTTPException(422, 'Evento bancário inválido')
    with services['db']() as con:
        return bank_transaction(con, 'bank-webhook', data.model_dump())


@router.post('/finance/receivables/{identifier}/boleto')
def boleto(identifier: str, authorization: str | None = Header(default=None)):
    actor(authorization, 'finance')
    raise HTTPException(503, 'Emissão não ativada: informe banco/provedor, contrato de cobrança e credenciais no ambiente seguro. Nenhum boleto fictício será emitido.')


METRIC_LABELS = {'doors': 'Portas', 'active_12m': 'Base ativa 12m', 'productivity_12m': 'Produtividade 12m', 'activity_percent': 'Atividade %', 'starts_12m': 'Inícios 12m', 'restarts_12m': 'Reinícios 12m', 'ceased_12m': 'Cessados 12m', 'upa_12m': 'UPA 12m', 'average_order': 'Ticket por pedido no período', 'average_unit_price': 'Preço unitário no período', 'sell_through': 'Sell Through período', 'sell_through_12m': 'Sell Through 12m'}


def export_xlsx(result):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    wb = Workbook(); wb.remove(wb.active)
    data = [('Indicadores', [{'Indicador': METRIC_LABELS.get(k,k), 'Valor': v} for k,v in result['metrics'].items()]),
            ('Retencao', result['retention']), ('Canais e regioes', result['regional']), ('Portfolio', result['portfolio']),
            ('Contexto', [{'Campo': k, 'Valor': str(v)} for k,v in {**result['period'], **result['filters'], **result['coverage'], 'formula_version': result['formula_version'], 'generated_at': result['generated_at']}.items()])]
    for title, rows in data:
        sheet = wb.create_sheet(title)
        keys = list(rows[0]) if rows else ['Sem registros']
        sheet.append(keys)
        for row in rows:
            values = [row.get(k) for k in keys]
            sheet.append(values)
            for cell in sheet[sheet.max_row]:
                if isinstance(cell.value, str):
                    # Prevent formula injection in client/SKU names from external sources.
                    cell.data_type = 's'
        for cell in sheet[1]:
            cell.font = Font(color='FFFFFF', bold=True); cell.fill = PatternFill('solid', fgColor='174A58')
        sheet.freeze_panes = 'A2'; sheet.auto_filter.ref = sheet.dimensions
        for column in sheet.columns:
            sheet.column_dimensions[column[0].column_letter].width = min(65, max(20, max(len(str(c.value or '')) for c in column) + 2))
    output = io.BytesIO(); wb.save(output); return output.getvalue()


def export_pdf(result):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
    from xml.sax.saxutils import escape
    output = io.BytesIO(); styles = getSampleStyleSheet()
    story = [Paragraph('L2 One | Planejamento Comercial', styles['Title']),
             Paragraph(escape('Período: ' + result['period']['start'] + ' até ' + result['period']['end_exclusive'] + ' (fim exclusivo)'), styles['Normal']),
             Paragraph(escape('Filtros: ' + str(result['filters']) + ' | Histórico completo: ' + str(result['coverage']['history_complete'])), styles['Normal']), Spacer(1,12)]
    def table(headers, rows, widths):
        data = [[Paragraph(escape(str(x)), styles['Normal']) for x in headers]]
        data += [[Paragraph(escape('—' if x is None else str(x)), styles['Normal']) for x in row] for row in rows]
        t = Table(data, colWidths=widths, repeatRows=1, hAlign='LEFT')
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#dce9ed')),('GRID',(0,0),(-1,-1),.3,colors.lightgrey),('VALIGN',(0,0),(-1,-1),'TOP'),('BOTTOMPADDING',(0,0),(-1,-1),7)]))
        story.append(t); story.append(Spacer(1,14))
    table(['Indicador','Valor'], [(label, result['metrics'].get(key)) for key,label in METRIC_LABELS.items()], [360,380])
    for title, headers, rows, widths in [
        ('Drenagem e retenção', ['Porta','Última compra','Dias até cessar','Status'], [(x['name'],x['last_purchase'],x['days_to_cessation'],x['status']) for x in result['retention']], [310,140,140,150]),
        ('Etapas por região e canal', ['Região','Canal','Sell In','Sell Through','Sell Out / GMV líquido'], [(x['region'],x['channel'],x['sell_in'],x['sell_through'],x['sell_out']) for x in result['regional']], [90,180,150,150,170]),
        ('Portfólio', ['Marca / SKU','Receita','Unidades','ABC','Classe'], [(x['brand']+' / '+x['sku'],x['amount'],x['units'],x['abc'],x['portfolio']) for x in result['portfolio']], [280,110,110,70,170])]:
        story.append(PageBreak()); story.append(Paragraph(title, styles['Heading1']))
        table(headers, rows or [['Sem registros'] + [''] * (len(headers)-1)], widths)
    story.append(Paragraph(escape('Sell Out: ' + result['coverage']['sell_out'] + '. GMV líquido usa exclusivamente Sell Out; etapas da cadeia não são somadas. Versão: ' + result['formula_version']), styles['Normal']))
    def footer(canvas, document):
        canvas.setFont('Helvetica',8); canvas.drawString(35,20, 'L2 One | ' + result['generated_at'][:19] + ' | Página ' + str(document.page))
    SimpleDocTemplate(output, pagesize=landscape(A4), leftMargin=35, rightMargin=35, topMargin=30, bottomMargin=35).build(story,onFirstPage=footer,onLaterPages=footer)
    return output.getvalue()


@router.get('/reports/{format}')
def export(format: Literal['pdf','xlsx'], start: date, end: date, brand: str = '', channel: str = '', region: str = '', authorization: str | None = Header(default=None)):
    user = actor(authorization)
    with services['db']() as con:
        result = report(con, user, start, end, brand, channel, region)
    content = export_pdf(result) if format == 'pdf' else export_xlsx(result)
    media = 'application/pdf' if format == 'pdf' else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    return Response(content, media_type=media, headers={'Content-Disposition': f'attachment; filename="L2_Planejamento_{start}_{end}.{format}"', 'Cache-Control': 'no-store'})

SCHEDULE_SCHEMA = """
CREATE TABLE IF NOT EXISTS l2_commercial_schedules (
 id TEXT PRIMARY KEY, author TEXT NOT NULL, cadence TEXT NOT NULL,
 hour INTEGER NOT NULL CHECK(hour BETWEEN 0 AND 23), enabled BOOLEAN NOT NULL DEFAULT true,
 filters JSONB NOT NULL, last_day DATE, error TEXT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS l2_commercial_report_runs (
 id TEXT PRIMARY KEY, schedule_id TEXT NOT NULL REFERENCES l2_commercial_schedules(id),
 author TEXT NOT NULL, scope_hash TEXT NOT NULL, payload JSONB NOT NULL,
 pdf BYTEA NOT NULL, xlsx BYTEA NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now());
"""
_original_setup = setup

def setup(con):
    _original_setup(con)
    for statement in SCHEDULE_SCHEMA.split(';'):
        if statement.strip():
            con.execute(statement)


class Schedule(Strict):
    cadence: Literal['daily', 'weekly', 'monthly']
    hour: int = Field(default=8, ge=0, le=23)
    brand: str = Field(default='', max_length=120)
    channel: str = Field(default='', max_length=60)
    region: str = Field(default='', max_length=2)


@router.post('/report-schedules')
def schedule(data: Schedule, authorization: str | None = Header(default=None)):
    user = actor(authorization)
    with services['db']() as con:
        con.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('schedule:' + user,))
        if con.execute('SELECT count(*) FROM l2_commercial_schedules WHERE author=%s AND enabled', (user,)).fetchone()[0] >= 5:
            raise HTTPException(409, 'Limite de cinco agendas ativas por usuário')
        key = str(uuid.uuid4())
        filters = data.model_dump(exclude={'cadence', 'hour'})
        con.execute('INSERT INTO l2_commercial_schedules(id,author,cadence,hour,filters) VALUES(%s,%s,%s,%s,%s)', (key, user, data.cadence, data.hour, Jsonb(filters)))
        audit(con, user, 'commercial_schedule', key, 'create')
    return {'id': key}


@router.get('/report-schedules')
def schedules(authorization: str | None = Header(default=None)):
    user = actor(authorization)
    with services['db']() as con:
        rows = con.execute('SELECT id,cadence,hour,enabled,filters,last_day,error FROM l2_commercial_schedules WHERE author=%s ORDER BY created_at DESC', (user,)).fetchall()
    return [dict(zip(('id','cadence','hour','enabled','filters','last_day','error'), row)) for row in rows]


@router.delete('/report-schedules/{identifier}')
def stop_schedule(identifier: str, authorization: str | None = Header(default=None)):
    user = actor(authorization)
    with services['db']() as con:
        con.execute('UPDATE l2_commercial_schedules SET enabled=false WHERE id=%s AND author=%s', (identifier,user))
    return {'disabled': True}


def schedule_window(cadence, now):
    end = now.date()
    if cadence == 'daily':
        return end - timedelta(days=1), end
    if cadence == 'weekly':
        return end - timedelta(days=7), end
    return months_before(end.replace(day=1), 1), end.replace(day=1)


def scope_hash(clients):
    return hashlib.sha256(json.dumps(sorted(x[0] for x in clients)).encode()).hexdigest()


def run_schedules(now=None):
    if not enabled():
        return
    now = now or datetime.now(TZ)
    # Cluster-safe: one scheduler transaction; SKIP LOCKED prevents double runs.
    with services['db']() as con:
        rows = con.execute('SELECT id,author,cadence,hour,filters FROM l2_commercial_schedules WHERE enabled AND (last_day IS NULL OR last_day<%s) FOR UPDATE SKIP LOCKED', (now.date(),)).fetchall()
        for identifier, user, cadence, hour, filters in rows:
            if now.hour < hour or (cadence == 'weekly' and now.weekday() != 5) or (cadence == 'monthly' and now.day != 1):
                continue
            if not services['sectors_for'](user) & {'commercial','management','admin'}:
                con.execute('UPDATE l2_commercial_schedules SET enabled=false,error=%s WHERE id=%s', ('Conta desativada ou sem permissão',identifier)); continue
            try:
                with con.transaction():
                    start,end = schedule_window(cadence,now)
                    r = report(con,user,start,end,**filters)
                    clients,_,_ = selected(con,user,end,**filters)
                    con.execute('INSERT INTO l2_commercial_report_runs(id,schedule_id,author,scope_hash,payload,pdf,xlsx) VALUES(%s,%s,%s,%s,%s,%s,%s)', (str(uuid.uuid4()), identifier,user,scope_hash(clients),Jsonb(json.loads(json.dumps(r,default=str))),export_pdf(r),export_xlsx(r)))
                    con.execute('UPDATE l2_commercial_schedules SET last_day=%s,error=NULL WHERE id=%s', (now.date(),identifier))
                    # Retain at most 90 days of generated files, not source facts.
                    con.execute("DELETE FROM l2_commercial_report_runs WHERE created_at<now()-interval '90 days'")
            except Exception:
                con.execute('UPDATE l2_commercial_schedules SET last_day=%s,error=%s WHERE id=%s', (now.date(),'Falha na geração; consulte os logs técnicos',identifier))
                import logging
                logging.getLogger(__name__).exception('commercial_report_generation_failed schedule=%s',identifier)


async def worker():
    import asyncio
    import logging
    while True:
        try:
            await asyncio.to_thread(run_schedules)
        except Exception:
            logging.getLogger(__name__).exception('commercial_scheduler_failed')
        await asyncio.sleep(60)


@router.get('/report-runs')
def report_runs(authorization: str | None = Header(default=None)):
    user = actor(authorization)
    with services['db']() as con:
        rows = con.execute('SELECT id,created_at,payload->\'period\',payload->\'filters\' FROM l2_commercial_report_runs WHERE author=%s ORDER BY created_at DESC LIMIT 50',(user,)).fetchall()
    return [dict(zip(('id','created_at','period','filters'),r)) for r in rows]


@router.get('/report-runs/{identifier}/{format}')
def scheduled_download(identifier: str, format: Literal['pdf','xlsx'], authorization: str | None = Header(default=None)):
    user = actor(authorization)
    with services['db']() as con:
        r = con.execute('SELECT scope_hash,payload,pdf,xlsx FROM l2_commercial_report_runs WHERE id=%s AND author=%s',(identifier,user)).fetchone()
        if not r:
            raise HTTPException(404,'Relatório não encontrado')
        clients,_,_ = selected(con,user,date.fromisoformat(r[1]['period']['end_exclusive']),**r[1]['filters'])
        if scope_hash(clients) != r[0]:
            raise HTTPException(403,'Carteira alterada; gere um novo relatório para o escopo atual')
    return Response(bytes(r[2] if format=='pdf' else r[3]),media_type='application/pdf' if format=='pdf' else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Cache-Control':'no-store','Content-Disposition':f'attachment; filename="L2_Relatorio_Automatico.{format}"'})

class Allocation(Strict):
    transaction_id: str = Field(min_length=1, max_length=100)
    receivable_id: str = Field(min_length=1, max_length=100)


@router.get('/finance/pending')
def pending_transactions(authorization: str | None = Header(default=None)):
    actor(authorization, 'finance')
    # Bank statement is a financial, account-wide resource; only the finance
    # sector can retrieve it. Customer data still goes through client scoping.
    with services['db']() as con:
        rows=con.execute('SELECT t.id,t.account,t.external_id,t.business_date,t.amount,t.reference FROM l2_commercial_bank_transactions t LEFT JOIN l2_commercial_allocations a ON a.transaction_id=t.id WHERE a.transaction_id IS NULL ORDER BY t.business_date DESC LIMIT 500').fetchall()
    return [dict(zip(('id','account','external_id','business_date','amount','reference'),r)) for r in rows]


@router.post('/finance/allocate')
def allocate(data: Allocation, authorization: str | None = Header(default=None), x_totp: str | None = Header(default=None)):
    user=actor(authorization,'finance');verify_mfa(user,x_totp)
    with services['db']() as con:
        transaction=con.execute('SELECT amount,account FROM l2_commercial_bank_transactions WHERE id=%s FOR UPDATE',(data.transaction_id,)).fetchone()
        title=con.execute('SELECT amount-paid,bank_account,client_id FROM l2_commercial_receivables WHERE id=%s FOR UPDATE',(data.receivable_id,)).fetchone()
        if not transaction or not title:
            raise HTTPException(404,'Lançamento ou título não encontrado')
        client(con,user,title[2])
        old=con.execute('SELECT receivable_id FROM l2_commercial_allocations WHERE transaction_id=%s',(data.transaction_id,)).fetchone()
        if old:
            if old[0]!=data.receivable_id:raise HTTPException(409,'Lançamento já alocado em outro título')
            return {'already_allocated':True}
        if transaction[0]<=0 or transaction[0]>title[0] or transaction[1]!=title[1]:
            raise HTTPException(422,'Crédito deve pertencer à mesma conta e não exceder o saldo do título')
        con.execute('UPDATE l2_commercial_receivables SET paid=paid+%s WHERE id=%s',(transaction[0],data.receivable_id))
        con.execute('INSERT INTO l2_commercial_allocations(transaction_id,receivable_id,amount,author) VALUES(%s,%s,%s,%s)',(data.transaction_id,data.receivable_id,transaction[0],user))
        audit(con,user,'commercial_bank',data.transaction_id,'manual_allocate')
    return {'already_allocated':False}
