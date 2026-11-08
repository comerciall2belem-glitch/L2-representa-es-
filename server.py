"""L2 ONE: secure FastAPI/PostgreSQL application for Render."""
import logging
from pipeline_rules import validate_opportunity
import cpq
import global_search_server
import next_staging_seed
import commercial_planning
from portfolio_transfer_20261020 import transfer as transfer_portfolio_20261020
import drive_backup
import os, json, time, hashlib, secrets, re, base64, gzip, unicodedata, io, zipfile, asyncio
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Header, UploadFile, File, Form, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from decimal import Decimal, InvalidOperation
from datetime import date, datetime
from pydantic import BaseModel, Field
import psycopg
from psycopg.types.json import Jsonb
from client_cleanup import plan as client_cleanup_plan
from daily_report import build_pdf, read_data, TZ
from speedio_integration import lookup_cnpj, SpeedioError
from cnpj_registry import lookup_registry, RegistryError
from senscience_seed import seed_senscience
from davines_seed import seed_davines
from magic_beauty_seed import seed_magic_beauty
from dalla_seed import seed_dalla
from inventory import setup_inventory, snapshot as inventory_snapshot, apply_movement as inventory_apply, InventoryMovement
from order_reconciliation import reconcile_invoice, reconcile_invoice_text, extract_invoice_pdf_text, InvoiceError
from lead_capture import LeadIntake, normalize_intake, ingest_lead, lead_sla
from login_identity import resolve_identity
from catalog_document import build_catalog_pdf
from whatsapp_media import send_media, validate_media, provider_config, MediaError, send_text

from commercial_tables import table_id, price_id, validate_table, seed_bella, validate_bella_order, migrate_bella_policy
from access_policy import effective_sectors, attribute_order, authorize_visit_change, COMMERCIAL_SECTORS
from seller_commission import validate_rate, apply_seller_commission
from financial_visibility import hide_industry_commissions, preserve_industry_commissions

import zara
import webchat
import strategic_crm
import field_assistant
import routine_alerts
import client_bases
import geo_prospecting

BASE = Path(__file__).resolve().parent
USERS = ['Ana Paula', 'Euler', 'Laís', 'Marlene']
SECTORS = {'commercial','clients_edit','routes','office','management','finance','catalog','admin'}
DEFAULT_SECTORS = {
 'Ana Paula':sorted(SECTORS),
 'Euler':['commercial','routes','management','finance','catalog'],
 'Laís':['commercial','clients_edit','office','management','finance','catalog','admin'],
 'Marlene':['commercial','clients_edit','routes','office','management','catalog','admin'],
}
CATALOG_EDITORS = {'Ana Paula', 'Laís', 'Marlene'}
INITIAL_PASSWORD = os.getenv('L2_INITIAL_PASSWORD', '')
PASSWORDS = {u: os.getenv(f'L2_PASSWORD_{i}', '') or INITIAL_PASSWORD for i, u in enumerate(USERS, 1)}
from staging_guard import validate_database_target
DATABASE_URL = os.getenv('DATABASE_URL', '')
validate_database_target(DATABASE_URL)
SESSION_HOURS = int(os.getenv('L2_SESSION_HOURS', '24'))
config_errors = []
if not DATABASE_URL:
    config_errors.append('DATABASE_URL ausente')
for i, user in enumerate(USERS, 1):
    if not PASSWORDS[user]:
        config_errors.append(f'L2_PASSWORD_{i} ausente')
    elif len(PASSWORDS[user]) < 12:
        config_errors.append(f'L2_PASSWORD_{i} deve ter pelo menos 12 caracteres')
if config_errors:
    raise RuntimeError('Configuracao invalida: ' + '; '.join(config_errors))

def db():
    return psycopg.connect(DATABASE_URL)

def next_order_number(con):
    return con.execute('UPDATE order_counter SET value=value+1 WHERE id=1 RETURNING value').fetchone()[0]

def password_hash(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 310_000)
    return salt.hex() + ':' + digest.hex()

def password_ok(password, stored):
    try:
        salt_hex, _ = stored.split(':', 1)
        return secrets.compare_digest(password_hash(password, bytes.fromhex(salt_hex)), stored)
    except (ValueError, TypeError):
        return False


MARCO_ZERO = date(2026, 9, 28)

def operational_date(value):
    try:
        parsed=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return parsed if parsed.date() >= MARCO_ZERO else None
    except (ValueError, TypeError):
        return None

def project_attendance(con,kind,entity_id,payload,created_at=None,updated_at=None):
    client_id=payload.get('clientId')
    if not client_id or not con.execute('SELECT 1 FROM clientes WHERE id=%s',(client_id,)).fetchone(): return
    user=payload.get('user') if kind=='visit' else payload.get('owner')
    if not con.execute('SELECT 1 FROM app_users WHERE username=%s',(user,)).fetchone(): user=None
    contact='visita' if kind=='visit' else {'Ligação':'ligacao','WhatsApp':'whatsapp','E-mail':'email'}.get(payload.get('type'))
    at=operational_date(payload.get('date') if kind=='visit' else payload.get('at'))
    note=(payload.get('notes') or payload.get('result') or '') if kind=='visit' else payload.get('text','')
    con.execute("""INSERT INTO atendimentos(id,cliente_id,usuario_id,tipo_contato,observacao,data_atendimento,created_at,updated_at)
        VALUES(%s,%s,%s,%s,%s,%s,coalesce(%s,now()),coalesce(%s,now()))
        ON CONFLICT(id) DO UPDATE SET cliente_id=excluded.cliente_id,usuario_id=excluded.usuario_id,
          tipo_contato=excluded.tipo_contato,observacao=excluded.observacao,data_atendimento=excluded.data_atendimento,updated_at=now()""",
        (kind+':'+entity_id,client_id,user,contact,note,at,created_at,updated_at))

def project_order(con,entity_id,payload,created_at=None,updated_at=None):
    client_id=payload.get('clientId')
    if not client_id or not con.execute('SELECT 1 FROM clientes WHERE id=%s',(client_id,)).fetchone(): return
    try:
        value=Decimal(str(payload.get('amount'))) if payload.get('amount') not in (None,'') else None
        if value is not None and (not value.is_finite() or value<0): value=None
    except (ValueError,TypeError,InvalidOperation): value=None
    con.execute("""INSERT INTO faturamento_pedidos(id,cliente_id,pedido_erp_id,valor_total,data_pedido,status_pedido,created_at,updated_at)
        VALUES(%s,%s,%s,%s,%s,%s,coalesce(%s,now()),coalesce(%s,now()))
        ON CONFLICT(id) DO UPDATE SET cliente_id=excluded.cliente_id,pedido_erp_id=excluded.pedido_erp_id,
          valor_total=excluded.valor_total,data_pedido=excluded.data_pedido,status_pedido=excluded.status_pedido,updated_at=now()""",
        (entity_id,client_id,str(payload.get('orderNumber') or '') or None,value,operational_date(payload.get('date')),str(payload.get('status') or 'Sem status'),created_at,updated_at))

def initialize():
    with db() as con:
        expected_database=os.getenv('L2_EXPECTED_DATABASE','').strip()
        if expected_database and con.execute('SELECT current_database()').fetchone()[0]!=expected_database:
            raise RuntimeError('Banco conectado diverge do ambiente esperado; inicialização recusada')
        zara.setup(con)
        drive_backup.setup(con)
        webchat.setup(con)
        con.execute('CREATE TABLE IF NOT EXISTS entities (kind TEXT NOT NULL, id TEXT NOT NULL, payload JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(kind,id))')
        if os.getenv('L2_EXPECTED_DATABASE') == 'l2_one_next_staging_db':
            global_search_server.create_indexes(con)
        con.execute('ALTER TABLE entities ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ')
        con.execute('ALTER TABLE entities ALTER COLUMN created_at SET DEFAULT now()')
        con.execute('CREATE TABLE IF NOT EXISTS commercial_drafts (username TEXT NOT NULL, draft_key TEXT NOT NULL, payload JSONB, client_timestamp BIGINT NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(username,draft_key))')
        con.execute('CREATE TABLE IF NOT EXISTS cnpj_registry_cache (cnpj TEXT PRIMARY KEY, payload JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now())')
        con.execute('CREATE TABLE IF NOT EXISTS applied_changes (change_id TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())')
        con.execute('CREATE TABLE IF NOT EXISTS audit_log (id BIGSERIAL PRIMARY KEY, username TEXT NOT NULL, kind TEXT NOT NULL, entity_id TEXT NOT NULL, action TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())')
        strategic_crm.setup(con)
        if commercial_planning.enabled(): commercial_planning.setup(con)
        field_assistant.setup(con)
        seed_bella(con, BASE)
        migrate_bella_policy(con)
        seed_senscience(con, BASE)
        seed_davines(con, BASE)
        seed_magic_beauty(con, BASE)
        # Cadastro inicial idempotente; preserva alterações feitas pela equipe no sistema.
        bth = {'id':'Brotherhood BTH','name':'Brotherhood BTH','active':True,
               'contact':'','notes':'Tabela BTH Varejo - Brotherhood 2026. Mesmos preços para PA e AP.',
               'commercialPolicy':[
                   {'minimum':'2000.00','paymentTerms':'30/45/60','freight':'CIF'},
                   {'minimum':'3000.00','paymentTerms':'30/60/90','freight':'CIF'}]}
        con.execute("INSERT INTO entities(kind,id,payload) VALUES('industry',%s,%s) ON CONFLICT(kind,id) DO NOTHING",
                    (bth['id'], Jsonb(bth)))
        bth_catalog=json.loads((BASE/'bth_catalog_2026.json').read_text(encoding='utf-8'))
        if len(bth_catalog)!=53 or len({row['sku'] for row in bth_catalog})!=53:
            raise RuntimeError('Catálogo BTH incompleto ou com SKU duplicado')
        for uf in ('PA','AP'):
            table={'id':f"{bth['name']}|{uf}",'brand':bth['name'],'state':uf,
                   'title':f'Tabela BTH Varejo 2026 - {uf}','active':True}
            con.execute("INSERT INTO entities(kind,id,payload) VALUES('price_table',%s,%s) ON CONFLICT(kind,id) DO NOTHING",
                        (table['id'],Jsonb(table)))
            for row in bth_catalog:
                payload={'brand':bth['name'],'state':uf,**row}
                price_id=f"{bth['name']}|{uf}|{row['sku']}"
                con.execute("INSERT INTO entities(kind,id,payload) VALUES('price',%s,%s) ON CONFLICT(kind,id) DO NOTHING",
                            (price_id,Jsonb(payload)))
        con.execute('CREATE TABLE IF NOT EXISTS archived_entities (kind TEXT NOT NULL, id TEXT NOT NULL, payload JSONB NOT NULL, reason TEXT NOT NULL, archived_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(kind,id))')
        seed_dalla(con, BASE)
        setup_inventory(con)
        con.execute('CREATE TABLE IF NOT EXISTS order_counter (id SMALLINT PRIMARY KEY CHECK(id=1), value BIGINT NOT NULL CHECK(value>=0))')
        con.execute('INSERT INTO order_counter(id,value) VALUES(1,0) ON CONFLICT(id) DO NOTHING')
        existing_max = con.execute("SELECT COALESCE(MAX((payload->>'orderNumber')::bigint),0) FROM (SELECT payload FROM entities WHERE kind='order' UNION ALL SELECT payload FROM archived_entities WHERE kind='order') AS orders WHERE payload->>'orderNumber' ~ '^[0-9]+$'").fetchone()[0]
        con.execute('UPDATE order_counter SET value=GREATEST(value,%s) WHERE id=1',(existing_max,))
        legacy_orders = con.execute("SELECT source,id FROM (SELECT 'active' AS source,id,payload FROM entities WHERE kind='order' AND payload->>'orderNumber' IS NULL UNION ALL SELECT 'archived' AS source,id,payload FROM archived_entities WHERE kind='order' AND payload->>'orderNumber' IS NULL) AS orders ORDER BY payload->>'date',id").fetchall()
        for source, order_id in legacy_orders:
            number=next_order_number(con)
            table='entities' if source=='active' else 'archived_entities'
            con.execute(f"UPDATE {table} SET payload=jsonb_set(payload,'{{orderNumber}}',to_jsonb(%s::bigint)) WHERE kind='order' AND id=%s",(number,order_id))
        con.execute('CREATE TABLE IF NOT EXISTS order_attachments (id TEXT PRIMARY KEY, order_id TEXT NOT NULL, filename TEXT NOT NULL, content_type TEXT NOT NULL, content BYTEA NOT NULL, uploaded_by TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())')
        con.execute('''CREATE TABLE IF NOT EXISTS order_invoices (
            id TEXT PRIMARY KEY, order_id TEXT NOT NULL, brand TEXT NOT NULL,
            invoice_number TEXT NOT NULL, filename TEXT NOT NULL, content BYTEA NOT NULL,
            reconciliation JSONB NOT NULL, uploaded_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(order_id,brand,invoice_number))''')
        con.execute('CREATE TABLE IF NOT EXISTS visit_photos (id TEXT PRIMARY KEY, visit_id TEXT NOT NULL, filename TEXT NOT NULL, content_type TEXT NOT NULL, content BYTEA NOT NULL, uploaded_by TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())')
        con.execute('CREATE INDEX IF NOT EXISTS idx_visit_photos_visit ON visit_photos(visit_id)')
        con.execute('CREATE INDEX IF NOT EXISTS idx_order_attachments_order ON order_attachments(order_id)')
        con.execute('CREATE TABLE IF NOT EXISTS app_users (username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, active BOOLEAN NOT NULL DEFAULT true, updated_at TIMESTAMPTZ NOT NULL DEFAULT now())')
        con.execute('ALTER TABLE app_users ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT false')
        con.execute('ALTER TABLE app_users ADD COLUMN IF NOT EXISTS sectors JSONB')
        con.execute('ALTER TABLE app_users ADD COLUMN IF NOT EXISTS role TEXT')
        con.execute('ALTER TABLE app_users ADD COLUMN IF NOT EXISTS department TEXT')
        con.execute('''CREATE TABLE IF NOT EXISTS seller_profiles (
            username TEXT PRIMARY KEY REFERENCES app_users(username) ON DELETE RESTRICT,
            full_name TEXT NOT NULL, document TEXT NOT NULL, email TEXT NOT NULL,
            phone TEXT NOT NULL, bank TEXT NOT NULL, account_type TEXT NOT NULL,
            branch TEXT NOT NULL, account_number TEXT NOT NULL, pix_key TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now())''')
        con.execute('ALTER TABLE app_users ADD COLUMN IF NOT EXISTS seller_commission_rate NUMERIC(5,2) CHECK (seller_commission_rate BETWEEN 0 AND 100)')
        con.execute("UPDATE app_users SET department=CASE username WHEN 'Ana Paula' THEN 'Direção comercial' WHEN 'Euler' THEN 'Comercial' WHEN 'Laís' THEN 'Suporte administrativo' WHEN 'Marlene' THEN 'Operações administrativas' ELSE coalesce(role,'Equipe') END WHERE department IS NULL")
        con.execute('CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, username TEXT NOT NULL REFERENCES app_users(username), expires_at TIMESTAMPTZ NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())')
        con.execute('CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at)')
        con.execute('''CREATE TABLE IF NOT EXISTS capture_accounts (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, tax_id TEXT UNIQUE, state TEXT, city TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now())''')
        con.execute('''CREATE TABLE IF NOT EXISTS capture_contacts (
            id TEXT PRIMARY KEY, account_id TEXT REFERENCES capture_accounts(id), name TEXT NOT NULL,
            email_norm TEXT, phone_norm TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now())''')
        con.execute('''CREATE TABLE IF NOT EXISTS capture_leads (
            id TEXT PRIMARY KEY, account_id TEXT REFERENCES capture_accounts(id),
            contact_id TEXT NOT NULL REFERENCES capture_contacts(id), owner TEXT REFERENCES app_users(username),
            status TEXT NOT NULL, origin TEXT NOT NULL, campaign TEXT, landing_page TEXT,
            utm_source TEXT, utm_medium TEXT, utm_campaign TEXT, utm_content TEXT, utm_term TEXT,
            custom_fields JSONB NOT NULL DEFAULT '{}'::jsonb, email_norm TEXT, phone_norm TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            assigned_at TIMESTAMPTZ, first_response_at TIMESTAMPTZ)''')
        con.execute('CREATE INDEX IF NOT EXISTS idx_capture_leads_email ON capture_leads(email_norm) WHERE email_norm IS NOT NULL')
        con.execute('CREATE INDEX IF NOT EXISTS idx_capture_leads_phone ON capture_leads(phone_norm) WHERE phone_norm IS NOT NULL')
        con.execute('CREATE INDEX IF NOT EXISTS idx_capture_leads_owner ON capture_leads(owner,created_at DESC)')
        con.execute('''CREATE TABLE IF NOT EXISTS capture_lead_events (
            id BIGSERIAL PRIMARY KEY, lead_id TEXT NOT NULL REFERENCES capture_leads(id),
            event_type TEXT NOT NULL, actor TEXT, details JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now())''')
        con.execute('CREATE INDEX IF NOT EXISTS idx_capture_events_lead ON capture_lead_events(lead_id,created_at)')
        con.execute('''CREATE TABLE IF NOT EXISTS capture_routing_state (
            id SMALLINT PRIMARY KEY CHECK(id=1), last_username TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now())''')
        con.execute('INSERT INTO capture_routing_state(id) VALUES(1) ON CONFLICT(id) DO NOTHING')
        con.execute("""CREATE TABLE IF NOT EXISTS client_seller_authorizations (
            client_id TEXT NOT NULL, seller_username TEXT NOT NULL REFERENCES app_users(username),
            approved_by TEXT NOT NULL REFERENCES app_users(username), active BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY(client_id,seller_username))""")
        con.execute('CREATE INDEX IF NOT EXISTS idx_client_seller_active ON client_seller_authorizations(seller_username,client_id) WHERE active')
        for user, password in PASSWORDS.items():
            exists = con.execute('SELECT 1 FROM app_users WHERE username=%s',(user,)).fetchone()
            if not exists:
                con.execute('INSERT INTO app_users(username,password_hash,must_change_password,sectors) VALUES(%s,%s,true,%s)',(user,password_hash(password),Jsonb(DEFAULT_SECTORS[user])))
            else:
                con.execute('UPDATE app_users SET sectors=%s WHERE username=%s AND sectors IS NULL',(Jsonb(DEFAULT_SECTORS[user]),user))
            con.execute('UPDATE app_users SET role=%s WHERE username=%s AND role IS NULL',('Administradora' if user=='Ana Paula' else 'Vendedor' if user=='Euler' else 'Administrativo',user))
        # Marlene: carteira, campo, operações, escritório e gestão; financeiro permanece separado.
        con.execute('UPDATE app_users SET sectors=%s,updated_at=now() WHERE username=%s AND sectors IS DISTINCT FROM %s',
                    (Jsonb(DEFAULT_SECTORS['Marlene']),'Marlene',Jsonb(DEFAULT_SECTORS['Marlene'])))
        # Política de acesso para contas existentes, inclusive sessões antigas.
        for name, role, sectors in con.execute('SELECT username,role,sectors FROM app_users').fetchall():
            updated_role = 'Administrativo' if name == 'Laís' else role
            allowed = sorted(effective_sectors(name, updated_role, set(sectors or []) | ({'admin'} if name=='Laís' else set())))
            if updated_role != role or set(allowed) != set(sectors or []):
                con.execute('UPDATE app_users SET role=%s,sectors=%s,updated_at=now() WHERE username=%s', (updated_role, Jsonb(allowed), name))
                con.execute('DELETE FROM sessions WHERE username=%s', (name,))
                con.execute("INSERT INTO audit_log(username,kind,entity_id,action) VALUES('Ana Paula','app_user',%s,'commercial_access_policy')", (name,))
        # Importação inicial opcional por segredo do Render. O valor é gzip+base64,
        # nunca fica no repositório, e só é aplicado enquanto a carteira estiver vazia.
        initial_clients = os.getenv('L2_INITIAL_CLIENTS_B64', '')
        has_clients = con.execute("SELECT 1 FROM entities WHERE kind='client' LIMIT 1").fetchone()
        if initial_clients and not has_clients:
            try:
                decoded = gzip.decompress(base64.b64decode(initial_clients)).decode('utf-8')
                clients = json.loads(decoded)
                if not isinstance(clients, list) or len(clients) != 432:
                    raise ValueError('A carga inicial deve conter 432 clientes.')
                seen = set()
                for client in clients:
                    entity_id = client.get('id') if isinstance(client, dict) else None
                    if not isinstance(entity_id, str) or not entity_id or entity_id in seen:
                        raise ValueError('ID de cliente inválido ou duplicado.')
                    if not isinstance(client.get('name'), str) or not client['name'].strip():
                        raise ValueError('Nome de cliente obrigatório.')
                    seen.add(entity_id)
                    payload = dict(client)
                    payload['updatedBy'] = 'importacao-inicial'
                    con.execute('INSERT INTO entities(kind,id,payload) VALUES(%s,%s,%s)', ('client', entity_id, Jsonb(payload)))
            except Exception as exc:
                raise RuntimeError('Falha ao importar a carteira inicial protegida.') from exc
        initial_prices = os.getenv('L2_INITIAL_PRICES_B64', '')
        has_prices = con.execute("SELECT 1 FROM entities WHERE kind='price' LIMIT 1").fetchone()
        if initial_prices and not has_prices:
            try:
                prices = json.loads(gzip.decompress(base64.b64decode(initial_prices)).decode('utf-8'))
                if not isinstance(prices, list) or len(prices) != 3934:
                    raise ValueError('Carga de preços deve conter 3934 registros.')
                seen = set()
                for row in prices:
                    if not isinstance(row, dict):
                        raise ValueError('Produto inválido.')
                    brand, sku = str(row.get('brand') or '').strip(), str(row.get('sku') or '').strip()
                    state = str(row.get('state') or '').strip().upper()
                    amount = Decimal(str(row.get('price') or '0'))
                    if not brand or not sku or state not in ('PA', 'AP') or not amount.is_finite() or amount <= 0:
                        raise ValueError('Marca, SKU, UF ou preço inválido.')
                    key = f'{brand}|{state}|{sku}'
                    if key in seen:
                        raise ValueError('Preço duplicado na carga.')
                    seen.add(key)
                    payload = {'brand':brand, 'sku':sku, 'state':state,
                               'price':str(amount), 'description':str(row.get('description') or '')}
                    con.execute("INSERT INTO entities(kind,id,payload) VALUES('price',%s,%s)", (key, Jsonb(payload)))
            except Exception as exc:
                raise RuntimeError('Falha ao importar preços iniciais protegidos.') from exc
        # Catálogos legados recebem indústria e tabela PA/AP sem alterar preços.
        for (brand,) in con.execute("SELECT DISTINCT payload->>'brand' FROM entities WHERE kind='price' AND payload->>'brand' IS NOT NULL"):
            con.execute("INSERT INTO entities(kind,id,payload) VALUES('industry',%s,%s) ON CONFLICT(kind,id) DO NOTHING",(brand,Jsonb({'id':brand,'name':brand,'active':True})))
        for brand,state in con.execute("SELECT DISTINCT payload->>'brand',payload->>'state' FROM entities WHERE kind='price' AND payload->>'state' IN ('PA','AP') AND nullif(payload->>'tableId','') IS NULL"):
            key=f'{brand}|{state}'
            con.execute("INSERT INTO entities(kind,id,payload) VALUES('price_table',%s,%s) ON CONFLICT(kind,id) DO NOTHING",(key,Jsonb({'id':key,'brand':brand,'state':state,'title':f'{brand} {state}','active':True})))
        office_seed = os.getenv('L2_OFFICE_SEED_B64', '')
        office_marker = 'office-management-seed-v1'
        already_imported = con.execute('SELECT 1 FROM applied_changes WHERE change_id=%s', (office_marker,)).fetchone()
        if office_seed and not already_imported:
            try:
                office = json.loads(gzip.decompress(base64.b64decode(office_seed)).decode('utf-8'))
                sections = {
                    'processes': ('office_process', 'ID'),
                    'budget': ('office_budget', None),
                    'rituals': ('office_ritual', 'Momento'),
                    'roles': ('office_role', 'Pessoa'),
                    'actions': ('office_action', 'ID'),
                    'commercial': ('office_commercial', 'ID'),
                    'administrative': ('office_administrative', 'ID'),
                    'finance': ('office_finance', 'ID'),
                    'monthlyClose': ('office_monthly_close', 'Mês'),
                }
                for section, (kind, id_field) in sections.items():
                    rows = office.get(section, [])
                    if not isinstance(rows, list):
                        raise ValueError(f'Seção inválida: {section}')
                    for index, row in enumerate(rows):
                        if not isinstance(row, dict):
                            raise ValueError(f'Linha inválida: {section}')
                        row_id = str(row.get(id_field, '') if id_field else '').strip()
                        if not row_id:
                            row_id = f'{section}-{index + 1}'
                        payload = dict(row)
                        payload['id'] = row_id
                        payload['source'] = 'L2 — Gestão Integrada do Escritório'
                        con.execute('INSERT INTO entities(kind,id,payload) VALUES(%s,%s,%s) ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload,updated_at=now()', (kind, row_id, Jsonb(payload)))
                con.execute('INSERT INTO applied_changes(change_id) VALUES(%s)', (office_marker,))
            except Exception as exc:
                raise RuntimeError('Falha ao importar a gestão integrada do escritório.') from exc

        # Relational projection of the existing sync entities; IDs remain stable across exports.
        con.execute("""CREATE TABLE IF NOT EXISTS clientes (
            id TEXT PRIMARY KEY, razao_social VARCHAR(180) NOT NULL,
            nome_fantasia VARCHAR(180), documento VARCHAR(20) UNIQUE,
            curva_abc CHAR(1) CHECK(curva_abc IN ('A','B','C')),
            status VARCHAR(12) NOT NULL DEFAULT 'ativo' CHECK(status IN ('ativo','inativo','em_risco')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
        con.execute("""CREATE TABLE IF NOT EXISTS atendimentos (
            id TEXT PRIMARY KEY, cliente_id TEXT NOT NULL REFERENCES clientes(id) ON DELETE RESTRICT,
            usuario_id TEXT REFERENCES app_users(username) ON DELETE SET NULL,
            tipo_contato VARCHAR(12) CHECK(tipo_contato IN ('ligacao','whatsapp','visita','email')),
…36004 tokens truncated…df',headers={'Cache-Control':'no-store','Content-Disposition':'attachment; filename="L2_ONE_relatorio_diario.pdf"'})

@app.get('/health')
def health():
    with db() as con:
        users = con.execute('SELECT count(*) FROM app_users WHERE active').fetchone()[0]
        counts = {kind: con.execute('SELECT count(*) FROM entities WHERE kind=%s',(kind,)).fetchone()[0]
                  for kind in ('client','visit','order','task','route','goal','price','office_process','office_budget','office_ritual','office_role')}
    return {'status':'ok', 'users':users, **counts}

@app.post('/api/self-test')
def self_test(authorization: str | None = Header(default=None)):
    user = auth(authorization)
    if user != 'Ana Paula':
        raise HTTPException(403, 'Autoteste restrito à administradora')
    sample = {
        'visit': {'id':'self-test-visit','clientId':'self-test','date':'2026-01-01','notes':'teste transacional'},
        'task': {'id':'self-test-task','title':'teste transacional','date':'2026-01-01','done':False},
        'route': {'id':'self-test-route','date':'2026-01-01','clientIds':[]},
        'goal': {'id':'self-test-goal','month':'2026-01','amount':1.0},
    }
    checks = {}
    with db() as con:
        for kind, payload in sample.items():
            con.execute('INSERT INTO entities(kind,id,payload) VALUES(%s,%s,%s) ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload,updated_at=now()', (kind,payload['id'],Jsonb(payload)))
            checks[kind] = bool(con.execute('SELECT 1 FROM entities WHERE kind=%s AND id=%s',(kind,payload['id'])).fetchone())
        con.rollback()
    return {'status':'ok' if all(checks.values()) else 'failed', 'transactionRolledBack':True, 'checks':checks}

@app.middleware('http')
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'camera=(self), microphone=(), geolocation=(self)'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; connect-src 'self' https:; img-src 'self' data: blob: https://tile.openstreetmap.org; media-src 'self' blob:; frame-ancestors 'none'"
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    return response

@app.get('/')
@app.get('/index.html')
def home(): return FileResponse(BASE/'index.html',headers={'Cache-Control':'no-store'})

@app.get('/atualizar')
def refresh_workspace():
    # A distinct navigation bypasses cached home documents without clearing user data.
    content = (BASE/'index.html').read_text()
    version = '20261013-logo-storage'
    content = re.sub(r'(<script src="[^"?]+)(?:\?[^" ]*)?("[^>]*>)', lambda m: m[1]+'?v='+version+m[2], content)
    content = re.sub(r'(<link[^>]+href="modern_workspace\.css)(?:\?[^" ]*)?("[^>]*>)', lambda m: m[1]+'?v='+version+m[2], content)
    return Response(content=content, media_type='text/html', headers={'Cache-Control':'no-store'})

@app.get('/zara.html')
def zara_page():
    return FileResponse(BASE/'zara.html', headers={'Cache-Control':'no-store'})

@app.get('/{filename}')
def asset(filename: str):
    if filename not in ('commercial_planning.js','commercial_planning.css','cpq.js','bi_dashboard.js','bi_dashboard.css','invoice_pdf.js','inventory.js','industry_workspace.js','jspdf.umd.min.js','central_operations.js','modern_workspace.js','modern_workspace.css','strategy_ui.css','leaflet.js','leaflet.css','strategic_crm.js','app.js','live_records.js','crm_operations.js','goals_dashboard.js', 'commercial_drafts.js','workspace.js','finance360.js','cash.js','personal-finance.js','sw.js','manifest.json','logo-l2.jpeg','logo-l2-light.jpg','logo-l2-dark.jpg','logo-data.js','icon-192.png','icon-512.png','apple-touch-icon.png'):
        raise HTTPException(404)
    if filename in ('logo-l2-light.jpg','logo-l2-dark.jpg'):
        source={'logo-l2-light.jpg':'logo-light.jpg.b64','logo-l2-dark.jpg':'logo-dark.jpg.b64'}[filename]
        return Response(content=base64.b64decode((BASE/source).read_text()), media_type='image/jpeg', headers={'Cache-Control':'no-store'})
    if filename in ('icon-192.png','icon-512.png','apple-touch-icon.png'):
        return Response(content=base64.b64decode((BASE/(filename+'.b64')).read_text()), media_type='image/png', headers={'Cache-Control':'public, max-age=86400'})
    return FileResponse(BASE/filename,headers={'Cache-Control':'no-store'})

from personal_finance import initialize_personal, install_personal
install_personal(app, db, auth)


@app.get('/api/commercial/lead-conversion')
def commercial_lead_conversion(month: str, authorization: str | None = Header(default=None)):
    from lead_conversion import conversion_metrics
    import re
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',month):raise HTTPException(422,'Mês inválido')
    user=auth(authorization)
    require_sector(user,'commercial','office')
    with db() as con:
        all_users=not is_seller(con,user)
        leads=[r[0] for r in con.execute("SELECT payload FROM entities WHERE kind='lead' AND (%s OR payload->>'owner'=%s)",(all_users,user))]
        rows=con.execute("SELECT l.id,a.tax_id,l.owner,l.created_at,l.first_response_at,l.status,l.custom_fields FROM capture_leads l LEFT JOIN capture_accounts a ON a.id=l.account_id WHERE (%s OR l.owner=%s)",(all_users,user)).fetchall()
        leads += [{'id':r[0],'taxId':r[1],'owner':r[2],'createdAt':r[3].isoformat(),'firstResponseAt':r[4].isoformat() if r[4] else None,'status':r[5],'clientId':(r[6] or {}).get('clientId')} for r in rows]
        return conversion_metrics(leads,scoped_rows(con,'client',user),scoped_rows(con,'order',user),month)

from financial_inputs import FinancialInput

@app.get('/api/finance/inputs')
def financial_inputs_list(authorization: str | None = Header(default=None)):
    user=auth(authorization)
    require_sector(user,'finance')
    with db() as con:
        return [r[0] for r in con.execute("SELECT payload FROM entities WHERE kind='financial_input' ORDER BY updated_at,id")]

@app.put('/api/finance/inputs')
def financial_inputs_save(data: FinancialInput, authorization: str | None = Header(default=None)):
    user=auth(authorization)
    require_sector(user,'finance')
    payload=data.model_dump(mode='json')
    payload['updatedBy']=user
    payload['updatedAt']=datetime.now(TZ).isoformat()
    with db() as con:
        con.execute("INSERT INTO entities(kind,id,payload) VALUES ('financial_input',%s,%s) ON CONFLICT(kind,id) DO UPDATE SET payload=EXCLUDED.payload,updated_at=now()",(data.id,Jsonb(payload)))
    return payload

@app.get('/api/finance/analysis')
def financial_analysis_read(month: str, authorization: str | None = Header(default=None)):
    from financial_analysis import analyze_finance
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',month):raise HTTPException(422,'Mês inválido')
    user=auth(authorization)
    require_sector(user,'finance')
    with db() as con:
        inputs=[r[0] for r in con.execute("SELECT payload FROM entities WHERE kind='financial_input'")]
        return analyze_finance(inputs,scoped_rows(con,'order',user),scoped_rows(con,'office_finance',user),scoped_rows(con,'commission_rate',user),month,scoped_rows(con,'goal',user))


def correct_order_18_date():
    # This data correction belongs exclusively to the authorized homologation service.
    if os.getenv('RENDER_SERVICE_ID') != 'srv-daqk8ifavr4c738m78f0':
        return
    order_id = '858c6713-4712-4873-b716-8f5e76425468'
    with db() as con:
        row = con.execute("SELECT payload FROM entities WHERE kind='order' AND id=%s FOR UPDATE", (order_id,)).fetchone()
        seed_text = os.getenv('L2_HOMOLOGATION_ORDER18_SEED', '')
        if not row and seed_text and os.getenv('L2_ENABLE_HOMOLOGATION_SEED') == '1':
            # Carga privada da operação para esta instância; não vai para Git.
            seed = json.loads(seed_text)
            order, customer = seed['order'], seed['client']
            if order.get('id') != order_id or str(order.get('orderNumber')) != '18' or customer.get('id') != order.get('clientId'):
                raise RuntimeError('Carga de homologação do pedido 18 incompatível')
            con.execute("INSERT INTO entities(kind,id,payload) VALUES('client',%s,%s) ON CONFLICT DO NOTHING", (customer['id'], Jsonb(customer)))
            con.execute("""INSERT INTO clientes(id,razao_social,nome_fantasia,documento,curva_abc)
                VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""", (customer['id'], customer['name'], customer.get('tradeName'), customer.get('taxId'), customer.get('abc') if customer.get('abc') in ('A','B','C') else None))
            con.execute("INSERT INTO entities(kind,id,payload) VALUES('order',%s,%s) ON CONFLICT DO NOTHING", (order_id, Jsonb(order)))
            number = con.execute("SELECT coalesce(max((payload->>'orderNumber')::integer),0) FROM entities WHERE kind='order' AND payload->>'orderNumber' ~ '^[0-9]+$'").fetchone()[0]
            con.execute('UPDATE order_counter SET value=greatest(value,%s) WHERE id=1', (number,))
            row = con.execute("SELECT payload FROM entities WHERE kind='order' AND id=%s FOR UPDATE", (order_id,)).fetchone()
        if not row:
            logging.getLogger('uvicorn.error').warning('order18_migration status=absent')
            return
        payload = dict(row[0])
        if payload.get('date') != '2026-09-29':
            payload['dateCorrection'] = {'original': payload.get('date'), 'corrected':'2026-09-29', 'reason':'Correção expressamente autorizada pela administração'}
            payload['date'] = '2026-09-29'
            con.execute("UPDATE entities SET payload=%s,updated_at=now() WHERE kind='order' AND id=%s", (Jsonb(payload), order_id))
            con.execute("INSERT INTO audit_log(username,kind,entity_id,action) VALUES('Ana Paula','order',%s,'date_correction_2026_09_29')", (order_id,))
        project_order(con, order_id, payload)
        verified = con.execute("SELECT payload->>'date' FROM entities WHERE kind='order' AND id=%s", (order_id,)).fetchone()
        if not verified or verified[0] != '2026-09-29':
            raise RuntimeError('Falha na conferência interna da migração do pedido 18')
    logging.getLogger('uvicorn.error').info('order18_migration status=verified date=2026-09-29')


@app.post('/api/catalog/import-pdf/preview')
async def preview_price_pdf(file: UploadFile = File(...), authorization: str | None = Header(default=None)):
    user = auth(authorization)
    if user not in CATALOG_EDITORS or 'catalog' not in sectors_for(user):
        raise HTTPException(403, 'Importação de preços restrita aos editores do catálogo')
    content = await file.read(10 * 1024 * 1024 + 1)
    from pdf_price_import import extract_price_candidates
    try:
        return await asyncio.to_thread(extract_price_candidates, content)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(400, 'Não foi possível extrair esta tabela. Confira o PDF ou use CSV.') from exc


@app.get('/api/commercial/activity')
def commercial_activity(authorization: str | None = Header(default=None)):
    user = auth(authorization)
    require_sector(user, 'commercial', 'office')
    monitoring = user in ('Ana Paula', 'Marlene')
    with db() as con:
        def visible(kind):
            if monitoring:
                return [{**row[1], 'id': row[0]} for row in con.execute('SELECT id,payload FROM entities WHERE kind=%s ORDER BY updated_at,id', (kind,))]
            return [{**x, 'id': x.get('id') or (price_id(x) if kind == 'price' else '')} for x in scoped_rows(con, kind, user)]
        clients = {x['id']: x for x in visible('client')}
        permissions = sectors_for(user)
        kinds = ['client', 'visit', 'interaction', 'task', 'opportunity', 'order', 'route', 'fulfillment']
        if 'office' in permissions: kinds += ['office_action', 'office_commercial', 'office_administrative']
        if 'catalog' in permissions: kinds += ['price_table', 'price', 'industry']
        allowed = set()
        for kind in kinds:
            for x in visible(kind):
                if kind.startswith('office_') and 'finance' not in permissions and str(x.get('Área', '')).casefold() == 'financeiro': continue
                if kind in ('client', 'price_table', 'price', 'industry', 'office_action', 'office_commercial', 'office_administrative') or x.get('clientId') in clients or (kind == 'task' and not x.get('clientId') and (monitoring or not is_seller(con, user) or x.get('user') == user)):
                    if x.get('id'): allowed.add((kind, x['id']))
        event_kinds = kinds + ['whatsapp_media', 'whatsapp_document']
        events, audited = [], set()
        query = "SELECT a.username,a.kind,a.entity_id,a.action,a.created_at,e.payload FROM audit_log a JOIN entities e ON e.kind=CASE WHEN a.kind IN ('whatsapp_media','whatsapp_document') THEN 'client' ELSE a.kind END AND e.id=a.entity_id WHERE a.kind=ANY(%s) ORDER BY a.id DESC LIMIT 2000"
        for actor, kind, entity_id, action, at, payload in con.execute(query, (event_kinds,)):
            access_kind = 'client' if kind in ('whatsapp_media', 'whatsapp_document') else kind
            if (access_kind, entity_id) not in allowed: continue
            audited.add((access_kind, entity_id))
            cid = entity_id if access_kind == 'client' else payload.get('clientId')
            events.append({'entityId': entity_id, 'user': actor, 'kind': kind, 'action': action, 'at': at.isoformat(), 'clientName': clients.get(cid, {}).get('name', '') or str(payload.get('Demanda ou problema') or payload.get('Demanda') or payload.get('title') or payload.get('brand') or '')})
        # Imported and older records may predate the audit feed. Show their actual
        # persisted update time, explicitly identified as existing records.
        for kind, entity_id, payload, at in con.execute('SELECT kind,id,payload,updated_at FROM entities WHERE kind=ANY(%s) ORDER BY updated_at DESC LIMIT 2000', (kinds,)):
            if (kind, entity_id) not in allowed or (kind, entity_id) in audited: continue
            cid = entity_id if kind == 'client' else payload.get('clientId')
            events.append({'entityId': entity_id, 'user': payload.get('updatedBy') or payload.get('enteredBy') or payload.get('user') or 'Autoria não informada', 'kind': kind, 'action': 'existing', 'at': at.isoformat(), 'clientName': clients.get(cid, {}).get('name', '') or str(payload.get('Demanda ou problema') or payload.get('Demanda') or payload.get('title') or payload.get('brand') or '')})
    return sorted(events, key=lambda x: x['at'], reverse=True)[:100]


class CommercialDraft(BaseModel):
    key: str
    payload: dict | None = None
    updatedAt: int

@app.get('/api/commercial/drafts')
def commercial_drafts_list(authorization: str | None = Header(default=None)):
    user=auth(authorization)
    require_sector(user, 'commercial', 'office')
    with db() as con:
        return [{'key':r[0],'payload':r[1],'updatedAt':r[2]} for r in con.execute('SELECT draft_key,payload,client_timestamp FROM commercial_drafts WHERE username=%s ORDER BY updated_at DESC LIMIT 100',(user,))]

@app.put('/api/commercial/drafts')
def commercial_drafts_save(data: CommercialDraft, authorization: str | None = Header(default=None)):
    user=auth(authorization)
    require_sector(user, 'commercial', 'office')
    if not re.fullmatch(r'(order|visit|interaction):[A-Za-z0-9_.-]{1,128}',data.key) or not 0<data.updatedAt<=int(datetime.now(TZ).timestamp()*1000)+300000:
        raise HTTPException(422,'Identificação ou data do rascunho inválida')
    if len(json.dumps(data.payload,ensure_ascii=False).encode())>100000:
        raise HTTPException(413,'Rascunho acima de 100 KB')
    with db() as con:
        if data.payload:
            fields=data.payload.get('fields',{})
            if not isinstance(fields,dict) or any(re.search(r'token|password|secret|senha',k,re.I) for k in fields):
                raise HTTPException(422,'Campos não permitidos no rascunho')
            cid=fields.get('clientId') or (data.payload.get('order') or {}).get('clientId')
            if cid:check_client_scope(con,user,cid)
        con.execute("INSERT INTO commercial_drafts(username,draft_key,payload,client_timestamp) VALUES(%s,%s,%s,%s) ON CONFLICT(username,draft_key) DO UPDATE SET payload=excluded.payload,client_timestamp=excluded.client_timestamp,updated_at=now() WHERE commercial_drafts.client_timestamp<excluded.client_timestamp",(user,data.key,Jsonb(data.payload),data.updatedAt))
    return {'saved':True}


class WhatsAppText(BaseModel):
    clientId: str
    message: str

@app.post('/api/whatsapp/text')
async def send_whatsapp_text(data: WhatsAppText, authorization: str | None = Header(default=None)):
    user=auth(authorization)
    require_sector(user,'commercial','office')
    if not provider_config():raise HTTPException(503,'API oficial do WhatsApp não configurada')
    if not data.message.strip() or len(data.message)>4096:raise HTTPException(422,'Mensagem vazia ou acima de 4.096 caracteres')
    with db() as con:
        check_client_scope(con,user,data.clientId)
        row=con.execute("SELECT payload FROM entities WHERE kind='client' AND id=%s",(data.clientId,)).fetchone()
        if not row:raise HTTPException(404,'Cliente não encontrado')
        phone=re.sub(r'\D','',str(row[0].get('phone') or ''))
        if len(phone) in (10,11):phone='55'+phone
        if not re.fullmatch(r'55\d{10,11}',phone):raise HTTPException(422,'Cadastre um telefone brasileiro com DDD')
    try:
        mid=await asyncio.to_thread(send_text,phone,data.message)
    except MediaError as exc:
        raise HTTPException(502,str(exc)) from exc
    try:
        with db() as con:
            event_id=secrets.token_hex(16)
            payload={'id':event_id,'clientId':data.clientId,'type':'WhatsApp','text':data.message,'owner':user,'at':datetime.now(TZ).isoformat(),'messageId':mid,'deliveryStatus':'accepted'}
            con.execute("INSERT INTO entities(kind,id,payload) VALUES('interaction',%s,%s)",(event_id,Jsonb(payload)))
            project_attendance(con,'interaction',event_id,payload)
            con.execute("INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,'interaction',%s,'whatsapp_text_accepted')",(user,event_id))
    except Exception:
        logging.getLogger('uvicorn.error').error('whatsapp_text accepted history_failed')
        return {'status':'accepted','messageId':mid,'historyRecorded':False}
    return {'status':'accepted','messageId':mid,'historyRecorded':True}


@app.get('/api/records/events')
async def records_events(request: Request, authorization: str | None = Header(default=None)):
    # The channel carries only an invalidation signal, never customer data or secrets.
    await asyncio.to_thread(auth, authorization)
    async def messages():
        async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as con:
            await con.execute('LISTEN l2_records_changed')
            yield 'event: ready\ndata: {}\n\n'
            while not await request.is_disconnected():
                received=False
                async for _ in con.notifies(timeout=25, stop_after=1):
                    received=True
                try:
                    await asyncio.to_thread(auth, authorization)
                except HTTPException:
                    break
                yield 'event: records\ndata: {}\n\n' if received else ': heartbeat\n\n'
    return StreamingResponse(messages(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})


def notify_record_change():
    with db() as con:
        con.execute("SELECT pg_notify('l2_records_changed', '')")

@app.middleware('http')
async def record_change_notifications(request: Request, call_next):
    response=await call_next(request)
    # Draft saves are private, and cannot recursively invalidate business records.
    if request.method in ('POST','PUT','PATCH','DELETE') and response.status_code<400 and request.url.path.startswith('/api/') and request.url.path not in ('/api/sync','/api/login','/api/logout','/api/commercial/drafts'):
        try:
            await asyncio.to_thread(notify_record_change)
        except Exception:
            logging.warning('record_notification_unavailable')
    return response

@app.get('/api/inventory')
def get_inventory(authorization: str | None = Header(default=None)):
    user = auth(authorization)
    require_sector(user, 'commercial', 'office')
    with db() as con: return inventory_snapshot(con)

@app.post('/api/inventory/movements')
def add_inventory_movement(data: InventoryMovement, authorization: str | None = Header(default=None)):
    user = auth(authorization)
    require_sector(user, 'commercial', 'office')
    with db() as con:
        result = inventory_apply(con, data, user)
        con.execute("SELECT pg_notify('l2_records_changed','')")
        return result

def google_drive_snapshot(user):
    """Only export records currently visible to the authenticated manager."""
    if user != 'Ana Paula':
        raise HTTPException(403, 'Acesso restrito à gestora')
    permissions = sectors_for(user)
    with db() as con:
        con.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        active = con.execute('SELECT active,must_change_password FROM app_users WHERE username=%s', (user,)).fetchone()
        if not active or not active[0] or active[1]:
            raise HTTPException(403, 'Conta indisponível para envio ao Drive')
        result = {'schemaVersion': 1, 'owner': user, 'records': {}}
        groups = {
            'commercial': ['client','visit','order','task','goal','opportunity','interaction','lead','fulfillment','whatsapp_template'],
            'catalog': ['industry','price_table','price'],
            'routes': ['route'],
            'office': ['office_process','office_action','office_commercial','office_administrative','office_ritual','office_role'],
            'finance': ['settlement','office_finance','office_budget','office_monthly_close','cash_day','cash_entry','commission_rate','commission_receipt']
        }
        for sector,kinds in groups.items():
            allowed = sector in permissions or (sector in ('commercial','catalog') and 'commercial' in permissions)
            if sector == 'commercial':
                allowed = bool({'commercial','office','finance','management'} & permissions)
            if not allowed: continue
            for kind in kinds:
                rows = scoped_rows(con,kind,user)
                if kind == 'office_process' and 'finance' not in permissions:
                    rows = [r for r in rows if r.get('Área') != 'Financeiro']
                result['records'][kind] = rows
        if 'finance' not in permissions:
            result = hide_industry_commissions(result)
        result['drafts'] = [{'key':r[0],'payload':r[1],'updatedAt':r[2]} for r in con.execute('SELECT draft_key,payload,client_timestamp FROM commercial_drafts WHERE username=%s ORDER BY draft_key', (user,))]
        result['personalFinance'] = {}
        con.execute("SELECT set_config('l2.personal_actor',%s,true)", (user,))
        for table in ('categories','accounts','transactions'):
            result['personalFinance'][table] = [r[0] for r in con.execute('SELECT row_to_json(t) FROM personal_finance.'+table+' t WHERE household_id=%s ORDER BY id', ('ana-paula-euler',))]
        result['personalFinance']['attachments'] = [r[0] for r in con.execute("SELECT json_build_object('id',id,'transactionId',transaction_id,'name',filename,'contentType',content_type) FROM personal_finance.attachments WHERE household_id=%s ORDER BY id", ('ana-paula-euler',))]
        return result

drive_backup.configure(app, db, auth, google_drive_snapshot)


from bi_dashboard import install as install_bi_dashboard
install_bi_dashboard(app, db, auth, sectors_for, scoped_rows, is_seller)


def sync_cpq_order(order, authorization):
    return sync(Sync(changes=[Change(type='order', data=order, changeId='cpq-convert-'+order['cpqQuoteId'])]), authorization)

cpq.configure(db=db, auth=auth, require_sector=require_sector, check_client_scope=check_client_scope,
              scoped_rows=scoped_rows, is_seller=is_seller, normalize_uf=normalize_uf,
              price_table_matches_client=price_table_matches_client, valid_cnpj=valid_cnpj, sync_order=sync_cpq_order)
app.include_router(cpq.router)
